<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import AdminLayout from "@/components/AdminLayout.vue";
import { useAdminStore } from "@/stores/admin";
import {
  listAdminSessions,
  listAdminSshKeys,
  listAdminTokens,
  revokeAdminSession,
  revokeAdminSessionsBulk,
  revokeAdminSshKey,
  revokeAdminToken,
} from "@/utils/api";
import {
  confirmDialog,
  showError,
  showSuccess,
  showWarning,
} from "@/utils/dialogs";
import dayjs from "dayjs";

const router = useRouter();
const adminStore = useAdminStore();

const PAGE_SIZE = 20;

const activeTab = ref("sessions");

const userFilter = ref("");
const unusedForDaysFilter = ref(null);
const onlyActiveSessions = ref(false);

const sessions = ref([]);
const tokens = ref([]);
const sshKeys = ref([]);

const sessionsTotal = ref(0);
const tokensTotal = ref(0);
const sshKeysTotal = ref(0);

const sessionsPage = ref(1);
const tokensPage = ref(1);
const sshKeysPage = ref(1);

const loading = ref(false);

const bulkRevokeForm = ref({ user: "", beforeTs: "" });
const bulkRevokeOpen = ref(false);

function checkAuth() {
  if (!adminStore.token) {
    router.push("/login");
    return false;
  }
  return true;
}

function formatDate(value) {
  if (!value) return "—";
  return dayjs(value).format("YYYY-MM-DD HH:mm:ss");
}

function handleApiError(error, fallback) {
  if (error.response?.status === 401 || error.response?.status === 403) {
    showError("Invalid admin token. Please login again.");
    adminStore.logout();
    router.push("/login");
    return;
  }
  showError(
    error.response?.data?.detail?.error || error.message || fallback,
  );
}

async function loadSessions() {
  if (!checkAuth()) return;
  loading.value = true;
  try {
    const data = await listAdminSessions(adminStore.token, {
      user: userFilter.value || undefined,
      activeOnly: onlyActiveSessions.value || undefined,
      limit: PAGE_SIZE,
      offset: (sessionsPage.value - 1) * PAGE_SIZE,
    });
    sessions.value = data.sessions;
    sessionsTotal.value = data.total;
  } catch (error) {
    handleApiError(error, "Failed to load sessions");
  } finally {
    loading.value = false;
  }
}

async function loadTokens() {
  if (!checkAuth()) return;
  loading.value = true;
  try {
    const data = await listAdminTokens(adminStore.token, {
      user: userFilter.value || undefined,
      unusedForDays:
        unusedForDaysFilter.value === null ||
        unusedForDaysFilter.value === ""
          ? undefined
          : Number(unusedForDaysFilter.value),
      limit: PAGE_SIZE,
      offset: (tokensPage.value - 1) * PAGE_SIZE,
    });
    tokens.value = data.tokens;
    tokensTotal.value = data.total;
  } catch (error) {
    handleApiError(error, "Failed to load API tokens");
  } finally {
    loading.value = false;
  }
}

async function loadSshKeys() {
  if (!checkAuth()) return;
  loading.value = true;
  try {
    const data = await listAdminSshKeys(adminStore.token, {
      user: userFilter.value || undefined,
      unusedForDays:
        unusedForDaysFilter.value === null ||
        unusedForDaysFilter.value === ""
          ? undefined
          : Number(unusedForDaysFilter.value),
      limit: PAGE_SIZE,
      offset: (sshKeysPage.value - 1) * PAGE_SIZE,
    });
    sshKeys.value = data.ssh_keys;
    sshKeysTotal.value = data.total;
  } catch (error) {
    handleApiError(error, "Failed to load SSH keys");
  } finally {
    loading.value = false;
  }
}

async function loadActiveTab() {
  if (activeTab.value === "sessions") return loadSessions();
  if (activeTab.value === "tokens") return loadTokens();
  if (activeTab.value === "ssh-keys") return loadSshKeys();
}

watch(activeTab, () => {
  sessionsPage.value = 1;
  tokensPage.value = 1;
  sshKeysPage.value = 1;
  loadActiveTab();
});

async function confirmAndRevoke({
  title,
  message,
  perform,
  reload,
}) {
  try {
    await confirmDialog(title, message, { confirmText: "Revoke" });
  } catch {
    return;
  }
  try {
    const result = await perform();
    showSuccess(`Revoked ${result.revoked}`);
    await reload();
  } catch (error) {
    handleApiError(error, "Revoke failed");
  }
}

function revokeSession(row) {
  return confirmAndRevoke({
    title: "Revoke session",
    message: `Revoke session #${row.id} for ${row.username}? Any active client using this session will get 401 on its next request.`,
    perform: () => revokeAdminSession(adminStore.token, row.id),
    reload: loadSessions,
  });
}

function revokeToken(row) {
  return confirmAndRevoke({
    title: "Revoke API token",
    message: `Revoke token "${row.name}" (#${row.id}) for ${row.username}? CI clients using this token will start failing immediately.`,
    perform: () => revokeAdminToken(adminStore.token, row.id),
    reload: loadTokens,
  });
}

function revokeSshKey(row) {
  return confirmAndRevoke({
    title: "Revoke SSH key",
    message: `Revoke SSH key "${row.title}" (${row.fingerprint}) for ${row.username}?`,
    perform: () => revokeAdminSshKey(adminStore.token, row.id),
    reload: loadSshKeys,
  });
}

async function submitBulkRevoke() {
  const body = {};
  if (bulkRevokeForm.value.user) body.user = bulkRevokeForm.value.user;
  if (bulkRevokeForm.value.beforeTs) body.before_ts = bulkRevokeForm.value.beforeTs;
  if (!body.user && !body.before_ts) {
    showWarning("Provide at least one filter (user or before_ts).");
    return;
  }
  try {
    await confirmDialog(
      "Bulk revoke",
      `Bulk-revoke sessions matching ${JSON.stringify(body)}? This is irreversible.`,
      { confirmText: "Revoke all matching" },
    );
  } catch {
    return;
  }
  try {
    const result = await revokeAdminSessionsBulk(adminStore.token, body);
    showSuccess(`Bulk revoked ${result.revoked} session(s)`);
    bulkRevokeOpen.value = false;
    bulkRevokeForm.value = { user: "", beforeTs: "" };
    await loadSessions();
  } catch (error) {
    handleApiError(error, "Bulk revoke failed");
  }
}

const showUnusedFilter = computed(
  () => activeTab.value === "tokens" || activeTab.value === "ssh-keys",
);

onMounted(() => {
  loadActiveTab();
});
</script>

<template>
  <AdminLayout>
    <div class="page-container">
      <div class="flex justify-between items-center mb-6 gap-4 flex-wrap">
        <div>
          <h1 class="text-3xl font-bold text-gray-900 dark:text-gray-100">
            Credentials
          </h1>
          <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">
            Sessions, API tokens and SSH keys across every user. Use this view
            to investigate a leak, clean up after an offboarding, or kill a
            misbehaving CI client.
          </p>
        </div>
        <el-button
          v-if="activeTab === 'sessions'"
          type="warning"
          @click="bulkRevokeOpen = true"
          data-testid="credentials-open-bulk"
        >
          <div class="i-carbon-trash-can mr-1" />
          Bulk revoke
        </el-button>
      </div>

      <el-card shadow="never" class="mb-4">
        <div class="flex items-center gap-3 flex-wrap">
          <el-input
            v-model="userFilter"
            placeholder="Filter by username..."
            clearable
            style="max-width: 240px"
            @keyup.enter="loadActiveTab"
            data-testid="credentials-user-filter"
          />
          <el-input-number
            v-if="showUnusedFilter"
            v-model="unusedForDaysFilter"
            :min="0"
            placeholder="Unused for N+ days"
            controls-position="right"
            style="width: 200px"
            data-testid="credentials-unused-filter"
          />
          <el-checkbox
            v-if="activeTab === 'sessions'"
            v-model="onlyActiveSessions"
            data-testid="credentials-active-only"
          >
            Only active (not expired)
          </el-checkbox>
          <el-button
            type="primary"
            :loading="loading"
            @click="loadActiveTab"
            data-testid="credentials-apply"
          >
            Apply
          </el-button>
        </div>
      </el-card>

      <el-tabs v-model="activeTab" data-testid="credentials-tabs">
        <el-tab-pane label="Sessions" name="sessions">
          <el-table
            v-loading="loading"
            :data="sessions"
            stripe
            data-testid="credentials-sessions-table"
          >
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="username" label="User" width="160" />
            <el-table-column label="Created" width="200">
              <template #default="{ row }">
                {{ formatDate(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="Expires" width="200">
              <template #default="{ row }">
                {{ formatDate(row.expires_at) }}
              </template>
            </el-table-column>
            <el-table-column label="Status" width="120">
              <template #default="{ row }">
                <el-tag :type="row.expired ? 'info' : 'success'">
                  {{ row.expired ? "Expired" : "Active" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Actions">
              <template #default="{ row }">
                <el-button
                  type="danger"
                  size="small"
                  @click="revokeSession(row)"
                  data-testid="credentials-revoke-session"
                >
                  Revoke
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="sessionsTotal > PAGE_SIZE"
            v-model:current-page="sessionsPage"
            :page-size="PAGE_SIZE"
            :total="sessionsTotal"
            layout="prev, pager, next, total"
            class="mt-4"
            @current-change="loadSessions"
          />
        </el-tab-pane>

        <el-tab-pane label="API Tokens" name="tokens">
          <el-table
            v-loading="loading"
            :data="tokens"
            stripe
            data-testid="credentials-tokens-table"
          >
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="username" label="User" width="160" />
            <el-table-column prop="name" label="Name" />
            <el-table-column label="Created" width="200">
              <template #default="{ row }">
                {{ formatDate(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="Last used" width="200">
              <template #default="{ row }">
                {{ formatDate(row.last_used) }}
              </template>
            </el-table-column>
            <el-table-column label="Actions" width="140">
              <template #default="{ row }">
                <el-button
                  type="danger"
                  size="small"
                  @click="revokeToken(row)"
                  data-testid="credentials-revoke-token"
                >
                  Revoke
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="tokensTotal > PAGE_SIZE"
            v-model:current-page="tokensPage"
            :page-size="PAGE_SIZE"
            :total="tokensTotal"
            layout="prev, pager, next, total"
            class="mt-4"
            @current-change="loadTokens"
          />
        </el-tab-pane>

        <el-tab-pane label="SSH Keys" name="ssh-keys">
          <el-table
            v-loading="loading"
            :data="sshKeys"
            stripe
            data-testid="credentials-ssh-keys-table"
          >
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="username" label="User" width="160" />
            <el-table-column prop="title" label="Title" />
            <el-table-column prop="key_type" label="Type" width="140" />
            <el-table-column label="Fingerprint">
              <template #default="{ row }">
                <code class="fingerprint">{{ row.fingerprint }}</code>
              </template>
            </el-table-column>
            <el-table-column label="Created" width="200">
              <template #default="{ row }">
                {{ formatDate(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="Last used" width="200">
              <template #default="{ row }">
                {{ formatDate(row.last_used) }}
              </template>
            </el-table-column>
            <el-table-column label="Actions" width="140">
              <template #default="{ row }">
                <el-button
                  type="danger"
                  size="small"
                  @click="revokeSshKey(row)"
                  data-testid="credentials-revoke-ssh-key"
                >
                  Revoke
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="sshKeysTotal > PAGE_SIZE"
            v-model:current-page="sshKeysPage"
            :page-size="PAGE_SIZE"
            :total="sshKeysTotal"
            layout="prev, pager, next, total"
            class="mt-4"
            @current-change="loadSshKeys"
          />
        </el-tab-pane>
      </el-tabs>

      <el-dialog
        v-model="bulkRevokeOpen"
        title="Bulk revoke sessions"
        width="500"
      >
        <el-form :model="bulkRevokeForm" label-position="top">
          <el-form-item label="Username (optional)">
            <el-input
              v-model="bulkRevokeForm.user"
              placeholder="e.g. former-employee"
              data-testid="credentials-bulk-user"
            />
          </el-form-item>
          <el-form-item label="Created strictly before (ISO timestamp, optional)">
            <el-input
              v-model="bulkRevokeForm.beforeTs"
              placeholder="e.g. 2026-01-01T00:00:00+00:00"
              data-testid="credentials-bulk-before"
            />
          </el-form-item>
          <p class="text-gray-500 dark:text-gray-400 text-sm">
            Provide at least one filter. The action is irreversible — clients
            will need to log in again.
          </p>
        </el-form>
        <template #footer>
          <el-button @click="bulkRevokeOpen = false">Cancel</el-button>
          <el-button
            type="warning"
            @click="submitBulkRevoke"
            data-testid="credentials-bulk-submit"
          >
            Revoke matching sessions
          </el-button>
        </template>
      </el-dialog>
    </div>
  </AdminLayout>
</template>

<style scoped>
.fingerprint {
  font-size: 12px;
  word-break: break-all;
  color: var(--el-text-color-secondary);
}
</style>
