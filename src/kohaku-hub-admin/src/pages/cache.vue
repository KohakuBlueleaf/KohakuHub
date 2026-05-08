<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import AdminLayout from "@/components/AdminLayout.vue";
import { useAdminStore } from "@/stores/admin";
import { getCacheStats, resetCacheMetrics } from "@/utils/api";
import { ElMessage, ElMessageBox } from "element-plus";
import dayjs from "dayjs";

const router = useRouter();
const adminStore = useAdminStore();

const loading = ref(false);
const resetting = ref(false);
const snapshot = ref(null);
const lastError = ref(null);
const refreshIntervalSeconds = ref(0);
let refreshTimer = null;

const REFRESH_OPTIONS = [
  { label: "Off", value: 0 },
  { label: "Every 5 s", value: 5 },
  { label: "Every 15 s", value: 15 },
  { label: "Every 60 s", value: 60 },
];

// The cache layer's three operational states. Tagged so the dashboard
// is glanceable: green = working, blue = explicitly off, orange =
// configured-on but degraded (i.e. unreachable Valkey, silent fallback).
const STATE_TAG = {
  enabled: { type: "success", label: "Enabled" },
  disabled: { type: "info", label: "Disabled" },
  degraded: { type: "warning", label: "Degraded" },
  unknown: { type: "warning", label: "Unknown" },
};

const metrics = computed(() => snapshot.value?.metrics ?? null);
const memory = computed(() => snapshot.value?.memory ?? null);

// Resolve the operational state from the snapshot. ``configured_enabled``
// is what the config says; ``client_initialized`` is whether the API
// process actually owns a live connection. Combined:
//   on + initialized = enabled
//   on + !initialized = degraded (silent fallback to source)
//   off = disabled (intentional)
const operationalState = computed(() => {
  if (!metrics.value) return "unknown";
  if (!metrics.value.configured_enabled) return "disabled";
  return metrics.value.client_initialized ? "enabled" : "degraded";
});

const stateBadge = computed(() => STATE_TAG[operationalState.value]);

// Pivot per-namespace counters into a flat row table. Hits + misses make
// hit ratio actionable; errors flag misconfigurations or Valkey trouble
// without needing to read the logs.
const namespaceRows = computed(() => {
  if (!metrics.value) return [];
  const ns = new Set([
    ...Object.keys(metrics.value.hits || {}),
    ...Object.keys(metrics.value.misses || {}),
    ...Object.keys(metrics.value.errors || {}),
    ...Object.keys(metrics.value.set_count || {}),
    ...Object.keys(metrics.value.invalidate_count || {}),
  ]);
  return [...ns]
    .sort()
    .map((name) => {
      const hits = metrics.value.hits?.[name] ?? 0;
      const misses = metrics.value.misses?.[name] ?? 0;
      const errors = metrics.value.errors?.[name] ?? 0;
      const sets = metrics.value.set_count?.[name] ?? 0;
      const invalidates = metrics.value.invalidate_count?.[name] ?? 0;
      const total = hits + misses;
      const hitRate = total > 0 ? (hits / total) * 100 : null;
      return { name, hits, misses, errors, sets, invalidates, hitRate, total };
    });
});

const totals = computed(() => {
  const rows = namespaceRows.value;
  const sum = rows.reduce(
    (acc, r) => {
      acc.hits += r.hits;
      acc.misses += r.misses;
      acc.errors += r.errors;
      return acc;
    },
    { hits: 0, misses: 0, errors: 0 },
  );
  const tot = sum.hits + sum.misses;
  return {
    ...sum,
    hitRate: tot > 0 ? (sum.hits / tot) * 100 : null,
    total: tot,
  };
});

function formatNumber(n) {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat().format(n);
}

function formatBytes(n) {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(2)} ${units[i]}`;
}

function formatPercentage(value) {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}

function formatTimestamp(ms) {
  if (!ms) return "—";
  return dayjs(ms).format("YYYY-MM-DD HH:mm:ss");
}

function formatRunId(value) {
  if (!value) return "—";
  // Valkey run_ids are 40-char hex; show first 12 to stay readable.
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

function checkAuth() {
  if (!adminStore.token) {
    router.push("/login");
    return false;
  }
  return true;
}

async function loadStats({ silent = false } = {}) {
  if (!checkAuth()) return;
  loading.value = true;
  try {
    const data = await getCacheStats(adminStore.token);
    snapshot.value = data;
    lastError.value = null;
  } catch (error) {
    if (
      error.response?.status === 401 ||
      error.response?.status === 403
    ) {
      ElMessage.error("Invalid admin token. Please login again.");
      adminStore.logout();
      router.push("/login");
      return;
    }
    const detail =
      error.response?.data?.detail ||
      error.message ||
      "Failed to load cache stats";
    lastError.value = String(detail);
    if (!silent) {
      ElMessage.error(String(detail));
    }
  } finally {
    loading.value = false;
  }
}

async function handleResetMetrics() {
  if (!checkAuth()) return;
  try {
    await ElMessageBox.confirm(
      "Reset in-process hit / miss / error counters? Cache contents are NOT touched.",
      "Reset cache metrics",
      {
        confirmButtonText: "Reset",
        cancelButtonText: "Cancel",
        type: "warning",
      },
    );
  } catch {
    return; // user cancelled
  }
  resetting.value = true;
  try {
    await resetCacheMetrics(adminStore.token);
    ElMessage.success("Cache metric counters reset");
    await loadStats({ silent: true });
  } catch (error) {
    const detail =
      error.response?.data?.detail ||
      error.message ||
      "Failed to reset cache metrics";
    ElMessage.error(String(detail));
  } finally {
    resetting.value = false;
  }
}

function clearTimer() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

watch(refreshIntervalSeconds, (seconds) => {
  clearTimer();
  if (seconds > 0) {
    refreshTimer = setInterval(() => {
      loadStats({ silent: true });
    }, seconds * 1000);
  }
});

onMounted(() => {
  loadStats();
});

onBeforeUnmount(() => {
  clearTimer();
});
</script>

<template>
  <AdminLayout>
    <div class="page-container">
      <div class="flex justify-between items-center mb-6 gap-4 flex-wrap">
        <div>
          <h1 class="text-3xl font-bold text-gray-900 dark:text-gray-100">
            L2 Cache (Redis)
          </h1>
          <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">
            Hit / miss counters, Redis memory state, and the bootstrap-flush
            metadata. The cache is never on the correctness critical path —
            see <code>docs/development/cache.md</code> for the design.
          </p>
        </div>
        <div class="flex items-center gap-3">
          <el-select
            v-model="refreshIntervalSeconds"
            class="refresh-select"
            placeholder="Auto-refresh"
          >
            <el-option
              v-for="option in REFRESH_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-button
            type="primary"
            :loading="loading"
            @click="loadStats()"
            data-testid="cache-refresh"
          >
            <div class="i-carbon-renew mr-1" />
            Refresh
          </el-button>
          <el-button
            :loading="resetting"
            :disabled="operationalState !== 'enabled'"
            @click="handleResetMetrics"
            data-testid="cache-reset-metrics"
          >
            <div class="i-carbon-reset mr-1" />
            Reset counters
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="lastError"
        type="warning"
        :title="lastError"
        :closable="false"
        show-icon
        class="mb-4"
      />

      <!-- Top banner: operational state + memory at a glance -->
      <el-card
        v-if="metrics"
        shadow="never"
        class="mb-4 overall-banner"
        data-testid="cache-overall"
      >
        <div class="flex items-center gap-3 flex-wrap">
          <span class="font-semibold text-gray-700 dark:text-gray-200">
            Status
          </span>
          <el-tag :type="stateBadge.type" size="large" effect="dark">
            {{ stateBadge.label }}
          </el-tag>
          <span class="text-gray-500 dark:text-gray-400 text-sm">
            namespace
            <code>{{ metrics.namespace }}</code>
          </span>
          <span
            v-if="memory?.available"
            class="text-gray-500 dark:text-gray-400 text-sm"
          >
            · {{ memory.used_memory_human || formatBytes(memory.used_memory) }}
            used
            <span v-if="memory.maxmemory && memory.maxmemory > 0">
              / {{ memory.maxmemory_human || formatBytes(memory.maxmemory) }}
            </span>
          </span>
          <span
            v-if="memory?.available"
            class="text-gray-500 dark:text-gray-400 text-sm"
          >
            · policy
            <code>{{ memory.maxmemory_policy }}</code>
          </span>
          <span
            v-if="memory?.available && memory.evicted_keys !== undefined"
            class="text-gray-500 dark:text-gray-400 text-sm"
          >
            · evictions {{ formatNumber(memory.evicted_keys) }}
          </span>
        </div>
      </el-card>

      <!-- Bootstrap flush + singleflight summary -->
      <el-card
        v-if="metrics"
        shadow="never"
        class="mb-4"
        data-testid="cache-bootstrap"
      >
        <template #header>
          <span class="font-semibold">Bootstrap flush</span>
        </template>
        <ul class="bootstrap-meta">
          <li>
            <span class="meta-label">Last flushed run_id</span>
            <span class="meta-value">
              <code>{{ formatRunId(metrics.last_flush_run_id) }}</code>
            </span>
          </li>
          <li>
            <span class="meta-label">Last flushed at</span>
            <span class="meta-value">
              {{ formatTimestamp(metrics.last_flush_at_ms) }}
            </span>
          </li>
          <li>
            <span class="meta-label">Keys flushed</span>
            <span class="meta-value">
              {{ formatNumber(metrics.last_flushed_keys) }}
            </span>
          </li>
          <li>
            <span class="meta-label">Singleflight contention</span>
            <span class="meta-value">
              {{ formatNumber(metrics.singleflight_contention) }}
            </span>
          </li>
        </ul>
      </el-card>

      <!-- Per-namespace counters -->
      <el-card
        shadow="never"
        v-loading="loading"
        data-testid="cache-namespace-table"
      >
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-semibold">Namespaces</span>
            <span
              v-if="totals.total > 0"
              class="text-gray-500 dark:text-gray-400 text-sm"
            >
              total {{ formatNumber(totals.total) }} reads ·
              hit rate {{ formatPercentage(totals.hitRate) }}
            </span>
          </div>
        </template>
        <el-table
          v-if="namespaceRows.length"
          :data="namespaceRows"
          stripe
          size="default"
        >
          <el-table-column prop="name" label="Namespace" width="180">
            <template #default="{ row }">
              <code>{{ row.name }}</code>
            </template>
          </el-table-column>
          <el-table-column label="Hits" align="right" width="120">
            <template #default="{ row }">
              {{ formatNumber(row.hits) }}
            </template>
          </el-table-column>
          <el-table-column label="Misses" align="right" width="120">
            <template #default="{ row }">
              {{ formatNumber(row.misses) }}
            </template>
          </el-table-column>
          <el-table-column label="Hit rate" align="right" width="120">
            <template #default="{ row }">
              {{ formatPercentage(row.hitRate) }}
            </template>
          </el-table-column>
          <el-table-column label="Sets" align="right" width="120">
            <template #default="{ row }">
              {{ formatNumber(row.sets) }}
            </template>
          </el-table-column>
          <el-table-column label="Invalidates" align="right" width="140">
            <template #default="{ row }">
              {{ formatNumber(row.invalidates) }}
            </template>
          </el-table-column>
          <el-table-column label="Errors" align="right" width="120">
            <template #default="{ row }">
              <el-tag
                v-if="row.errors > 0"
                type="danger"
                size="small"
                effect="light"
              >
                {{ formatNumber(row.errors) }}
              </el-tag>
              <span v-else>0</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else-if="!loading"
          description="No traffic recorded yet"
        />
      </el-card>
    </div>
  </AdminLayout>
</template>

<style scoped>
.refresh-select {
  width: 160px;
}

.overall-banner {
  border-radius: 10px;
}

.bootstrap-meta {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.bootstrap-meta li {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.meta-value {
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.page-container {
  padding: 24px;
  max-width: 1280px;
  margin: 0 auto;
}
</style>
