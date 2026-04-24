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
  parseSafetensorsMetadata,
  summarizeSafetensors,
  SafetensorsFetchError,
} from "@/utils/safetensors";
import {
  parseParquetMetadata,
  summarizeParquetSchema,
} from "@/utils/parquet";

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
const errorMessage = ref("");
const errorCorsLikely = ref(false);
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
  errorMessage.value = "";
  errorCorsLikely.value = false;

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
    errorMessage.value = err?.message ?? String(err);
    errorCorsLikely.value = isLikelyCorsError(err);
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

function isLikelyCorsError(err) {
  // Browsers don't expose the CORS failure reason to JS. All we get is a
  // generic TypeError with "Failed to fetch" (Chromium) or "NetworkError
  // when attempting to fetch resource" (Firefox). We flag likely-CORS so
  // the modal can point at the MinIO CORS doc section — misdiagnosing a
  // real 404 as CORS is cheap; the retry button surfaces the real error.
  const message = (err?.message ?? "").toLowerCase();
  if (err?.name === "TypeError") return true;
  if (message.includes("failed to fetch")) return true;
  if (message.includes("networkerror")) return true;
  return false;
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

const safetensorsRows = computed(() => {
  if (payload.value?.kind !== "safetensors") return [];
  return Object.entries(payload.value.header.tensors).map(
    ([name, entry]) => ({
      name,
      dtype: entry.dtype,
      shape: formatShape(entry.shape),
      parameters: entry.parameters,
      byteSize: entry.data_offsets[1] - entry.data_offsets[0],
    }),
  );
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

    <div
      v-else-if="state === 'error'"
      class="py-8 flex flex-col items-center text-center"
    >
      <div class="i-carbon-warning-alt text-5xl text-amber-500" />
      <p class="mt-4 text-sm font-medium text-gray-800 dark:text-gray-100">
        Preview failed
      </p>
      <p class="mt-2 text-xs text-gray-500 dark:text-gray-400 max-w-md break-words">
        {{ errorMessage }}
      </p>
      <p
        v-if="errorCorsLikely"
        class="mt-3 text-xs text-gray-500 dark:text-gray-400 max-w-md"
      >
        This looks like a CORS failure on the object-storage host. Preview
        needs the S3/MinIO backend to advertise
        <code>Access-Control-Allow-Origin</code>. See
        <em>docs/development/local-dev.md &rarr; "MinIO CORS"</em>.
      </p>
      <el-button class="mt-4" type="primary" plain @click="retry">
        Retry
      </el-button>
    </div>

    <div v-else-if="state === 'ready' && payload?.kind === 'safetensors'">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">Tensors</div>
          <div class="text-lg font-semibold mt-1">
            {{ Object.keys(payload.header.tensors).length }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
          <div class="text-xs text-gray-500 dark:text-gray-400">
            Total parameters
          </div>
          <div class="text-lg font-semibold mt-1">
            {{ formatNumber(payload.summary.total) }}
          </div>
        </div>
        <div class="p-3 bg-gray-50 dark:bg-gray-800 rounded">
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
          :data="safetensorsRows"
          size="small"
          :border="true"
          max-height="320"
        >
          <el-table-column prop="name" label="Name" min-width="240" />
          <el-table-column prop="dtype" label="dtype" width="110" />
          <el-table-column prop="shape" label="Shape" min-width="160" />
          <el-table-column label="Parameters" width="140" align="right">
            <template #default="{ row }">
              {{ formatNumber(row.parameters) }}
            </template>
          </el-table-column>
          <el-table-column label="Bytes" width="120" align="right">
            <template #default="{ row }">
              {{ formatBytes(row.byteSize) }}
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
