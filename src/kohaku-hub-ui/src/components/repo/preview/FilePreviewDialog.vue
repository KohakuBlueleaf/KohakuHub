<!--
  FilePreviewDialog.vue

  Pure-client metadata preview for .safetensors and .parquet files.
  Reads the file header/footer over HTTP Range against /resolve/ (which
  302s to a presigned S3/MinIO URL). No backend code path: the SPA hits
  object storage directly, relying on MinIO CORS being wired (see
  docs/development/local-dev.md § "MinIO CORS").

  The modal is deliberately cold-start-tolerant: the user sees a spinner
  plus a human-readable phase ("fetching header range (100 KB)…") so a
  1–2 s cold path never looks like the UI froze. Abort on close cancels
  in-flight fetches.

  Implements surface A from issue #27 v4 — file-level preview only, no
  repo-aggregate badges.
-->

<script setup>
import { computed, ref, watch } from "vue";
import {
  buildTensorTree,
  formatHumanReadable,
  parseSafetensorsMetadata,
  summarizeSafetensors,
  SafetensorsFetchError,
} from "@/utils/safetensors";
import {
  parseParquetMetadata,
  summarizeParquetSchema,
} from "@/utils/parquet";
import { classifyError, ERROR_KIND } from "@/utils/http-errors";
import ErrorState from "@/components/common/ErrorState.vue";

const props = defineProps({
  visible: { type: Boolean, required: true },
  kind: { type: String, required: true }, // "safetensors" | "parquet"
  resolveUrl: { type: String, required: true },
  filename: { type: String, required: true },
});
const emit = defineEmits(["update:visible"]);

const state = ref("idle"); // idle | loading | ready | error
const phase = ref(""); // human-readable current phase
const payload = ref(null);
// Classification output from utils/http-errors.js — shared with the
// blob / edit pages and RepoViewer via the same `<ErrorState>`
// component, so "authentication required" copy stays identical
// across every surface where a fallback-sourced resource fails.
const errorClassification = ref(null);
let currentController = null;
let currentRequestId = 0;

const dialogVisible = computed({
  get: () => props.visible,
  set: (value) => emit("update:visible", value),
});

const title = computed(() => {
  if (props.kind === "safetensors") {
    return `Safetensors metadata · ${props.filename}`;
  }
  if (props.kind === "parquet") {
    return `Parquet metadata · ${props.filename}`;
  }
  return `Metadata · ${props.filename}`;
});

watch(
  () => [props.visible, props.resolveUrl, props.kind],
  ([visible]) => {
    if (visible) {
      startLoad();
    } else {
      cancelInFlight();
    }
  },
  { immediate: true },
);

function cancelInFlight() {
  if (currentController) {
    currentController.abort();
    currentController = null;
  }
}

async function startLoad() {
  cancelInFlight();
  const requestId = ++currentRequestId;

  state.value = "loading";
  phase.value = describePhase(props.kind, "init");
  payload.value = null;
  errorClassification.value = null;

  const controller = new AbortController();
  currentController = controller;

  try {
    const onProgress = (currentPhase) => {
      phase.value = describePhase(props.kind, currentPhase);
    };
    let result;
    if (props.kind === "safetensors") {
      const header = await parseSafetensorsMetadata(props.resolveUrl, {
        signal: controller.signal,
        onProgress,
      });
      result = {
        kind: "safetensors",
        header,
        summary: summarizeSafetensors(header),
      };
    } else if (props.kind === "parquet") {
      const metadata = await parseParquetMetadata(props.resolveUrl, {
        signal: controller.signal,
        onProgress,
      });
      result = {
        kind: "parquet",
        metadata,
        summary: summarizeParquetSchema(metadata),
      };
    } else {
      throw new Error(`Unsupported preview kind: ${props.kind}`);
    }
    if (requestId !== currentRequestId) return; // superseded
    payload.value = result;
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

function describePhase(kind, phaseName) {
  if (kind === "safetensors") {
    if (phaseName === "init") return "Preparing Range request…";
    if (phaseName === "range-head") return "Fetching header Range (100 KB)…";
    if (phaseName === "range-full") return "Header is large — fetching full header bytes…";
    if (phaseName === "parsing") return "Parsing header JSON…";
    if (phaseName === "done") return "Done.";
  }
  if (kind === "parquet") {
    if (phaseName === "init") return "Preparing Range request…";
    if (phaseName === "head") return "Probing file size (HEAD)…";
    if (phaseName === "footer") return "Fetching parquet footer (512 KB tail)…";
    if (phaseName === "parsing") return "Decoding parquet metadata…";
    if (phaseName === "done") return "Done.";
  }
  return phaseName;
}

function formatNumber(value) {
  if (value == null) return "-";
  if (typeof value === "string") return value;
  return value.toLocaleString();
}

function formatBytes(value) {
  if (value == null) return "-";
  const bytes = typeof value === "bigint" ? Number(value) : value;
  if (!Number.isFinite(bytes)) return String(value);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  let scaled = bytes;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : 2;
  return `${scaled.toFixed(digits)} ${units[unit]}`;
}

function formatShape(shape) {
  if (!Array.isArray(shape) || shape.length === 0) return "[]";
  return `[${shape.join(", ")}]`;
}

function formatBytesExact(value) {
  if (value == null) return "0";
  const n = typeof value === "bigint" ? Number(value) : value;
  if (!Number.isFinite(n)) return String(value);
  return Math.trunc(n).toLocaleString();
}

// Continuous-gradient fill for the %-of-parent bar. slate-500 RGB,
// alpha ramps linearly from 0.08 at 0% to 0.72 at 100% so low-mass
// tensors stay visible without drowning out the high-mass ones.
function percentBarStyle(pct) {
  const clamped = Math.min(100, Math.max(0, Number(pct) || 0));
  const alpha = (0.08 + (clamped / 100) * 0.64).toFixed(3);
  return {
    width: `${clamped}%`,
    backgroundColor: `rgba(100, 116, 139, ${alpha})`,
  };
}

// Tree of tensor rows keyed by dotted-path hierarchy. Element Plus's
// tree table consumes `row-key` + `tree-props.children`; build every
// node up front and hand it the full nested structure instead of
// flipping between flat + tree datasets.
const safetensorsTreeRows = computed(() => {
  if (payload.value?.kind !== "safetensors") return [];
  return buildTensorTree(
    payload.value.header.tensors,
    payload.value.summary.total,
  );
});

// Toggle between human-readable ("1.23B") and exact ("1,234,567,890")
// rendering of the "Total parameters" pill. Human is the default
// because the compact form is what people say out loud when they
// describe a model ("a 7B model"); the exact form stays one click away
// for anyone verifying a precise count.
const totalParamsFormat = ref("human"); // "human" | "exact"
function toggleTotalParamsFormat() {
  totalParamsFormat.value =
    totalParamsFormat.value === "human" ? "exact" : "human";
}
const totalParamsDisplay = computed(() => {
  if (payload.value?.kind !== "safetensors") return "-";
  const total = payload.value.summary.total;
  return totalParamsFormat.value === "human"
    ? formatHumanReadable(total)
    : formatNumber(total);
});

const parquetColumnRows = computed(() => {
  if (payload.value?.kind !== "parquet") return [];
  return payload.value.summary.columns.map((col) => ({
    name: col.name,
    physicalType: col.physicalType ?? "",
    logicalType: col.logicalType ?? "",
    repetition: col.repetitionType ?? "",
  }));
});

// Customize the title / hint copy for the preview-specific "file
// header fetch" context. The default `ErrorState` copy talks about
// the whole repo, but inside the preview dialog we specifically
// failed to read ONE file's metadata — the shared hint for "gated"
// still applies verbatim, others benefit from a preview-scoped nudge.
const previewTitle = computed(() => {
  if (!errorClassification.value) return null;
  switch (errorClassification.value.kind) {
    case ERROR_KIND.NOT_FOUND:
      return "File header not found on any source";
    case ERROR_KIND.UPSTREAM_UNAVAILABLE:
      return "Upstream source unavailable";
    default:
      return null; // fall back to ErrorState's default
  }
});
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    :title="title"
    width="760px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div v-if="state === 'loading'" class="py-10 flex flex-col items-center">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
      <p class="mt-4 text-sm text-gray-600 dark:text-gray-300">
        {{ phase }}
      </p>
      <p class="mt-1 text-xs text-gray-400 dark:text-gray-500 max-w-md text-center">
        Reading only the file header (typically &lt; 100 KB). The file itself is not downloaded.
      </p>
    </div>

    <ErrorState
      v-else-if="state === 'error' && errorClassification"
      :classification="errorClassification"
      mode="inline-panel"
      :retry="retry"
      :title-override="previewTitle"
    />

    <div v-else-if="state === 'ready' && payload?.kind === 'safetensors'">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">Tensors</div>
          <div class="text-lg font-semibold mt-1">
            {{ Object.keys(payload.header.tensors).length }}
          </div>
        </div>
        <div
          class="p-3 bg-gray-50 dark:bg-gray-800 rounded cursor-pointer select-none hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          :title="
            totalParamsFormat === 'human'
              ? 'Click to show the exact parameter count'
              : 'Click to show a compact (K / M / B / T) summary'
          "
          @click="toggleTotalParamsFormat"
        >
          <div class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
            <div
              class="i-carbon-arrows-horizontal text-[10px] opacity-60 flex-shrink-0"
            />
            <span>Total parameters</span>
          </div>
          <div class="text-lg font-semibold mt-1">
            {{ totalParamsDisplay }}
          </div>
        </div>
        <div
          class="p-3 bg-gray-50 dark:bg-gray-800 rounded"
          :title="`${formatBytesExact(payload.summary.byte_size)} bytes`"
        >
          <div class="text-xs text-gray-500 dark:text-gray-400">
            Tensor bytes
          </div>
          <div class="text-lg font-semibold mt-1">
            {{ formatBytes(payload.summary.byte_size) }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">Dtypes</div>
          <div class="text-sm font-medium mt-1 break-words">
            {{ Object.keys(payload.summary.parameters).join(", ") || "-" }}
          </div>
        </div>
      </div>

      <div class="mb-4">
        <h4 class="text-sm font-semibold mb-2">Parameters by dtype</h4>
        <el-table
          :data="Object.entries(payload.summary.parameters).map(([dtype, count]) => ({ dtype, count }))"
          size="small"
          :border="true"
        >
          <el-table-column prop="dtype" label="dtype" width="140" />
          <el-table-column label="Parameters">
            <template #default="{ row }">
              {{ formatNumber(row.count) }}
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="payload.header.metadata" class="mb-4">
        <h4 class="text-sm font-semibold mb-2">__metadata__</h4>
        <el-table
          :data="Object.entries(payload.header.metadata).map(([key, value]) => ({ key, value }))"
          size="small"
          :border="true"
        >
          <el-table-column prop="key" label="Key" width="180" />
          <el-table-column prop="value" label="Value" />
        </el-table>
      </div>

      <div>
        <h4 class="text-sm font-semibold mb-2">Tensors</h4>
        <el-table
          :data="safetensorsTreeRows"
          row-key="path"
          :tree-props="{ children: 'children' }"
          :default-expand-all="false"
          size="small"
          :border="true"
          max-height="360"
        >
          <el-table-column label="Name" min-width="280">
            <template #default="{ row }">
              <span :class="row.isLeaf ? '' : 'font-semibold'">
                {{ row.segment }}
              </span>
              <span
                v-if="!row.isLeaf"
                class="ml-2 text-xs text-gray-400 dark:text-gray-500"
              >
                ({{ row.leafCount }}
                {{ row.leafCount === 1 ? "tensor" : "tensors" }})
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="dtypeLabel" label="dtype" />
          <el-table-column label="Shape">
            <template #default="{ row }">
              {{ row.isLeaf ? formatShape(row.shape) : "—" }}
            </template>
          </el-table-column>
          <el-table-column align="right">
            <!--
              Header cell is clickable; toggles the SAME
              `totalParamsFormat` ref the top "Total parameters" pill
              uses, so the whole table + pill flip together. Cells
              themselves are not clickable per the user's ask.
            -->
            <template #header>
              <!--
                Icon sits BEFORE the label: with `align="right"` the
                column auto-widths to the header content and anything
                after "Parameters" gets clipped off the right edge.
                Putting the icon on the leading side keeps both parts
                inside the column regardless of how narrow ElTable
                sizes it.
              -->
              <span
                class="cursor-pointer select-none inline-flex items-center gap-1"
                :title="
                  totalParamsFormat === 'human'
                    ? 'Click to show the exact parameter count'
                    : 'Click to show a compact (K / M / B / T) summary'
                "
                @click="toggleTotalParamsFormat"
              >
                <div
                  class="i-carbon-arrows-horizontal text-[10px] opacity-60 flex-shrink-0"
                />
                <span>Parameters</span>
              </span>
            </template>
            <template #default="{ row }">
              {{
                totalParamsFormat === "human"
                  ? formatHumanReadable(row.parameters)
                  : formatNumber(row.parameters)
              }}
            </template>
          </el-table-column>
          <el-table-column label="% of parent" width="140">
            <template #default="{ row }">
              <!--
                Continuous-gradient "share of parent" bar. Bar alpha
                ramps from ~0.08 (very faint) at 0% to ~0.72 at 100%,
                so high-mass subtrees read as dark gray while long tails
                stay visible but unobtrusive. Same slate-500 RGB on
                both themes — alpha on transparent parent composes
                against the table background either way.
              -->
              <div
                class="relative h-5 rounded bg-gray-100 dark:bg-gray-700 overflow-hidden"
              >
                <div
                  class="absolute inset-y-0 left-0"
                  :style="percentBarStyle(row.percent)"
                />
                <div
                  class="relative text-xs text-right pr-2 leading-5 font-mono text-gray-700 dark:text-gray-200"
                >
                  {{ row.percent.toFixed(2) }}%
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="Bytes" align="right">
            <template #default="{ row }">
              <!--
                Hovering the cell reveals the exact byte count so a
                user eyeballing "497.51 KB" can still copy the precise
                509,452 bytes for a bug report or a sanity-check
                against the safetensors header math.
              -->
              <span :title="`${formatBytesExact(row.byteSize)} bytes`">
                {{ formatBytes(row.byteSize) }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div v-else-if="state === 'ready' && payload?.kind === 'parquet'">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">Rows</div>
          <div class="text-lg font-semibold mt-1">
            {{ formatNumber(payload.metadata.numRows) }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">Columns</div>
          <div class="text-lg font-semibold mt-1">
            {{ payload.summary.columnCount }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">
            Row groups
          </div>
          <div class="text-lg font-semibold mt-1">
            {{ payload.metadata.rowGroups.length }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">File size</div>
          <div class="text-lg font-semibold mt-1">
            {{ formatBytes(payload.metadata.byteLength) }}
          </div>
        </div>
      </div>

      <div class="mb-4">
        <h4 class="text-sm font-semibold mb-2">Columns (top-level)</h4>
        <el-table
          :data="parquetColumnRows"
          size="small"
          :border="true"
          max-height="260"
        >
          <el-table-column prop="name" label="Name" min-width="220" />
          <el-table-column prop="physicalType" label="Physical" width="130" />
          <el-table-column prop="logicalType" label="Logical" width="130" />
          <el-table-column prop="repetition" label="Repetition" width="140" />
        </el-table>
      </div>

      <div class="mb-4">
        <h4 class="text-sm font-semibold mb-2">Row groups</h4>
        <el-table
          :data="payload.metadata.rowGroups.map((rg, idx) => ({ idx, ...rg }))"
          size="small"
          :border="true"
          max-height="220"
        >
          <el-table-column prop="idx" label="#" width="70" />
          <el-table-column label="Rows" align="right">
            <template #default="{ row }">
              {{ formatNumber(row.numRows) }}
            </template>
          </el-table-column>
          <el-table-column label="Total size" align="right">
            <template #default="{ row }">
              {{ formatBytes(row.totalByteSize) }}
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="payload.metadata.createdBy" class="text-xs text-gray-500 dark:text-gray-400">
        Created by: {{ payload.metadata.createdBy }}
      </div>
    </div>

    <template #footer>
      <el-button @click="dialogVisible = false">Close</el-button>
    </template>
  </el-dialog>
</template>
