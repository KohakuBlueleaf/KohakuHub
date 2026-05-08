<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import AdminLayout from "@/components/AdminLayout.vue";
import ProbeReportView from "@/components/ProbeReportView.vue";
import { useChainTesterState } from "@/composables/useChainTesterState";
import { useAdminStore } from "@/stores/admin";
import {
  listFallbackSources,
  createFallbackSource,
  updateFallbackSource,
  deleteFallbackSource,
  getFallbackCacheStats,
  clearFallbackCache,
  invalidateFallbackRepoCache,
  invalidateFallbackUserCacheById,
  invalidateFallbackUserCacheByUsername,
  listUsers,
  listRepositories,
  bulkReplaceFallbackSources,
  runFallbackChainSimulate,
  runFallbackProbe,
} from "@/utils/api";
import { ElMessage, ElMessageBox } from "element-plus";
import dayjs from "dayjs";

const router = useRouter();
const adminStore = useAdminStore();

const sources = ref([]);
const cacheStats = ref(null);
const loading = ref(false);

// Form dialog
const dialogVisible = ref(false);
const dialogMode = ref("create"); // 'create' or 'edit'
const formData = ref({
  id: null,
  namespace: "",
  url: "",
  token: "",
  priority: 100,
  name: "",
  source_type: "huggingface",
  enabled: true,
});

// Per-repo eviction dialog (#79 admin tooling).
const evictRepoDialogVisible = ref(false);
const evictRepoForm = ref({
  repo_type: "model",
  namespace: "",
  name: "",
});

// Per-user eviction dialog. ``mode`` toggles between username- and
// user_id-keyed paths so an admin who only knows one of the two doesn't
// have to leave the page to look up the other.
const evictUserDialogVisible = ref(false);
const evictUserForm = ref({
  mode: "username", // "username" | "user_id"
  username: "",
  user_id: null,
});

function checkAuth() {
  if (!adminStore.token) {
    router.push("/login");
    return false;
  }
  return true;
}

async function loadSources() {
  if (!checkAuth()) return;

  loading.value = true;
  try {
    sources.value = await listFallbackSources(adminStore.token);
  } catch (error) {
    console.error("Failed to load fallback sources:", error);
    ElMessage.error(
      error.response?.data?.detail?.error || "Failed to load fallback sources",
    );
  } finally {
    loading.value = false;
  }
}

async function loadCacheStats() {
  if (!checkAuth()) return;

  try {
    cacheStats.value = await getFallbackCacheStats(adminStore.token);
  } catch (error) {
    console.error("Failed to load cache stats:", error);
  }
}

function openCreateDialog() {
  dialogMode.value = "create";
  formData.value = {
    id: null,
    namespace: "",
    url: "",
    token: "",
    priority: 100,
    name: "",
    source_type: "huggingface",
    enabled: true,
  };
  dialogVisible.value = true;
}

function openEditDialog(source) {
  dialogMode.value = "edit";
  formData.value = {
    id: source.id,
    namespace: source.namespace,
    url: source.url,
    token: source.token || "",
    priority: source.priority,
    name: source.name,
    source_type: source.source_type,
    enabled: source.enabled,
  };
  dialogVisible.value = true;
}

async function handleSubmit() {
  if (!checkAuth()) return;

  loading.value = true;
  try {
    if (dialogMode.value === "create") {
      await createFallbackSource(adminStore.token, formData.value);
      ElMessage.success("Fallback source created successfully");
    } else {
      const { id, ...updateData } = formData.value;
      await updateFallbackSource(adminStore.token, id, updateData);
      ElMessage.success("Fallback source updated successfully");
    }

    dialogVisible.value = false;
    await loadSources();
    await loadCacheStats();
  } catch (error) {
    console.error("Failed to save fallback source:", error);
    ElMessage.error(
      error.response?.data?.detail?.error || "Failed to save fallback source",
    );
  } finally {
    loading.value = false;
  }
}

async function handleDelete(source) {
  if (!checkAuth()) return;

  try {
    await ElMessageBox.confirm(
      `Are you sure you want to delete "${source.name}"? This action cannot be undone.`,
      "Confirm Delete",
      {
        confirmButtonText: "Delete",
        cancelButtonText: "Cancel",
        type: "warning",
      },
    );

    loading.value = true;
    await deleteFallbackSource(adminStore.token, source.id);
    ElMessage.success(`Fallback source "${source.name}" deleted`);
    await loadSources();
    await loadCacheStats();
  } catch (error) {
    if (error !== "cancel") {
      console.error("Failed to delete fallback source:", error);
      ElMessage.error(
        error.response?.data?.detail?.error ||
          "Failed to delete fallback source",
      );
    }
  } finally {
    loading.value = false;
  }
}

async function handleToggleEnabled(source) {
  if (!checkAuth()) return;

  loading.value = true;
  try {
    await updateFallbackSource(adminStore.token, source.id, {
      enabled: !source.enabled,
    });
    ElMessage.success(
      `Source "${source.name}" ${!source.enabled ? "enabled" : "disabled"}`,
    );
    await loadSources();
  } catch (error) {
    console.error("Failed to toggle source:", error);
    ElMessage.error(
      error.response?.data?.detail?.error || "Failed to toggle source",
    );
  } finally {
    loading.value = false;
  }
}

async function handleClearCache() {
  if (!checkAuth()) return;

  try {
    await ElMessageBox.confirm(
      "Are you sure you want to clear the fallback cache? This will remove all cached repository→source mappings.",
      "Confirm Clear Cache",
      {
        confirmButtonText: "Clear Cache",
        cancelButtonText: "Cancel",
        type: "warning",
      },
    );

    loading.value = true;
    const result = await clearFallbackCache(adminStore.token);
    ElMessage.success(result.message);
    await loadCacheStats();
  } catch (error) {
    if (error !== "cancel") {
      console.error("Failed to clear cache:", error);
      ElMessage.error(
        error.response?.data?.detail?.error || "Failed to clear cache",
      );
    }
  } finally {
    loading.value = false;
  }
}

function openEvictRepoDialog() {
  evictRepoForm.value = {
    repo_type: "model",
    namespace: "",
    name: "",
  };
  evictRepoDialogVisible.value = true;
}

// Autocomplete suggestion fetchers (#79). Each is scoped to the right
// admin lookup for the field it serves:
//
//  - namespace (evict-by-repo dialog) → ``listUsers(include_orgs=true)``
//    because a repository's namespace is either a user **or** an
//    organisation, and listUsers in that mode returns the union.
//
//  - repo name (evict-by-repo dialog, given a fixed namespace) →
//    ``listRepositories(namespace, repo_type)`` for the
//    namespace-scoped enumeration. The query string filters
//    server-side via the existing ``search`` param.
//
//  - username (evict-by-user dialog, username mode) →
//    ``listUsers(include_orgs=false)``. Per the post-#79 cache key
//    shape ``(user_id_or_None, tokens_hash, repo_type, ns, name)``,
//    user_id is the request originator — orgs never originate a
//    request, so they have no cache bucket to evict and don't belong
//    in these suggestions.
//
// All three return ``{value: string}[]`` per Element Plus's
// ``el-autocomplete`` ``fetch-suggestions`` contract.
async function fetchNamespaceSuggestions(query, cb) {
  if (!checkAuth()) {
    cb([]);
    return;
  }
  try {
    if (!query || query.length < 1) {
      cb([]);
      return;
    }
    const data = await listUsers(adminStore.token, {
      search: query,
      limit: 20,
      include_orgs: true, // namespaces span both users and orgs
    });
    const items = Array.isArray(data) ? data : data?.users || data?.items || [];
    cb(
      items
        .filter((u) => u.username)
        .map((u) => ({ value: u.username })),
    );
  } catch (error) {
    console.error("Failed to fetch namespace suggestions:", error);
    cb([]);
  }
}

async function fetchRepoNameSuggestions(query, cb) {
  if (!checkAuth()) {
    cb([]);
    return;
  }
  try {
    if (!query || query.length < 1) {
      cb([]);
      return;
    }
    // Namespace-scoped enumeration. Without a chosen namespace we
    // can't bound the search to a single namespace, so fall back to
    // a coarse filter — but this dialog asks the operator to pick
    // namespace first, so the namespace-empty case is a UX hint to
    // fill that field first.
    const data = await listRepositories(adminStore.token, {
      search: query,
      repo_type: evictRepoForm.value.repo_type,
      namespace: evictRepoForm.value.namespace || undefined,
      limit: 20,
    });
    // Backend wraps under ``{repositories: [...]}`` (admin /repositories
    // route shape); also tolerate ``{items: [...]}`` and a raw array
    // for forward compatibility.
    const items = Array.isArray(data)
      ? data
      : data?.repositories || data?.items || [];
    cb(
      items
        .filter((repo) => repo.name)
        .map((repo) => ({ value: repo.name })),
    );
  } catch (error) {
    console.error("Failed to fetch repo name suggestions:", error);
    cb([]);
  }
}

async function fetchProbeRepoNameSuggestions(query, cb) {
  // Mirror of ``fetchRepoNameSuggestions`` but scoped to ``probeForm``
  // (the chain-tester probe target form) instead of ``evictRepoForm``.
  // Two parallel fetchers exist because the two forms have different
  // ``repo_type`` / ``namespace`` Vue refs and conflating them would
  // tie the eviction dialog's autocomplete to whatever the probe form
  // happens to have selected.
  if (!checkAuth()) {
    cb([]);
    return;
  }
  try {
    if (!query || query.length < 1) {
      cb([]);
      return;
    }
    const data = await listRepositories(adminStore.token, {
      search: query,
      repo_type: probeForm.value.repo_type,
      namespace: probeForm.value.namespace || undefined,
      limit: 20,
    });
    const items = Array.isArray(data)
      ? data
      : data?.repositories || data?.items || [];
    cb(
      items
        .filter((repo) => repo.name)
        .map((repo) => ({ value: repo.name })),
    );
  } catch (error) {
    console.error("Failed to fetch probe repo name suggestions:", error);
    cb([]);
  }
}

async function fetchUsernameSuggestions(query, cb) {
  if (!checkAuth()) {
    cb([]);
    return;
  }
  try {
    if (!query || query.length < 1) {
      cb([]);
      return;
    }
    // ``include_orgs: false`` — fallback cache buckets are keyed by the
    // real user_id of the request originator. Orgs never make requests
    // themselves, so they have no bucket to evict and don't belong in
    // these suggestions.
    const data = await listUsers(adminStore.token, {
      search: query,
      limit: 20,
      include_orgs: false,
    });
    const items = Array.isArray(data) ? data : data?.users || data?.items || [];
    cb(
      items
        .filter((u) => u.username)
        .map((u) => ({ value: u.username })),
    );
  } catch (error) {
    console.error("Failed to fetch username suggestions:", error);
    cb([]);
  }
}

function openEvictUserDialog() {
  evictUserForm.value = {
    mode: "username",
    username: "",
    user_id: null,
  };
  evictUserDialogVisible.value = true;
}

async function handleEvictRepo() {
  if (!checkAuth()) return;

  const { repo_type, namespace, name } = evictRepoForm.value;
  if (!repo_type || !namespace || !name) {
    ElMessage.error("repo_type, namespace, and name are all required");
    return;
  }

  loading.value = true;
  try {
    const result = await invalidateFallbackRepoCache(
      adminStore.token,
      repo_type,
      namespace.trim(),
      name.trim(),
    );
    ElMessage.success(
      `Evicted ${result.evicted} cache entr${result.evicted === 1 ? "y" : "ies"} for ${repo_type}/${namespace}/${name}`,
    );
    evictRepoDialogVisible.value = false;
    await loadCacheStats();
  } catch (error) {
    console.error("Failed to evict repo cache:", error);
    ElMessage.error(
      error.response?.data?.detail?.error || "Failed to evict repo cache",
    );
  } finally {
    loading.value = false;
  }
}

async function handleEvictUser() {
  if (!checkAuth()) return;

  const { mode, username, user_id } = evictUserForm.value;

  if (mode === "username") {
    if (!username || !username.trim()) {
      ElMessage.error("Username is required");
      return;
    }
  } else {
    if (user_id == null || Number.isNaN(Number(user_id)) || Number(user_id) <= 0) {
      ElMessage.error("user_id must be a positive integer");
      return;
    }
  }

  // Strong confirm — per-user eviction crosses every repo this user has
  // bound. See PR #81 review item #5–#12 noted comment for the
  // confirm-on-broad-scope rule.
  try {
    const targetLabel =
      mode === "username" ? `username "${username}"` : `user_id ${user_id}`;
    await ElMessageBox.confirm(
      `Evict every cached fallback binding for ${targetLabel}? This drops every repo binding the user has cached and forces a re-probe on their next request.`,
      "Confirm User Cache Eviction",
      {
        confirmButtonText: "Evict",
        cancelButtonText: "Cancel",
        type: "warning",
      },
    );
  } catch (error) {
    if (error !== "cancel") {
      console.error("Confirm dialog dismissed unexpectedly:", error);
    }
    return;
  }

  loading.value = true;
  try {
    let result;
    if (mode === "username") {
      result = await invalidateFallbackUserCacheByUsername(
        adminStore.token,
        username.trim(),
      );
    } else {
      result = await invalidateFallbackUserCacheById(
        adminStore.token,
        Number(user_id),
      );
    }
    const tail =
      mode === "username"
        ? `${username} (user_id=${result.user_id})`
        : `user_id=${user_id}`;
    ElMessage.success(
      `Evicted ${result.evicted} cache entr${result.evicted === 1 ? "y" : "ies"} for ${tail}`,
    );
    evictUserDialogVisible.value = false;
    await loadCacheStats();
  } catch (error) {
    console.error("Failed to evict user cache:", error);
    ElMessage.error(
      error.response?.data?.detail?.error || "Failed to evict user cache",
    );
  } finally {
    loading.value = false;
  }
}

function formatDate(dateStr) {
  return dayjs(dateStr).format("YYYY-MM-DD HH:mm:ss");
}

// =====================================================================
// Chain Tester (#78). System-state draft + user-state simulation +
// probe runner with timeline rendering.
// =====================================================================

// HF-API-equivalent labels for the op dropdown — operators recognise
// these by their hf_hub method names rather than the internal op string.
const PROBE_OP_OPTIONS = [
  {
    value: "info",
    label: "Repo info  (HfApi.model_info / dataset_info / space_info)",
  },
  {
    value: "tree",
    label: "List files  (HfApi.list_repo_files / list_repo_tree)",
  },
  {
    value: "resolve",
    label: "Resolve file  (hf_hub_download / HEAD on /resolve/)",
  },
  {
    value: "paths_info",
    label: "paths_info  (HfApi.get_paths_info)",
  },
];
const PROBE_OPS = PROBE_OP_OPTIONS.map((o) => o.value);
const REPO_TYPES = ["model", "dataset", "space"];
const SOURCE_TYPES = ["huggingface", "kohakuhub"];

// ``draftSources`` and ``draftDirty`` come from ``useChainTesterState``
// at the top of this script — see comment there for why they're
// module-level. Edits accumulate; only ``pushDraftToSystem`` propagates
// them back to the live config.

function _blankDraftSource() {
  return {
    namespace: "",
    url: "",
    token: "",
    priority: 100,
    name: "",
    source_type: "huggingface",
    enabled: true,
  };
}

function loadDraftFromSystem() {
  if (!checkAuth()) return;
  // Fresh deep copy of live sources into the draft area.
  draftSources.value = (sources.value || []).map((s) => ({
    namespace: s.namespace || "",
    url: s.url || "",
    token: s.token || "",
    priority: s.priority ?? 100,
    name: s.name || "",
    source_type: s.source_type || "huggingface",
    enabled: s.enabled !== false,
  }));
  draftDirty.value = false;
  ElMessage.success(
    `Loaded ${draftSources.value.length} source(s) from system into draft`,
  );
}

function discardDraft() {
  draftSources.value = [];
  draftDirty.value = false;
  ElMessage.info("Draft discarded");
}

function addDraftSource() {
  draftSources.value.push(_blankDraftSource());
  draftDirty.value = true;
}

function removeDraftSource(index) {
  draftSources.value.splice(index, 1);
  draftDirty.value = true;
}

function _markDirty() {
  draftDirty.value = true;
}

async function pushDraftToSystem() {
  if (!checkAuth()) return;
  // Strong confirm: bulk-replace touches every row + bumps cache gen.
  try {
    await ElMessageBox.confirm(
      `Replace the live fallback source list with the ${draftSources.value.length} draft entr${draftSources.value.length === 1 ? "y" : "ies"}? This atomically swaps the entire table and clears the bind cache.`,
      "Confirm Push Draft to System",
      {
        confirmButtonText: "Push",
        cancelButtonText: "Cancel",
        type: "warning",
      },
    );
  } catch (error) {
    if (error !== "cancel") {
      console.error("Push-draft confirm dismissed unexpectedly:", error);
    }
    return;
  }

  loading.value = true;
  try {
    // Sanitize draft rows: drop empty token strings (treat "" as no token).
    const sanitized = draftSources.value.map((s) => ({
      namespace: s.namespace || "",
      url: s.url,
      token: s.token ? s.token : null,
      priority: Number(s.priority) || 100,
      name: s.name,
      source_type: s.source_type,
      enabled: s.enabled !== false,
    }));
    const result = await bulkReplaceFallbackSources(adminStore.token, sanitized);
    ElMessage.success(
      `Pushed draft to system: ${result.before} → ${result.after} source(s)`,
    );
    draftDirty.value = false;
    await loadSources();
    await loadCacheStats();
  } catch (error) {
    console.error("Failed to push draft:", error);
    ElMessage.error(
      error.response?.data?.detail?.error ||
        "Failed to push draft to system",
    );
  } finally {
    loading.value = false;
  }
}

// Chain Tester state lives in module-level singletons (see
// useChainTesterState) so SPA route switches don't unmount it.
// Component-local refs would reset every time the operator
// navigates away and back, blowing away any pending draft edits.
// Browser refresh still resets the page (whole bundle re-evaluates),
// and admin logout calls ``resetChainTesterState`` for cleanup —
// route changes and tab switches preserve every field.
const {
  probeTab,
  probeForm,
  draftSources,
  draftDirty,
  simIdentity,
  simHeaderTokens,
  realKhubToken,
  realHeaderTokens,
  autoLoadDone,
} = useChainTesterState();

// =====================================================================
// Frame 1: Draft simulate
// =====================================================================
//
// Left (system state): the ``draftSources`` list + its load / push /
// discard controls (already defined above).
// Right (user state): which identity to impersonate + per-URL token
// overlay. Impersonation is only meaningful in simulate mode — admin
// doesn't bear other users' khub credentials, so for the real-probe
// frame we drop it and use the operator's own auth instead.
//
// ``simIdentity`` and ``simHeaderTokens`` come from
// ``useChainTesterState`` (module-level singletons).

function addSimHeaderToken() {
  simHeaderTokens.value.push({ url: "", token: "" });
}
function removeSimHeaderToken(index) {
  simHeaderTokens.value.splice(index, 1);
}
function _simHeaderTokensToObj() {
  const out = {};
  for (const row of simHeaderTokens.value) {
    if (row.url && row.token) out[row.url] = row.token;
  }
  return out;
}

const simRunning = ref(false);
const simReport = ref(null);
const simError = ref(null);

function _validateProbeTargetForm(form) {
  if (!form.namespace || !form.name) {
    ElMessage.error("namespace and name are required for the probe target");
    return false;
  }
  return true;
}

function _parsePathsCsv(form) {
  return form.op === "paths_info" && form.paths_csv
    ? form.paths_csv
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean)
    : null;
}

async function runSimulate() {
  if (!checkAuth()) return;
  if (!_validateProbeTargetForm(probeForm.value)) return;
  simRunning.value = true;
  simError.value = null;
  try {
    const payload = {
      op: probeForm.value.op,
      repo_type: probeForm.value.repo_type,
      namespace: probeForm.value.namespace,
      name: probeForm.value.name,
      revision: probeForm.value.revision || "main",
      file_path: probeForm.value.file_path || "",
      paths: _parsePathsCsv(probeForm.value),
      sources: draftSources.value.map((s) => ({
        name: s.name || s.url,
        url: s.url,
        source_type: s.source_type,
        token: s.token || null,
        priority: Number(s.priority) || 100,
      })),
      header_tokens: _simHeaderTokensToObj(),
    };
    if (simIdentity.value.mode === "username" && simIdentity.value.username) {
      payload.as_username = simIdentity.value.username.trim();
    } else if (
      simIdentity.value.mode === "user_id" &&
      simIdentity.value.user_id != null &&
      Number(simIdentity.value.user_id) > 0
    ) {
      payload.as_user_id = Number(simIdentity.value.user_id);
    }
    simReport.value = await runFallbackChainSimulate(adminStore.token, payload);
  } catch (error) {
    console.error("simulate probe failed:", error);
    simError.value =
      error.response?.data?.detail?.error || error.message || "Simulate failed";
    simReport.value = null;
  } finally {
    simRunning.value = false;
  }
}

// =====================================================================
// Frame 2: Live real probe
// =====================================================================
//
// Left (system state): read-only summary of the *currently-applied*
// fallback source list (the same ``sources`` ref the live management
// table renders).
// Right (user state): admin's own KohakuHub access token + per-URL
// fallback-source tokens, combined into a single ``Authorization``
// header in the production "Bearer khub_xxx|url,token|..." shape.
// Empty everywhere ⇒ anonymous, no header sent.
//
// ``realKhubToken`` and ``realHeaderTokens`` come from
// ``useChainTesterState`` (module-level singletons).

function addRealHeaderToken() {
  realHeaderTokens.value.push({ url: "", token: "" });
}
function removeRealHeaderToken(index) {
  realHeaderTokens.value.splice(index, 1);
}

function _buildRealAuthorizationHeader() {
  const segments = [];
  for (const row of realHeaderTokens.value) {
    if (row.url && row.token) {
      segments.push(`${row.url},${row.token}`);
    }
  }
  const t = (realKhubToken.value || "").trim();
  if (!t && segments.length === 0) return null;
  return `Bearer ${t}${segments.length > 0 ? "|" + segments.join("|") : ""}`;
}

const realRunning = ref(false);
const realReport = ref(null);
const realError = ref(null);

async function runRealRequest() {
  if (!checkAuth()) return;
  if (!_validateProbeTargetForm(probeForm.value)) return;
  realRunning.value = true;
  realError.value = null;
  try {
    realReport.value = await runFallbackProbe({
      op: probeForm.value.op,
      repo_type: probeForm.value.repo_type,
      namespace: probeForm.value.namespace,
      name: probeForm.value.name,
      revision: probeForm.value.revision || "main",
      file_path: probeForm.value.file_path || "",
      paths: _parsePathsCsv(probeForm.value),
      authorization: _buildRealAuthorizationHeader(),
    });
  } catch (error) {
    console.error("real request failed:", error);
    realError.value =
      error.response?.data?.detail?.error || error.message || "Probe failed";
    realReport.value = null;
  } finally {
    realRunning.value = false;
  }
}

function decisionTagType(decision) {
  switch (decision) {
    case "LOCAL_HIT":
    case "BIND_AND_RESPOND":
      return "success";
    case "LOCAL_FILTERED":
    case "BIND_AND_PROPAGATE":
      return "warning";
    case "LOCAL_MISS":
    case "TRY_NEXT_SOURCE":
      return "info";
    case "LOCAL_OTHER_ERROR":
    case "TIMEOUT":
    case "NETWORK_ERROR":
      return "danger";
    default:
      return "info";
  }
}

// First-mount auto-seed:
//   On the operator's first visit per page-load, seed the draft
//   from the live config so they can edit + simulate immediately
//   without clicking Load. ``autoLoadDone`` is module-level (see
//   useChainTesterState) so subsequent route-switch returns DON'T
//   re-seed — that would clobber pending edits and would also
//   override an explicit Discard the operator just performed.
//
//   Page refresh resets ``autoLoadDone`` to false (whole bundle re-
//   evaluates), and admin logout resets it via
//   ``resetChainTesterState`` — both correctly fall back to "auto-
//   seed on next mount".
onMounted(async () => {
  await loadSources();
  await loadCacheStats();
  if (!autoLoadDone.value && draftSources.value.length === 0) {
    loadDraftFromSystem();
    autoLoadDone.value = true;
  }
});
</script>

<template>
  <AdminLayout>
    <div class="page-container">
      <div class="flex justify-between items-center mb-6 gap-4 flex-wrap">
        <div>
          <h1 class="text-3xl font-bold text-gray-900 dark:text-gray-100">
            Fallback Sources
          </h1>
          <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">
            Manage external repository sources (HuggingFace, other KohakuHub
            instances).
          </p>
        </div>
      </div>

      <!-- Cache Stats Card -->
      <el-card v-if="cacheStats" class="stats-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <span>Cache Statistics</span>
            <div class="cache-actions">
              <el-button
                type="warning"
                size="small"
                @click="openEvictRepoDialog"
                :loading="loading"
                data-testid="evict-repo-button"
              >
                <i class="i-carbon-cube mr-1"></i>
                Evict by Repo...
              </el-button>
              <el-button
                type="warning"
                size="small"
                @click="openEvictUserDialog"
                :loading="loading"
                data-testid="evict-user-button"
              >
                <i class="i-carbon-user mr-1"></i>
                Evict by User...
              </el-button>
              <el-button
                type="danger"
                size="small"
                @click="handleClearCache"
                :loading="loading"
              >
                <i class="i-carbon-trash-can mr-1"></i>
                Clear Cache
              </el-button>
            </div>
          </div>
        </template>
        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-label">Size</div>
            <div class="stat-value">{{ cacheStats.size }}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Max Size</div>
            <div class="stat-value">{{ cacheStats.maxsize }}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">TTL</div>
            <div class="stat-value">{{ cacheStats.ttl_seconds }}s</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Usage</div>
            <div class="stat-value">{{ cacheStats.usage_percent }}%</div>
          </div>
        </div>
      </el-card>

      <!-- Chain Tester Card (#78) -->
      <el-card
        class="tester-card"
        shadow="hover"
        data-testid="tester-card"
      >
        <template #header>
          <div class="card-header">
            <span>
              Chain Tester
              <el-tag
                v-if="draftDirty"
                type="warning"
                size="small"
                class="ml-2"
                data-testid="draft-dirty-tag"
              >
                Draft modified
              </el-tag>
            </span>
          </div>
        </template>

        <p class="tester-help">
          Edit a system-state draft and a user-state simulation, run a probe to
          see how the chain would resolve, then push the draft back to the live
          configuration once you're satisfied. Simulate runs against the draft
          (no live impact); Real runs against the current live config and the
          impersonated identity. Neither writes the production cache.
        </p>

        <!-- Probe target / op -->
        <h3 class="tester-section-title">Probe target</h3>
        <el-form :model="probeForm" label-width="120px" class="tester-form">
          <div class="tester-grid">
            <el-form-item label="Op" required>
              <el-select
                v-model="probeForm.op"
                style="width: 100%"
                data-testid="probe-op"
              >
                <el-option
                  v-for="opt in PROBE_OP_OPTIONS"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="Repo type" required>
              <el-select
                v-model="probeForm.repo_type"
                style="width: 100%"
                data-testid="probe-repo-type"
              >
                <el-option
                  v-for="rt in REPO_TYPES"
                  :key="rt"
                  :label="rt"
                  :value="rt"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="Namespace" required>
              <el-autocomplete
                v-model="probeForm.namespace"
                :fetch-suggestions="fetchNamespaceSuggestions"
                placeholder="owner / org"
                clearable
                style="width: 100%"
                data-testid="probe-namespace"
              />
            </el-form-item>
            <el-form-item label="Name" required>
              <el-autocomplete
                v-model="probeForm.name"
                :fetch-suggestions="fetchProbeRepoNameSuggestions"
                placeholder="repo name"
                clearable
                style="width: 100%"
                data-testid="probe-name"
              />
            </el-form-item>
            <el-form-item label="Revision">
              <el-input
                v-model="probeForm.revision"
                placeholder="main"
                data-testid="probe-revision"
              />
            </el-form-item>
            <el-form-item
              v-if="probeForm.op === 'resolve' || probeForm.op === 'tree'"
              label="File / path"
            >
              <el-input
                v-model="probeForm.file_path"
                :placeholder="
                  probeForm.op === 'resolve' ? 'config.json' : '/'
                "
                data-testid="probe-file-path"
              />
            </el-form-item>
            <el-form-item
              v-if="probeForm.op === 'paths_info'"
              label="Paths (CSV)"
            >
              <el-input
                v-model="probeForm.paths_csv"
                placeholder="config.json, README.md"
                data-testid="probe-paths-csv"
              />
            </el-form-item>
          </div>
        </el-form>

        <!-- Tabbed two-frame layout: Draft simulate vs Live real probe.
             Each frame has system-state on the left (draft sources for
             simulate; read-only live sources for real probe), user-state
             on the right (impersonation+per-URL tokens for simulate;
             admin token+per-URL tokens for real), and its own run+report
             section below the grid. -->
        <el-tabs
          v-model="probeTab"
          class="tester-tabs"
          data-testid="tester-tabs"
        >
          <!-- ===== Tab 1: Draft simulate ===== -->
          <el-tab-pane name="simulate" data-testid="tester-tab-simulate">
            <template #label>
              <span data-testid="tester-tab-simulate-label">
                <i class="i-carbon-edit mr-1"></i>
                Draft simulate
              </span>
            </template>

            <p class="tester-section-hint">
              Run the chain probe against an editable draft source list +
              an impersonated identity. Hits the
              <code>/admin/api/fallback/test/simulate</code>
              endpoint — pure read, never writes the live config or the
              bind cache. The local hop runs through the *real* handler
              code (via
              <code>__wrapped__</code>) so
              <code>LOCAL_HIT</code>/<code>LOCAL_FILTERED</code>/<code>LOCAL_MISS</code>
              decisions are byte-identical to production.
            </p>

            <div class="tester-frame-grid">
              <!-- Left: system state (draft sources editor) -->
              <div class="tester-frame-col">
                <h3 class="tester-section-title">
                  System state — draft sources
                  <span class="tester-section-hint">
                    ({{ draftSources.length }} source{{ draftSources.length === 1 ? "" : "s" }})
                  </span>
                </h3>
                <div class="tester-empty" v-if="draftSources.length === 0">
                  Draft is empty. Click
                  <strong>Load from System</strong> above to seed it with
                  the current configuration, or
                  <a href="javascript:void(0)" @click="addDraftSource">add a source</a>
                  manually.
                </div>
                <div
                  v-else
                  class="draft-list"
                  data-testid="draft-list"
                >
                  <div
                    v-for="(src, idx) in draftSources"
                    :key="idx"
                    class="draft-row"
                    :data-testid="`draft-row-${idx}`"
                  >
                    <div class="draft-row-grid">
                      <el-form-item label="Name">
                        <el-input
                          v-model="src.name"
                          @input="_markDirty"
                          placeholder="HuggingFace"
                        />
                      </el-form-item>
                      <el-form-item label="URL">
                        <el-input
                          v-model="src.url"
                          @input="_markDirty"
                          placeholder="https://huggingface.co"
                        />
                      </el-form-item>
                      <el-form-item label="Type">
                        <el-select
                          v-model="src.source_type"
                          @change="_markDirty"
                          style="width: 100%"
                        >
                          <el-option
                            v-for="st in SOURCE_TYPES"
                            :key="st"
                            :label="st"
                            :value="st"
                          />
                        </el-select>
                      </el-form-item>
                      <el-form-item label="Priority">
                        <el-input-number
                          v-model="src.priority"
                          :min="1"
                          :max="1000"
                          @change="_markDirty"
                          style="width: 100%"
                        />
                      </el-form-item>
                      <el-form-item label="Namespace">
                        <el-input
                          v-model="src.namespace"
                          @input="_markDirty"
                          placeholder="(empty for global)"
                        />
                      </el-form-item>
                      <el-form-item label="Token">
                        <el-input
                          v-model="src.token"
                          @input="_markDirty"
                          type="password"
                          placeholder="hf_xxx (optional)"
                          show-password
                        />
                      </el-form-item>
                      <el-form-item label="Enabled">
                        <el-switch v-model="src.enabled" @change="_markDirty" />
                      </el-form-item>
                    </div>
                    <div class="draft-row-actions">
                      <el-button
                        size="small"
                        type="danger"
                        @click="removeDraftSource(idx)"
                        :data-testid="`draft-remove-${idx}`"
                      >
                        <i class="i-carbon-trash-can"></i>
                      </el-button>
                    </div>
                  </div>
                </div>
                <div class="draft-add-row">
                  <el-button
                    size="small"
                    @click="addDraftSource"
                    data-testid="draft-add-btn"
                  >
                    <i class="i-carbon-add mr-1"></i>
                    Add Draft Source
                  </el-button>
                </div>
                <!-- Draft lifecycle actions, scoped to the system-state
                     column so they live next to what they operate on. -->
                <div class="draft-lifecycle-row">
                  <el-button
                    size="small"
                    @click="loadDraftFromSystem"
                    :loading="loading"
                    data-testid="load-from-system-btn"
                  >
                    <i class="i-carbon-download mr-1"></i>
                    Load from System
                  </el-button>
                  <el-button
                    type="primary"
                    size="small"
                    :disabled="!draftDirty || draftSources.length === 0"
                    @click="pushDraftToSystem"
                    :loading="loading"
                    data-testid="push-to-system-btn"
                  >
                    <i class="i-carbon-upload mr-1"></i>
                    Push to System
                  </el-button>
                  <el-button
                    size="small"
                    :disabled="!draftDirty && draftSources.length === 0"
                    @click="discardDraft"
                    data-testid="discard-draft-btn"
                  >
                    Discard Draft
                  </el-button>
                </div>
              </div>

              <!-- Right: user state (impersonation + token overlay) -->
              <div class="tester-frame-col">
                <h3 class="tester-section-title">User state — identity</h3>
                <p class="tester-section-hint">
                  Impersonate which user the probe runs as. Anonymous
                  is a public caller; by-username / by-user_id load
                  that user's
                  <code>UserExternalToken</code>
                  rows from the DB so per-user tokens enter the chain
                  exactly as they would in production.
                </p>
                <el-form label-width="120px" class="tester-form">
                  <el-form-item label="Identity">
                    <el-radio-group
                      v-model="simIdentity.mode"
                      data-testid="sim-identity-mode"
                    >
                      <el-radio value="anonymous">Anonymous</el-radio>
                      <el-radio value="username">By username</el-radio>
                      <el-radio value="user_id">By user_id</el-radio>
                    </el-radio-group>
                  </el-form-item>
                  <el-form-item v-if="simIdentity.mode === 'username'" label="Username">
                    <el-input
                      v-model="simIdentity.username"
                      placeholder="e.g. mai_lin"
                      data-testid="sim-identity-username"
                    />
                  </el-form-item>
                  <el-form-item v-if="simIdentity.mode === 'user_id'" label="User ID">
                    <el-input-number
                      v-model="simIdentity.user_id"
                      :min="1"
                      style="width: 100%"
                      data-testid="sim-identity-userid"
                    />
                  </el-form-item>
                </el-form>

                <h4 class="tester-subsection-title">
                  Per-URL token overrides
                  <span class="tester-section-hint">
                    (Authorization-header-style overrides applied on
                    top of the impersonated user's DB tokens — header
                    wins on URL collision, mirroring production's
                    <code>get_merged_external_tokens</code>)
                  </span>
                </h4>
                <div
                  v-if="simHeaderTokens.length === 0"
                  class="tester-empty"
                >
                  No header overrides.
                  <a href="javascript:void(0)" @click="addSimHeaderToken">Add one</a>.
                </div>
                <div v-else class="header-tokens" data-testid="sim-header-tokens">
                  <div
                    v-for="(row, idx) in simHeaderTokens"
                    :key="idx"
                    class="header-token-row"
                  >
                    <el-input
                      v-model="row.url"
                      placeholder="https://huggingface.co"
                      :data-testid="`sim-header-token-url-${idx}`"
                      style="flex: 2"
                    />
                    <el-input
                      v-model="row.token"
                      type="password"
                      placeholder="hf_xxx"
                      show-password
                      :data-testid="`sim-header-token-token-${idx}`"
                      style="flex: 2"
                    />
                    <el-button
                      size="small"
                      type="danger"
                      @click="removeSimHeaderToken(idx)"
                      :data-testid="`sim-header-token-remove-${idx}`"
                    >
                      <i class="i-carbon-trash-can"></i>
                    </el-button>
                  </div>
                </div>
                <div class="draft-add-row">
                  <el-button
                    size="small"
                    @click="addSimHeaderToken"
                    data-testid="sim-header-token-add"
                  >
                    <i class="i-carbon-add mr-1"></i>
                    Add header override
                  </el-button>
                </div>
              </div>
            </div>

            <!-- Run + results (simulate) -->
            <h3 class="tester-section-title">Run simulate</h3>
            <div class="run-buttons">
              <el-button
                type="primary"
                @click="runSimulate"
                :loading="simRunning"
                data-testid="run-simulate-btn"
              >
                <i class="i-carbon-play-filled mr-1"></i>
                Run simulate against draft
              </el-button>
            </div>

            <div
              v-if="simError"
              class="probe-error"
              data-testid="sim-probe-error"
            >
              {{ simError }}
            </div>

            <ProbeReportView
              v-if="simReport"
              :report="simReport"
              data-testid-prefix="sim-probe"
              :decision-tag-type="decisionTagType"
            />
          </el-tab-pane>

          <!-- ===== Tab 2: Live real probe ===== -->
          <el-tab-pane name="real" data-testid="tester-tab-real">
            <template #label>
              <span data-testid="tester-tab-real-label">
                <i class="i-carbon-cloud-satellite mr-1"></i>
                Live real probe
              </span>
            </template>

            <p class="tester-section-hint">
              Send a real HTTP request from this browser to the live
              KohakuHub instance — same handler chain a production
              <code>huggingface_hub</code>
              client would hit. The chain (local hop first, then any
              fallback hops walked) is reconstructed from the
              <code>X-Chain-Trace</code>
              response header. Use this when simulate says "OK" but
              you still want to confirm the live config is actually
              wired up correctly — the simulate path can't catch a
              broken cache, a misconfigured nginx hop, etc.
            </p>

            <div class="tester-frame-grid">
              <!-- Left: system state (live config, read-only) -->
              <div class="tester-frame-col">
                <h3 class="tester-section-title">
                  System state — live config
                  <span class="tester-section-hint">
                    (read-only summary of the {{ sources.length }}
                    currently-applied source{{ sources.length === 1 ? "" : "s" }})
                  </span>
                </h3>
                <div class="tester-empty" v-if="sources.length === 0">
                  No sources currently applied. Configure them in the
                  <strong>Configured Sources</strong>
                  card below, or stage a draft above and
                  <strong>Push to System</strong>.
                </div>
                <div
                  v-else
                  class="live-config-list"
                  data-testid="live-config-list"
                >
                  <div
                    v-for="src in sources"
                    :key="src.id"
                    class="live-config-row"
                    :data-testid="`live-config-row-${src.id}`"
                  >
                    <div class="live-config-name">
                      <strong>{{ src.name }}</strong>
                      <el-tag
                        :type="src.source_type === 'huggingface' ? 'primary' : 'success'"
                        size="small"
                      >
                        {{ src.source_type }}
                      </el-tag>
                      <el-tag
                        :type="src.enabled ? 'success' : 'info'"
                        size="small"
                      >
                        {{ src.enabled ? "enabled" : "disabled" }}
                      </el-tag>
                      <el-tag v-if="src.namespace" type="warning" size="small">
                        {{ src.namespace }}
                      </el-tag>
                    </div>
                    <div class="live-config-url">
                      <code>{{ src.url }}</code>
                      <span class="live-config-priority">
                        priority {{ src.priority }}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Right: user state (admin's khub token + per-URL tokens) -->
              <div class="tester-frame-col">
                <h3 class="tester-section-title">User state — Authorization</h3>
                <p class="tester-section-hint">
                  The probe sends a real request to this instance with
                  these credentials encoded into a single
                  <code>Authorization</code>
                  header in the
                  <code>Bearer khub_xxx|url1,token1|...</code>
                  shape. Admin can't impersonate other users in this
                  mode — paste the user-in-question's
                  <em>own</em>
                  KohakuHub access token here when debugging
                  user-specific issues. Leave blank to send anonymously.
                </p>
                <el-form label-width="160px" class="tester-form">
                  <el-form-item label="KohakuHub token">
                    <el-input
                      v-model="realKhubToken"
                      type="password"
                      placeholder="khub_xxx (optional)"
                      show-password
                      data-testid="real-khub-token"
                    />
                  </el-form-item>
                </el-form>
                <h4 class="tester-subsection-title">
                  Per-URL fallback tokens
                  <span class="tester-section-hint">
                    (encoded into the
                    <code>|url,token|...</code>
                    segments of the Authorization header)
                  </span>
                </h4>
                <div
                  v-if="realHeaderTokens.length === 0"
                  class="tester-empty"
                >
                  No header overrides.
                  <a href="javascript:void(0)" @click="addRealHeaderToken">Add one</a>.
                </div>
                <div v-else class="header-tokens" data-testid="real-header-tokens">
                  <div
                    v-for="(row, idx) in realHeaderTokens"
                    :key="idx"
                    class="header-token-row"
                  >
                    <el-input
                      v-model="row.url"
                      placeholder="https://huggingface.co"
                      :data-testid="`real-header-token-url-${idx}`"
                      style="flex: 2"
                    />
                    <el-input
                      v-model="row.token"
                      type="password"
                      placeholder="hf_xxx"
                      show-password
                      :data-testid="`real-header-token-token-${idx}`"
                      style="flex: 2"
                    />
                    <el-button
                      size="small"
                      type="danger"
                      @click="removeRealHeaderToken(idx)"
                      :data-testid="`real-header-token-remove-${idx}`"
                    >
                      <i class="i-carbon-trash-can"></i>
                    </el-button>
                  </div>
                </div>
                <div class="draft-add-row">
                  <el-button
                    size="small"
                    @click="addRealHeaderToken"
                    data-testid="real-header-token-add"
                  >
                    <i class="i-carbon-add mr-1"></i>
                    Add header override
                  </el-button>
                </div>
              </div>
            </div>

            <!-- Run + results (real) -->
            <h3 class="tester-section-title">Run real request</h3>
            <div class="run-buttons">
              <el-button
                type="primary"
                @click="runRealRequest"
                :loading="realRunning"
                data-testid="run-real-btn"
              >
                <i class="i-carbon-play-filled mr-1"></i>
                Run real request
              </el-button>
            </div>

            <div
              v-if="realError"
              class="probe-error"
              data-testid="real-probe-error"
            >
              {{ realError }}
            </div>

            <ProbeReportView
              v-if="realReport"
              :report="realReport"
              data-testid-prefix="real-probe"
              :decision-tag-type="decisionTagType"
            />
          </el-tab-pane>
        </el-tabs>
      </el-card>

      <!-- Sources List Card -->
      <el-card class="sources-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <span>Configured Sources</span>
            <el-button
              type="primary"
              size="small"
              @click="openCreateDialog"
              :loading="loading"
            >
              <i class="i-carbon-add mr-1"></i>
              Add Source
            </el-button>
          </div>
        </template>

        <div v-if="loading && sources.length === 0" class="empty-state">
          <i class="i-carbon-loading animate-spin text-4xl"></i>
          <p>Loading...</p>
        </div>

        <div v-else-if="sources.length === 0" class="empty-state">
          <i class="i-carbon-cloud-offline text-6xl opacity-30"></i>
          <p>No fallback sources configured</p>
          <p class="text-sm opacity-60">
            Add a source to enable fallback to HuggingFace or other hubs
          </p>
        </div>

        <div v-else class="sources-list">
          <div
            v-for="source in sources"
            :key="source.id"
            class="source-item"
            :class="{ disabled: !source.enabled }"
          >
            <div class="source-header">
              <div class="source-title">
                <h3>{{ source.name }}</h3>
                <div class="badges">
                  <el-tag
                    :type="
                      source.source_type === 'huggingface'
                        ? 'primary'
                        : 'success'
                    "
                    size="small"
                  >
                    {{ source.source_type }}
                  </el-tag>
                  <el-tag
                    :type="source.enabled ? 'success' : 'info'"
                    size="small"
                  >
                    {{ source.enabled ? "Enabled" : "Disabled" }}
                  </el-tag>
                  <el-tag v-if="source.namespace" type="warning" size="small">
                    {{ source.namespace }}
                  </el-tag>
                </div>
              </div>
              <div class="source-actions">
                <el-button
                  :type="source.enabled ? 'default' : 'success'"
                  size="small"
                  @click="handleToggleEnabled(source)"
                  :loading="loading"
                >
                  {{ source.enabled ? "Disable" : "Enable" }}
                </el-button>
                <el-button
                  type="primary"
                  size="small"
                  @click="openEditDialog(source)"
                  :loading="loading"
                >
                  <i class="i-carbon-edit"></i>
                </el-button>
                <el-button
                  type="danger"
                  size="small"
                  @click="handleDelete(source)"
                  :loading="loading"
                >
                  <i class="i-carbon-trash-can"></i>
                </el-button>
              </div>
            </div>

            <div class="source-details">
              <div class="detail-row">
                <span class="detail-label">URL:</span>
                <code>{{ source.url }}</code>
              </div>
              <div class="detail-row">
                <span class="detail-label">Priority:</span>
                <span>{{ source.priority }} (lower = higher priority)</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Namespace:</span>
                <span>{{ source.namespace || "(global)" }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Created:</span>
                <span>{{ formatDate(source.created_at) }}</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Create/Edit Dialog -->
      <el-dialog
        v-model="dialogVisible"
        :title="
          dialogMode === 'create'
            ? 'Create Fallback Source'
            : 'Edit Fallback Source'
        "
        width="600px"
      >
        <el-form :model="formData" label-width="120px">
          <el-form-item label="Name" required>
            <el-input v-model="formData.name" placeholder="HuggingFace" />
          </el-form-item>

          <el-form-item label="URL" required>
            <el-input
              v-model="formData.url"
              placeholder="https://huggingface.co"
            />
          </el-form-item>

          <el-form-item label="Source Type" required>
            <el-select v-model="formData.source_type" style="width: 100%">
              <el-option label="HuggingFace" value="huggingface" />
              <el-option label="KohakuHub" value="kohakuhub" />
            </el-select>
          </el-form-item>

          <el-form-item label="Token">
            <el-input
              v-model="formData.token"
              type="password"
              placeholder="Optional API token (hf_xxx...)"
              show-password
            />
            <div class="form-help">
              Admin-configured token for accessing private repos
            </div>
          </el-form-item>

          <el-form-item label="Priority" required>
            <el-input-number
              v-model="formData.priority"
              :min="1"
              :max="1000"
              style="width: 100%"
            />
            <div class="form-help">
              Lower values = higher priority (checked first)
            </div>
          </el-form-item>

          <el-form-item label="Namespace">
            <el-input
              v-model="formData.namespace"
              placeholder="(empty for global)"
            />
            <div class="form-help">
              Empty = global, or specify user/org name
            </div>
          </el-form-item>

          <el-form-item label="Enabled">
            <el-switch v-model="formData.enabled" />
          </el-form-item>
        </el-form>

        <template #footer>
          <el-button @click="dialogVisible = false">Cancel</el-button>
          <el-button type="primary" @click="handleSubmit" :loading="loading">
            {{ dialogMode === "create" ? "Create" : "Update" }}
          </el-button>
        </template>
      </el-dialog>

      <!-- Per-repo cache eviction dialog (#79) -->
      <el-dialog
        v-model="evictRepoDialogVisible"
        title="Evict Fallback Cache by Repo"
        width="540px"
        data-testid="evict-repo-dialog"
      >
        <p class="dialog-help">
          Drop every cached source binding for one repository, across all
          users. Useful when you know an upstream repo's state changed and
          you don't want to wait for the TTL.
        </p>
        <el-form :model="evictRepoForm" label-width="120px">
          <el-form-item label="Repo type" required>
            <el-select
              v-model="evictRepoForm.repo_type"
              style="width: 100%"
              data-testid="evict-repo-type"
            >
              <el-option label="model" value="model" />
              <el-option label="dataset" value="dataset" />
              <el-option label="space" value="space" />
            </el-select>
          </el-form-item>
          <el-form-item label="Namespace" required>
            <el-autocomplete
              v-model="evictRepoForm.namespace"
              :fetch-suggestions="fetchNamespaceSuggestions"
              placeholder="owner / org name"
              clearable
              style="width: 100%"
              data-testid="evict-repo-namespace"
            />
          </el-form-item>
          <el-form-item label="Name" required>
            <el-autocomplete
              v-model="evictRepoForm.name"
              :fetch-suggestions="fetchRepoNameSuggestions"
              placeholder="repo name"
              clearable
              style="width: 100%"
              data-testid="evict-repo-name"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="evictRepoDialogVisible = false">Cancel</el-button>
          <el-button
            type="warning"
            @click="handleEvictRepo"
            :loading="loading"
            data-testid="evict-repo-submit"
          >
            Evict
          </el-button>
        </template>
      </el-dialog>

      <!-- Per-user cache eviction dialog (#79) -->
      <el-dialog
        v-model="evictUserDialogVisible"
        title="Evict Fallback Cache by User"
        width="540px"
        data-testid="evict-user-dialog"
      >
        <p class="dialog-help">
          Drop every cached source binding belonging to one user (across
          every repo they have a binding for). The next request from that
          user re-probes the chain. Address by username (resolved
          server-side) or numeric user_id (script-friendly).
        </p>
        <el-form :model="evictUserForm" label-width="120px">
          <el-form-item label="Mode" required>
            <el-radio-group
              v-model="evictUserForm.mode"
              data-testid="evict-user-mode"
            >
              <el-radio value="username">By username</el-radio>
              <el-radio value="user_id">By user_id</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item
            v-if="evictUserForm.mode === 'username'"
            label="Username"
            required
          >
            <el-autocomplete
              v-model="evictUserForm.username"
              :fetch-suggestions="fetchUsernameSuggestions"
              placeholder="e.g. mai_lin"
              clearable
              style="width: 100%"
              data-testid="evict-user-username"
            />
          </el-form-item>
          <el-form-item v-else label="User ID" required>
            <el-input-number
              v-model="evictUserForm.user_id"
              :min="1"
              style="width: 100%"
              data-testid="evict-user-userid"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="evictUserDialogVisible = false">Cancel</el-button>
          <el-button
            type="warning"
            @click="handleEvictUser"
            :loading="loading"
            data-testid="evict-user-submit"
          >
            Evict...
          </el-button>
        </template>
      </el-dialog>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1280px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 8px;
}

.page-header p {
  color: var(--el-text-color-secondary);
  margin: 0;
}

.stats-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.cache-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.dialog-help {
  margin: 0 0 16px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
}

.stat-item {
  text-align: center;
}

.stat-label {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 600;
  color: var(--el-color-primary);
}

.sources-card {
  margin-bottom: 20px;
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--el-text-color-secondary);
}

.empty-state i {
  display: block;
  margin-bottom: 16px;
}

.sources-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.source-item {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 16px;
  transition: all 0.3s;
}

.source-item:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.source-item.disabled {
  opacity: 0.5;
}

.source-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.source-title h3 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 8px 0;
}

.badges {
  display: flex;
  gap: 8px;
}

.source-actions {
  display: flex;
  gap: 8px;
}

.source-details {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-row {
  display: flex;
  align-items: center;
  font-size: 14px;
}

.detail-label {
  font-weight: 600;
  min-width: 100px;
  color: var(--el-text-color-secondary);
}

.detail-row code {
  background: var(--el-fill-color-light);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: "Consolas", "Monaco", monospace;
}

.form-help {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

/* Chain Tester (#78) */
.tester-card {
  margin-bottom: 20px;
}

.tester-help {
  margin: 0 0 16px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.tester-section-title {
  font-size: 16px;
  font-weight: 600;
  margin: 24px 0 12px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.tester-subsection-title {
  font-size: 14px;
  font-weight: 500;
  margin: 16px 0 8px 0;
  color: var(--el-text-color-secondary);
}

.tester-section-hint {
  font-weight: 400;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.tester-form {
  margin-bottom: 16px;
}

.tester-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 24px;
}

/* Two-frame layout per tester tab: left = system state, right = user
   state. Stacks on narrow viewports so the form items stay legible. */
.tester-tabs {
  margin-top: 8px;
}

.tester-frame-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0 24px;
  margin-bottom: 16px;
}

@media (max-width: 1024px) {
  .tester-frame-grid {
    grid-template-columns: 1fr;
  }
}

.tester-frame-col {
  min-width: 0;
}

.live-config-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.live-config-row {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-bg-color-page);
}

.live-config-name {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.live-config-url {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}

.live-config-url code {
  font-family: var(--el-font-family-mono, monospace);
  background: var(--el-fill-color, #ececec);
  padding: 1px 6px;
  border-radius: 4px;
}

.live-config-priority {
  margin-left: auto;
  color: var(--el-text-color-secondary, #909399);
  font-size: 11px;
}

.tester-empty {
  padding: 12px 16px;
  border: 1px dashed var(--el-border-color);
  border-radius: 4px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-bottom: 12px;
}

.draft-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 12px;
}

.draft-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-bg-color-page);
}

.draft-row-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 16px;
  flex: 1;
}

.draft-row-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 4px;
}

.draft-add-row {
  margin-bottom: 12px;
}

/* Draft lifecycle actions (Load / Push / Discard) — moved out of the
   Chain Tester card header into the simulate tab's system-state
   column so they sit next to the draft they operate on. Spacing
   mirrors ``.draft-add-row`` so the column reads cleanly. */
.draft-lifecycle-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  margin-bottom: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
}

.header-tokens {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.header-token-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.run-buttons {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.probe-error {
  padding: 12px 16px;
  border: 1px solid var(--el-color-danger);
  border-radius: 4px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 13px;
  margin-bottom: 12px;
}

.probe-report {
  border-top: 1px solid var(--el-border-color);
  padding-top: 16px;
}

.probe-report-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  font-size: 14px;
}

.probe-bound-source {
  color: var(--el-text-color-secondary);
}

.probe-duration {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: auto;
}

.probe-attempts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.probe-attempt {
  padding: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  background: var(--el-bg-color-page);
}

.probe-attempt-line {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
}

.probe-attempt-source {
  font-weight: 600;
}

.probe-attempt-method {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.probe-attempt-status {
  font-family: monospace;
  font-size: 13px;
}

.probe-attempt-xerror {
  font-size: 12px;
  color: var(--el-color-warning);
}

.probe-attempt-ms {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.probe-attempt-path,
.probe-attempt-headers {
  margin-top: 8px;
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
}

.probe-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-right: 4px;
}

.probe-attempt-header {
  background: var(--el-fill-color-light);
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 11px;
}

.probe-attempt-body {
  margin-top: 8px;
  font-size: 12px;
}

.probe-attempt-body pre {
  margin: 6px 0 0 0;
  padding: 8px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  max-height: 320px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.probe-attempt-error-detail {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-danger);
}

.probe-final-response {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--el-color-success);
  border-radius: 4px;
  background: var(--el-color-success-light-9);
}

.probe-final-title {
  margin: 0 0 8px 0;
  font-size: 14px;
  font-weight: 600;
}

.probe-final-status {
  font-size: 13px;
  margin-bottom: 6px;
}

.probe-final-headers {
  margin-bottom: 6px;
  font-size: 12px;
}
</style>
