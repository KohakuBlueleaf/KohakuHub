<!--
  TarBrowserPanel.vue

  Self-contained read-only browser for hfutils.index TAR + JSON
  pairs. Renders without any dialog/modal wrapper so it can be
  mounted inline on the standalone blob page or wrapped inside an
  <el-dialog> by TarBrowserDialog.

  Lifecycle: loads the sidecar JSON on mount (and on indexUrl
  change), exposes the listing + member view through internal state,
  and uses FilePreviewDialog as a sub-dialog for in-archive
  safetensors / parquet metadata previews.

  No backend code path: index JSON and member ranges are both
  fetched via /resolve/.
-->

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import {
  parseTarIndex,
  buildTreeFromIndex,
  listDirectory,
  extractMemberBytes,
  compareTarHash,
  classifyMember,
  guessMimeType,
  downloadBytesAs,
} from "@/utils/indexed-tar";
import { classifyError } from "@/utils/http-errors";
import { useThumbnailToggle, isImageMember } from "@/utils/tar-thumbnail";
import {
  readViewMode,
  writeViewMode,
  readPageSize,
  writePageSize,
} from "@/utils/tar-listing-prefs";
import ErrorState from "@/components/common/ErrorState.vue";
import CodeViewer from "@/components/common/CodeViewer.vue";
import MarkdownViewer from "@/components/common/MarkdownViewer.vue";
import FilePreviewDialog from "@/components/repo/preview/FilePreviewDialog.vue";
import TarMemberThumbnail from "@/components/repo/preview/TarMemberThumbnail.vue";
import { ElMessage } from "element-plus";

const props = defineProps({
  tarUrl: { type: String, required: true },
  indexUrl: { type: String, required: true },
  filename: { type: String, required: true },
  // Tree-API entry for the .tar file. Used to compare its on-disk
  // hash against the hashes inside the sidecar index — drives the
  // top-of-panel warning / notice banner.
  tarTreeEntry: { type: Object, default: null },
});

const state = ref("idle");
const phase = ref("");
const indexPayload = ref(null);
const tree = ref(null);
const errorClassification = ref(null);
let currentController = null;
let currentRequestId = 0;

// Browser navigation state.
const pathStack = ref([]); // segments inside the tar, e.g. ['sub', 'nested']
const searchQuery = ref("");
// View mode + page size are user-level preferences persisted in
// localStorage. Defaults: grid layout at 20 entries / page (per
// the user's request — bigger thumbs, denser browsing). Watching
// each ref writes the change back without a separate save action.
const viewMode = ref(readViewMode());
const pageSize = ref(readPageSize());
watch(viewMode, (next) => writeViewMode(next));
watch(pageSize, (next) => writePageSize(next));
const currentPage = ref(1);

// Member preview state.
const memberView = ref(null);
let memberObjectUrl = null;
let memberAbortController = null;
const INLINE_TEXT_MAX_BYTES = 256 * 1024;
const INLINE_BLOB_MAX_BYTES = 200 * 1024 * 1024;

const innerPreviewProps = ref(null);

// Load on mount and re-load if the source URL changes. The blob-page
// surface keeps this component mounted while the user navigates
// inside the archive, so a fresh indexUrl (e.g. switching to a
// different .tar in the same repo) needs to trigger a reload.
onMounted(() => {
  if (props.indexUrl) startLoad();
});
watch(
  () => props.indexUrl,
  (newUrl, oldUrl) => {
    if (newUrl && newUrl !== oldUrl) startLoad();
    if (!newUrl) {
      cancelInFlight();
      resetMember();
    }
  },
);

function cancelInFlight() {
  if (currentController) {
    currentController.abort();
    currentController = null;
  }
}

function resetMember() {
  if (memberAbortController) {
    memberAbortController.abort();
    memberAbortController = null;
  }
  if (memberObjectUrl) {
    // 60 s grace so an in-flight <video> / <img> request can finish
    // reading the object URL before it is revoked.
    const stale = memberObjectUrl;
    setTimeout(() => URL.revokeObjectURL(stale), 60_000);
    memberObjectUrl = null;
  }
  memberView.value = null;
  innerPreviewProps.value = null;
}

async function startLoad() {
  cancelInFlight();
  resetMember();
  pathStack.value = [];
  searchQuery.value = "";
  currentPage.value = 1;

  const requestId = ++currentRequestId;
  state.value = "loading";
  phase.value = "Fetching tar index sidecar…";
  indexPayload.value = null;
  tree.value = null;
  errorClassification.value = null;

  const controller = new AbortController();
  currentController = controller;

  try {
    const onProgress = (p) => {
      if (p === "fetch") phase.value = "Fetching tar index sidecar…";
      else if (p === "parsing") phase.value = "Parsing index JSON…";
      else if (p === "done") phase.value = "Building directory tree…";
    };
    const payload = await parseTarIndex(props.indexUrl, {
      signal: controller.signal,
      onProgress,
    });
    if (requestId !== currentRequestId) return;
    indexPayload.value = payload;
    tree.value = buildTreeFromIndex(payload.files);
    state.value = "ready";
  } catch (err) {
    if (requestId !== currentRequestId) return;
    if (err?.name === "AbortError") return;
    errorClassification.value = classifyError(err);
    state.value = "error";
  } finally {
    if (requestId === currentRequestId) currentController = null;
  }
}

function retry() {
  startLoad();
}

const hashCheck = computed(() => {
  if (!indexPayload.value) return null;
  return compareTarHash(indexPayload.value, props.tarTreeEntry || null);
});

const directoryListing = computed(() => {
  if (!tree.value) return { folders: [], files: [], node: null };
  return listDirectory(tree.value, pathStack.value);
});

const filteredEntries = computed(() => {
  const { folders, files } = directoryListing.value;
  const query = searchQuery.value.trim().toLowerCase();
  const filteredFolders = query
    ? folders.filter((f) => f.name.toLowerCase().includes(query))
    : folders;
  const filteredFiles = query
    ? files.filter((f) => f.name.toLowerCase().includes(query))
    : files;
  return [...filteredFolders, ...filteredFiles];
});

const totalEntries = computed(() => filteredEntries.value.length);

const pageStartIdx = computed(
  () => (currentPage.value - 1) * pageSize.value,
);
const pageEndIdx = computed(() =>
  Math.min(pageStartIdx.value + pageSize.value, totalEntries.value),
);
const pagedEntries = computed(() =>
  filteredEntries.value.slice(pageStartIdx.value, pageEndIdx.value),
);

watch(totalEntries, () => {
  // Reset to page 1 if the current page falls outside the new bounds
  // (e.g. user typed a search that narrows results below the current
  // page's offset).
  const maxPage = Math.max(1, Math.ceil(totalEntries.value / pageSize.value));
  if (currentPage.value > maxPage) currentPage.value = maxPage;
});

watch(pathStack, () => {
  currentPage.value = 1;
  searchQuery.value = "";
});

function enterFolder(node) {
  pathStack.value = [...pathStack.value, node.name];
}

function jumpToCrumb(idx) {
  // idx is the breadcrumb index; -1 means root.
  pathStack.value = pathStack.value.slice(0, idx + 1);
}

function goUp() {
  if (pathStack.value.length === 0) return;
  pathStack.value = pathStack.value.slice(0, -1);
}

function formatBytes(value) {
  if (value == null) return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  let scaled = n;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return unit === 0
    ? `${scaled.toFixed(0)} ${units[unit]}`
    : `${scaled.toFixed(2)} ${units[unit]}`;
}

function iconForFile(name) {
  // Mirror the icon family the standalone blob page uses so visual
  // identity stays consistent across surfaces. Source of truth:
  // pages/.../blob/.../[...file].vue — getFileIcon().
  const ext = name.split(".").pop()?.toLowerCase();
  const cls = classifyMember(name);
  if (cls === "image") return "i-carbon-image text-purple-500";
  if (cls === "video") return "i-carbon-video text-red-500";
  if (cls === "audio") return "i-carbon-music text-green-500";
  if (cls === "pdf") return "i-carbon-document-pdf text-red-600";
  // Markdown intentionally does NOT get a dedicated icon — the
  // user asked for it to share the generic document fallback so
  // README rows look the same as plain text rows. The earlier
  // draft used an icon name that does not ship in Carbon, which
  // collapsed the icon container to zero width and pushed the
  // row text against the left edge.
  if (cls === "safetensors") return "i-carbon-data-vis-1 text-blue-500";
  if (cls === "parquet") return "i-carbon-data-table text-orange-500";
  if (["js", "ts", "jsx", "tsx"].includes(ext))
    return "i-carbon-code text-yellow-500";
  if (ext === "py") return "i-carbon-code text-blue-600";
  if (["json", "xml", "yaml", "yml"].includes(ext))
    return "i-carbon-data-structured text-orange-500";
  return "i-carbon-document text-gray-500";
}

function breadcrumbItems() {
  return [{ name: props.filename, idx: -1 }].concat(
    pathStack.value.map((seg, idx) => ({ name: seg, idx })),
  );
}

async function openMember(node) {
  resetMember();
  const cls = classifyMember(node.name);
  const mime = guessMimeType(node.name);
  const controller = new AbortController();
  memberAbortController = controller;
  memberView.value = {
    path: node.path,
    name: node.name,
    size: node.size,
    // offset is required by extractMemberBytes for the Download
    // button. Forgetting it caused MinIO to ignore a malformed Range
    // header and return the entire tar — the resulting "got X, want
    // Y" mismatch was confusing because the bug was an undefined
    // field in the UI wrapper, not a network problem.
    offset: node.offset,
    sha256: node.sha256,
    cls,
    state: "loading",
    text: null,
    blobUrl: null,
    bytes: null,
  };

  // Empty members: nothing to render but still allow "download" of an
  // empty file so the action is not silently denied.
  if (node.size === 0) {
    memberView.value.state = "ready";
    return;
  }

  if (cls === "text" || cls === "markdown") {
    if (node.size > INLINE_TEXT_MAX_BYTES) {
      memberView.value.state = "too-large-text";
      return;
    }
  } else if (cls !== "binary" && node.size > INLINE_BLOB_MAX_BYTES) {
    memberView.value.state = "too-large-blob";
    return;
  } else if (cls === "binary") {
    memberView.value.state = "binary";
    return;
  }

  try {
    const bytes = await extractMemberBytes(
      props.tarUrl,
      { offset: node.offset, size: node.size },
      { signal: controller.signal },
    );
    if (memberAbortController !== controller) return; // superseded

    // Cache the in-memory bytes on the memberView so the Download
    // button can reuse them instead of re-issuing the Range read.
    // Saves a round-trip and keeps the saved file byte-identical to
    // what the modal previewed.
    memberView.value.bytes = bytes;

    if (cls === "text" || cls === "markdown") {
      const decoder = new TextDecoder("utf-8", { fatal: false });
      memberView.value.text = decoder.decode(bytes);
      memberView.value.state = "ready";
    } else {
      const blob = new Blob([bytes], { type: mime });
      memberObjectUrl = URL.createObjectURL(blob);
      memberView.value.blobUrl = memberObjectUrl;
      if (cls === "safetensors" || cls === "parquet") {
        // Hand the FilePreviewDialog the already-extracted bytes
        // directly. A blob URL would not work — hyparquet's
        // asyncBufferFromUrl issues HEAD + Range requests against
        // the source URL, and HEAD on `blob:` URLs is rejected
        // (treated as a CORS-style failure by classifyError).
        // Parsing the in-memory ArrayBuffer skips that dependency.
        innerPreviewProps.value = {
          kind: cls,
          bytes,
          filename: node.name,
        };
      }
      memberView.value.state = "ready";
    }
  } catch (err) {
    if (err?.name === "AbortError") return;
    memberView.value.state = "error";
    memberView.value.error = classifyError(err);
  }
}

async function downloadMember(node) {
  try {
    // Reuse the in-memory bytes if openMember already extracted them
    // (the typical path — ready/loaded state). Skipping a second
    // Range read makes the saved file byte-identical to what the
    // user just previewed and avoids re-paying the round-trip.
    let bytes = node && node.bytes ? node.bytes : null;
    if (!bytes) {
      bytes = await extractMemberBytes(props.tarUrl, {
        offset: node.offset,
        size: node.size,
      });
    }
    downloadBytesAs(bytes, node.name, guessMimeType(node.name));
  } catch (err) {
    ElMessage.error(`Download failed: ${err.message || err}`);
  }
}

function backToListing() {
  resetMember();
}

function fileExtension(name) {
  const m = name.match(/\.([^.]+)$/);
  return m ? m[1].toLowerCase() : "";
}

// Image-thumbnail toggle. Persisted globally in localStorage so the
// user's choice survives modal re-opens and even tab navigation.
// Default ON; flipping OFF skips ALL extraction work for image rows
// (no Range read, no IO subscription, no cache lookup) — just the
// generic placeholder icon, same as before this feature landed.
const { enabled: thumbnailsEnabled, setEnabled: setThumbnailsEnabled } =
  useThumbnailToggle();

function shouldRenderThumbnail(entry) {
  return (
    thumbnailsEnabled.value &&
    entry.type !== "dir" &&
    isImageMember(entry)
  );
}

// Inner FilePreviewDialog stays closed until the user explicitly
// clicks "Open metadata preview". Auto-opening it on prop change
// would race with the member view itself, leave the inner overlay
// stacked over the button, and intercept subsequent clicks (the
// reproduced symptom: every click on "Open metadata preview" hit
// the overlay instead of the button).
const innerPreviewVisible = ref(false);
watch(innerPreviewProps, (val) => {
  if (!val) innerPreviewVisible.value = false;
});
</script>

<template>
  <div class="tar-browser-panel">
    <div v-if="state === 'loading'" class="py-10 flex flex-col items-center">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
      <p class="mt-4 text-sm text-gray-600 dark:text-gray-300">
        {{ phase }}
      </p>
      <p
        class="mt-1 text-xs text-gray-400 dark:text-gray-500 max-w-md text-center"
      >
        Reading the index sidecar (.json next to the .tar). The .tar
        itself is only Range-read for individual member previews.
      </p>
    </div>

    <ErrorState
      v-else-if="state === 'error' && errorClassification"
      :classification="errorClassification"
      mode="inline-panel"
      :retry="retry"
    />

    <div v-else-if="state === 'ready' && tree">
      <!-- Hash banner. The index always carries hash_lfs (sha256 of
           the tar bytes) when produced by hfutils.index — see
           hfutils/index/make.py:tar_get_index_info. We compare it
           against the tree-API oid for the .tar, which the backend
           also stores as sha256. A mismatch usually means the .tar
           was rewritten without regenerating the .json sidecar. -->
      <div v-if="hashCheck && hashCheck.kind === 'mismatch'" class="mb-4">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="Tar hash does not match the sidecar index"
        >
          <template #default>
            <div class="text-xs">
              The .tar file's sha256
              <code>{{ hashCheck.actual.slice(0, 16) }}…</code>
              does not match the
              <code>hash_lfs</code> recorded in the .json sidecar
              (<code>{{ hashCheck.expected.slice(0, 16) }}…</code>). The
              archive may have been re-uploaded without regenerating the
              index — member offsets may be incorrect or outdated.
            </div>
          </template>
        </el-alert>
      </div>
      <div
        v-else-if="hashCheck && hashCheck.kind === 'unknown'"
        class="mb-4"
      >
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="No hash recorded in the index"
        >
          <template #default>
            <div class="text-xs">
              The .json sidecar does not carry a tar hash, so consistency
              with the actual .tar cannot be verified. Listings and
              previews are still served, but a stale or rewritten archive
              would not be detected here.
            </div>
          </template>
        </el-alert>
      </div>
      <div
        v-else-if="hashCheck && hashCheck.kind === 'partial'"
        class="mb-4"
      >
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="Tar hash check skipped"
        >
          <template #default>
            <div class="text-xs">
              Could not verify the tar against the sidecar index — one
              side does not advertise a hash in a comparable shape.
            </div>
          </template>
        </el-alert>
      </div>

      <!-- Member view. Replaces the listing pane while a member is
           open so the modal stays a single navigable surface. -->
      <div v-if="memberView">
        <div class="flex flex-wrap items-center gap-2 mb-3">
          <el-button size="small" @click="backToListing">
            <div class="i-carbon-arrow-left inline-block mr-1" />
            Back
          </el-button>
          <span class="font-medium truncate">{{ memberView.name }}</span>
          <span class="text-xs text-gray-500 dark:text-gray-400">
            ({{ formatBytes(memberView.size) }})
          </span>
          <span class="flex-1" />
          <el-button
            size="small"
            type="primary"
            @click="downloadMember(memberView)"
          >
            <div class="i-carbon-download inline-block mr-1" />
            Download
          </el-button>
        </div>

        <div
          v-if="memberView.state === 'loading'"
          class="py-10 flex flex-col items-center"
        >
          <el-icon class="is-loading" :size="32">
            <div class="i-carbon-loading" />
          </el-icon>
          <p class="mt-3 text-xs text-gray-500 dark:text-gray-400">
            Range-reading {{ formatBytes(memberView.size) }} from the
            archive…
          </p>
        </div>

        <ErrorState
          v-else-if="memberView.state === 'error'"
          :classification="memberView.error"
          mode="inline-panel"
        />

        <div v-else-if="memberView.state === 'too-large-text'" class="text-center py-10">
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-3">
            Text member is {{ formatBytes(memberView.size) }} — too
            large to inline-preview ({{ formatBytes(INLINE_TEXT_MAX_BYTES) }} limit).
          </p>
          <el-button type="primary" @click="downloadMember(memberView)">
            <div class="i-carbon-download inline-block mr-1" />
            Download to inspect
          </el-button>
        </div>

        <div v-else-if="memberView.state === 'too-large-blob'" class="text-center py-10">
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-3">
            Member is {{ formatBytes(memberView.size) }} — above the
            inline-preview cap. Use the download button to save it
            locally.
          </p>
        </div>

        <div v-else-if="memberView.state === 'binary'" class="text-center py-10">
          <div
            class="i-carbon-document text-7xl text-gray-300 dark:text-gray-600 mb-2 inline-block"
          />
          <p class="text-sm text-gray-600 dark:text-gray-400">
            Binary member — preview not available. Use the download
            button above to save it locally.
          </p>
        </div>

        <div v-else-if="memberView.state === 'ready' && memberView.cls === 'image'" class="text-center">
          <img
            :src="memberView.blobUrl"
            :alt="memberView.name"
            class="max-w-full h-auto mx-auto"
            style="max-height: 600px"
          />
        </div>

        <div v-else-if="memberView.state === 'ready' && memberView.cls === 'video'" class="text-center">
          <video
            :src="memberView.blobUrl"
            controls
            class="max-w-full h-auto mx-auto"
            style="max-height: 500px"
            :aria-label="`Video: ${memberView.name}`"
          >
            <track kind="captions" :label="`${memberView.name} captions`" />
          </video>
        </div>

        <div v-else-if="memberView.state === 'ready' && memberView.cls === 'audio'" class="text-center py-6">
          <div
            class="i-carbon-music text-6xl text-gray-400 mb-3 inline-block"
          />
          <audio
            :src="memberView.blobUrl"
            controls
            class="w-full max-w-xl mx-auto"
          />
        </div>

        <div v-else-if="memberView.state === 'ready' && memberView.cls === 'pdf'" class="h-[600px]">
          <iframe
            :src="memberView.blobUrl"
            class="w-full h-full border-0"
            title="PDF Preview"
          />
        </div>

        <div v-else-if="memberView.state === 'ready' && memberView.cls === 'markdown'">
          <MarkdownViewer :content="memberView.text || ''" />
        </div>

        <div v-else-if="memberView.state === 'ready' && memberView.cls === 'text'">
          <CodeViewer
            :code="memberView.text || ''"
            :language="fileExtension(memberView.name)"
          />
        </div>

        <div
          v-else-if="memberView.state === 'ready' && (memberView.cls === 'safetensors' || memberView.cls === 'parquet')"
          class="text-center py-10"
        >
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-3">
            {{ memberView.cls === "safetensors" ? "Safetensors" : "Parquet" }}
            metadata preview is available for this archive member.
          </p>
          <el-button type="primary" @click="innerPreviewVisible = true">
            <div class="i-carbon-chart-line-data inline-block mr-1" />
            Open metadata preview
          </el-button>
        </div>
      </div>

      <!-- Listing view. -->
      <div v-else>
        <!-- In-tar breadcrumb. Clickable, last segment is the current
             folder. The first crumb is the .tar filename itself so
             the user can jump back to root in one click. -->
        <div class="flex items-center gap-2 flex-wrap mb-3 text-sm">
          <template v-for="(crumb, idx) in breadcrumbItems()" :key="idx">
            <el-button
              :type="idx === breadcrumbItems().length - 1 ? '' : 'primary'"
              :link="idx !== breadcrumbItems().length - 1"
              size="small"
              :disabled="idx === breadcrumbItems().length - 1"
              @click="jumpToCrumb(crumb.idx)"
            >
              <div
                v-if="idx === 0"
                class="i-carbon-archive inline-block mr-1"
              />
              <div v-else class="i-carbon-folder inline-block mr-1" />
              {{ crumb.name }}
            </el-button>
            <span
              v-if="idx < breadcrumbItems().length - 1"
              class="text-gray-400 dark:text-gray-500"
            >/</span>
          </template>
        </div>

        <!-- Toolbar. Search, view toggle, page size. -->
        <div class="flex flex-wrap items-center gap-2 mb-3">
          <el-input
            v-model="searchQuery"
            size="small"
            placeholder="Filter in this folder…"
            clearable
            class="!w-60"
          >
            <template #prefix>
              <div class="i-carbon-search" />
            </template>
          </el-input>
          <el-button
            v-if="pathStack.length > 0"
            size="small"
            @click="goUp"
          >
            <div class="i-carbon-arrow-up inline-block mr-1" />
            Up
          </el-button>
          <span class="flex-1" />
          <el-tooltip
            content="Toggle in-listing thumbnails (saved across sessions)"
            placement="top"
          >
            <el-switch
              :model-value="thumbnailsEnabled"
              @update:model-value="setThumbnailsEnabled"
              size="small"
              inline-prompt
              active-text="thumbs"
              inactive-text="thumbs"
              data-testid="tar-thumbnail-toggle"
            />
          </el-tooltip>
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button label="list">
              <div class="i-carbon-list inline-block" />
            </el-radio-button>
            <el-radio-button label="grid">
              <div class="i-carbon-grid inline-block" />
            </el-radio-button>
          </el-radio-group>
          <el-select
            v-model="pageSize"
            size="small"
            class="!w-28"
          >
            <el-option :value="20" label="20 / page" />
            <el-option :value="50" label="50 / page" />
            <el-option :value="100" label="100 / page" />
          </el-select>
        </div>

        <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">
          {{ totalEntries }} entries · {{ tree.fileCount }} files in archive ·
          {{ formatBytes(tree.size) }} total
        </p>

        <!-- List view. -->
        <div
          v-if="viewMode === 'list'"
          class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden"
        >
          <div
            v-if="pagedEntries.length === 0"
            class="text-center text-sm text-gray-500 dark:text-gray-400 py-8"
          >
            No matching entries.
          </div>
          <div
            v-for="entry in pagedEntries"
            :key="entry.path || entry.name"
            class="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 border-b border-gray-100 dark:border-gray-800 last:border-b-0"
            @click="entry.type === 'dir' ? enterFolder(entry) : openMember(entry)"
          >
            <TarMemberThumbnail
              v-if="shouldRenderThumbnail(entry)"
              :tar-url="props.tarUrl"
              :member="entry"
              :placeholder-icon="iconForFile(entry.name)"
              :size="48"
            />
            <div
              v-else
              :class="
                entry.type === 'dir'
                  ? 'i-carbon-folder text-blue-500'
                  : iconForFile(entry.name)
              "
              class="text-xl flex-shrink-0"
            />
            <div class="min-w-0 flex-1">
              <div class="font-medium truncate">{{ entry.name }}</div>
              <div
                class="text-xs text-gray-500 dark:text-gray-400"
                v-if="entry.type === 'dir'"
              >
                {{ entry.fileCount }} files · {{ formatBytes(entry.size) }}
              </div>
              <div
                class="text-xs text-gray-500 dark:text-gray-400"
                v-else
              >
                {{ formatBytes(entry.size) }}
              </div>
            </div>
            <div
              class="i-carbon-chevron-right text-gray-400 flex-shrink-0"
            />
          </div>
        </div>

        <!-- Grid view. -->
        <div
          v-else
          class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3"
        >
          <div
            v-if="pagedEntries.length === 0"
            class="col-span-full text-center text-sm text-gray-500 dark:text-gray-400 py-8"
          >
            No matching entries.
          </div>
          <div
            v-for="entry in pagedEntries"
            :key="entry.path || entry.name"
            class="border border-gray-200 dark:border-gray-700 rounded p-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 flex flex-col items-center text-center"
            @click="entry.type === 'dir' ? enterFolder(entry) : openMember(entry)"
          >
            <TarMemberThumbnail
              v-if="shouldRenderThumbnail(entry)"
              :tar-url="props.tarUrl"
              :member="entry"
              :placeholder-icon="iconForFile(entry.name)"
              class="mb-2"
            />
            <div
              v-else
              :class="
                entry.type === 'dir'
                  ? 'i-carbon-folder text-blue-500'
                  : iconForFile(entry.name)
              "
              class="text-3xl mb-2"
            />
            <div class="text-xs font-medium truncate w-full" :title="entry.name">
              {{ entry.name }}
            </div>
            <div
              class="text-[10px] text-gray-500 dark:text-gray-400 mt-1"
              v-if="entry.type === 'dir'"
            >
              {{ entry.fileCount }} · {{ formatBytes(entry.size) }}
            </div>
            <div
              v-else
              class="text-[10px] text-gray-500 dark:text-gray-400 mt-1"
            >
              {{ formatBytes(entry.size) }}
            </div>
          </div>
        </div>

        <!-- Pagination. -->
        <div
          v-if="totalEntries > pageSize"
          class="mt-4 flex justify-end"
        >
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="totalEntries"
            layout="prev, pager, next, jumper"
            small
            background
          />
        </div>
      </div>
    </div>

  </div>

  <!-- Sub-dialog used to render safetensors / parquet metadata for a
       member that has already been extracted into memory. We hand
       the FilePreviewDialog the raw bytes via its `bytes` prop;
       the URL-based parsers cannot be reused here because hyparquet
       issues HEAD + Range against the source URL and `blob:` URLs
       reject those reliably enough that the user saw the failure as
       a CORS-shaped error. -->
  <FilePreviewDialog
    v-if="innerPreviewProps && innerPreviewVisible"
    v-model:visible="innerPreviewVisible"
    :kind="innerPreviewProps.kind"
    :bytes="innerPreviewProps.bytes"
    :filename="innerPreviewProps.filename"
  />
</template>
