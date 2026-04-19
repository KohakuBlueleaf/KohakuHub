<!-- src/pages/[type]s/[namespace]/[name]/settings.vue -->
<template>
  <div class="container-main">
    <!-- Breadcrumb Navigation -->
    <el-breadcrumb separator="/" class="mb-6 text-gray-700 dark:text-gray-300">
      <el-breadcrumb-item>
        <RouterLink
          to="/"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          Home
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>
        <RouterLink
          :to="`/${repoType}s`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{
            repoType === "model"
              ? "Models"
              : repoType === "dataset"
                ? "Datasets"
                : "Spaces"
          }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>
        <RouterLink
          :to="`/${route.params.namespace}`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ route.params.namespace }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>
        <RouterLink
          :to="`/${repoType}s/${route.params.namespace}/${route.params.name}`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ route.params.name }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>Settings</el-breadcrumb-item>
    </el-breadcrumb>

    <h1 class="text-3xl font-bold mb-6">
      Repository Settings: {{ route.params.name }}
    </h1>

    <el-tabs v-model="activeTab">
      <!-- General Settings -->
      <el-tab-pane label="General" name="general">
        <div class="max-w-2xl space-y-6">
          <!-- Visibility -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">Visibility</h2>
            <el-form label-position="top">
              <el-form-item label="Repository Visibility">
                <div class="visibility-options">
                  <div
                    :class="[
                      'visibility-option',
                      { selected: !settings.private },
                    ]"
                    @click="settings.private = false"
                  >
                    <div class="option-radio">
                      <div
                        :class="[
                          'radio-circle',
                          { checked: !settings.private },
                        ]"
                      >
                        <div v-if="!settings.private" class="radio-dot" />
                      </div>
                    </div>
                    <div class="option-icon">
                      <div class="i-carbon-unlock text-xl" />
                    </div>
                    <div class="option-content">
                      <div class="option-title">Public</div>
                      <div class="option-description">
                        Anyone can see this repository
                      </div>
                    </div>
                  </div>

                  <div
                    :class="[
                      'visibility-option',
                      { selected: settings.private },
                    ]"
                    @click="settings.private = true"
                  >
                    <div class="option-radio">
                      <div
                        :class="['radio-circle', { checked: settings.private }]"
                      >
                        <div v-if="settings.private" class="radio-dot" />
                      </div>
                    </div>
                    <div class="option-icon">
                      <div class="i-carbon-locked text-xl" />
                    </div>
                    <div class="option-content">
                      <div class="option-title">Private</div>
                      <div class="option-description">
                        Only you and collaborators can see this repository
                      </div>
                    </div>
                  </div>
                </div>
              </el-form-item>

              <el-button type="primary" @click="saveGeneralSettings">
                Save Changes
              </el-button>
            </el-form>
          </div>

          <!-- Move/Rename Repository -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4 text-warning">
              Move/Rename Repository
            </h2>
            <p class="text-sm text-gray-600 mb-4">
              Moving a repository will redirect the old URL to the new one.
            </p>
            <el-form label-position="top">
              <el-form-item
                label="New Repository ID (namespace/name)"
                :error="
                  moveToRepoValidation.available === false
                    ? moveToRepoValidation.message
                    : undefined
                "
              >
                <el-input
                  v-model="moveToRepo"
                  placeholder="e.g., my-org/my-new-repo"
                  @input="handleMoveToRepoChange"
                >
                  <template #suffix>
                    <el-icon
                      v-if="moveToRepoValidation.checking"
                      class="is-loading"
                    >
                      <div class="i-carbon-circle-dash" />
                    </el-icon>
                    <el-icon
                      v-else-if="moveToRepoValidation.available === true"
                      style="color: #67c23a"
                    >
                      <div class="i-carbon-checkmark" />
                    </el-icon>
                    <el-icon
                      v-else-if="moveToRepoValidation.available === false"
                      style="color: #f56c6c"
                    >
                      <div class="i-carbon-warning" />
                    </el-icon>
                  </template>
                </el-input>
              </el-form-item>
              <el-button
                type="warning"
                @click="handleMoveRepo"
                :disabled="moveToRepoValidation.available !== true"
              >
                Move Repository
              </el-button>
            </el-form>
          </div>

          <!-- Danger Zone -->
          <div class="card border-2 border-red-500">
            <h2 class="text-xl font-semibold mb-4 text-red-600">Danger Zone</h2>
            <div class="space-y-4">
              <div>
                <h3 class="font-medium text-orange-600 mb-2">
                  Squash repository history
                </h3>
                <p class="text-sm text-gray-600 mb-3">
                  This will clear all commit history and optimize storage by
                  removing old versions. Only the current state will be
                  preserved. This action cannot be undone.
                </p>
                <el-button type="warning" @click="handleSquashRepo">
                  Squash Repository
                </el-button>
              </div>

              <div class="pt-4 border-t border-red-300">
                <h3 class="font-medium text-red-600 mb-2">
                  Delete this repository
                </h3>
                <p class="text-sm text-gray-600 mb-3">
                  Once you delete a repository, there is no going back. Please
                  be certain.
                </p>
                <el-button type="danger" @click="handleDeleteRepo">
                  Delete Repository
                </el-button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Branches & Tags -->
      <el-tab-pane label="Branches & Tags" name="branches">
        <div class="max-w-2xl space-y-6">
          <!-- Branches -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">Branches</h2>

            <!-- Create Branch -->
            <div class="mb-6">
              <h3 class="font-medium mb-3">Create New Branch</h3>
              <el-form inline>
                <el-form-item label="Branch Name">
                  <el-input
                    v-model="newBranch.name"
                    placeholder="my-feature-branch"
                  />
                </el-form-item>
                <el-form-item label="From Revision">
                  <el-input
                    v-model="newBranch.revision"
                    placeholder="main (default)"
                  />
                </el-form-item>
                <el-button type="primary" @click="handleCreateBranch">
                  Create Branch
                </el-button>
              </el-form>
            </div>
          </div>

          <!-- Tags -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">Tags</h2>

            <!-- Create Tag -->
            <div class="mb-6">
              <h3 class="font-medium mb-3">Create New Tag</h3>
              <el-form label-position="top">
                <el-form-item label="Tag Name">
                  <el-input v-model="newTag.name" placeholder="v1.0.0" />
                </el-form-item>
                <el-form-item label="From Revision (optional)">
                  <el-input
                    v-model="newTag.revision"
                    placeholder="main (default)"
                  />
                </el-form-item>
                <el-form-item label="Message (optional)">
                  <el-input
                    v-model="newTag.message"
                    type="textarea"
                    placeholder="Release notes..."
                  />
                </el-form-item>
                <el-button type="primary" @click="handleCreateTag">
                  Create Tag
                </el-button>
              </el-form>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- LFS Settings -->
      <el-tab-pane label="LFS Settings" name="lfs">
        <div class="max-w-2xl space-y-6">
          <!-- LFS Threshold -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">LFS Threshold</h2>
            <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
              Files larger than this size will be stored using Git LFS (Large
              File Storage). This setting affects upload performance and storage
              deduplication.
            </p>
            <div v-if="lfsSettings" class="space-y-4">
              <el-form label-position="top">
                <el-form-item label="Threshold Mode">
                  <el-radio-group v-model="lfsSettings.threshold_mode">
                    <div class="space-y-2">
                      <el-radio label="server_default">
                        Use server default
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                          ({{
                            formatSize(
                              lfsSettings.server_defaults.lfs_threshold_bytes,
                            )
                          }})
                        </span>
                      </el-radio>
                      <el-radio label="custom">Custom threshold</el-radio>
                    </div>
                  </el-radio-group>
                </el-form-item>

                <el-form-item
                  v-if="lfsSettings.threshold_mode === 'custom'"
                  label="Custom Threshold (MB)"
                >
                  <el-input-number
                    v-model="lfsSettings.threshold_mb"
                    :min="1"
                    :max="10000"
                    :step="1"
                    :precision="0"
                  />
                  <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    Minimum: 1 MB (recommended: 5-10 MB for ML models)
                  </div>
                </el-form-item>

                <div
                  v-if="lfsSettings.lfs_threshold_bytes_effective"
                  class="text-sm text-gray-600 dark:text-gray-400"
                >
                  <strong>Currently using:</strong>
                  {{ formatSize(lfsSettings.lfs_threshold_bytes_effective) }}
                  ({{
                    lfsSettings.lfs_threshold_bytes_source === "repository"
                      ? "custom"
                      : "server default"
                  }})
                </div>
              </el-form>
            </div>
          </div>

          <!-- LFS Keep Versions -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">LFS Version History</h2>
            <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
              Number of historical versions to keep for each LFS file. Older
              versions are garbage collected. Higher values allow more git
              reset/revert operations.
            </p>
            <div v-if="lfsSettings" class="space-y-4">
              <el-form label-position="top">
                <el-form-item label="Version History Mode">
                  <el-radio-group v-model="lfsSettings.versions_mode">
                    <div class="space-y-2">
                      <el-radio label="server_default">
                        Use server default
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                          ({{ lfsSettings.server_defaults.lfs_keep_versions }}
                          versions)
                        </span>
                      </el-radio>
                      <el-radio label="custom">Custom version count</el-radio>
                    </div>
                  </el-radio-group>
                </el-form-item>

                <el-form-item
                  v-if="lfsSettings.versions_mode === 'custom'"
                  label="Keep Versions"
                >
                  <el-input-number
                    v-model="lfsSettings.keep_versions"
                    :min="2"
                    :max="100"
                    :step="1"
                  />
                  <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    Minimum: 2 versions (recommended: 5-10 for production)
                  </div>
                </el-form-item>

                <div
                  v-if="lfsSettings.lfs_keep_versions_effective"
                  class="text-sm text-gray-600 dark:text-gray-400"
                >
                  <strong>Currently using:</strong>
                  {{ lfsSettings.lfs_keep_versions_effective }} versions ({{
                    lfsSettings.lfs_keep_versions_source === "repository"
                      ? "custom"
                      : "server default"
                  }})
                </div>
              </el-form>
            </div>
          </div>

          <!-- LFS Suffix Rules -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">LFS Suffix Rules</h2>
            <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
              File extensions that should ALWAYS use LFS, regardless of file
              size. Useful for model formats that should always be treated as
              large files.
            </p>
            <div v-if="lfsSettings" class="space-y-4">
              <el-form label-position="top">
                <el-form-item label="Suffix Rules">
                  <div class="space-y-2">
                    <div
                      v-for="(suffix, index) in lfsSettings.suffix_rules"
                      :key="index"
                      class="flex items-center gap-2"
                    >
                      <el-input
                        v-model="lfsSettings.suffix_rules[index]"
                        placeholder=".safetensors"
                        class="flex-1"
                      />
                      <el-button
                        type="danger"
                        size="small"
                        @click="removeSuffixRule(index)"
                      >
                        <div class="i-carbon-trash-can" />
                      </el-button>
                    </div>
                    <el-button
                      type="primary"
                      size="small"
                      @click="addSuffixRule"
                    >
                      <div class="i-carbon-add inline-block mr-1" />
                      Add Suffix Rule
                    </el-button>
                  </div>
                  <div class="text-sm text-gray-500 dark:text-gray-400 mt-2">
                    <strong>Common ML formats:</strong> .safetensors, .bin,
                    .gguf, .pt, .pth, .onnx, .msgpack
                  </div>
                </el-form-item>

                <div
                  v-if="
                    lfsSettings.lfs_suffix_rules_effective &&
                    lfsSettings.lfs_suffix_rules_effective.length > 0
                  "
                  class="text-sm text-gray-600 dark:text-gray-400"
                >
                  <strong>Currently active:</strong>
                  {{ lfsSettings.lfs_suffix_rules_effective.join(", ") }}
                </div>
              </el-form>
            </div>
          </div>

          <!-- Save Button -->
          <div class="card">
            <el-button
              type="primary"
              @click="saveLfsSettings"
              :loading="savingLfs"
              size="large"
            >
              Save LFS Settings
            </el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- Storage & Quota -->
      <el-tab-pane label="Storage & Quota" name="quota">
        <div class="max-w-2xl space-y-6">
          <!-- Storage Usage Card -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">Storage Usage</h2>
            <div v-if="quotaInfo" class="space-y-4">
              <div>
                <div class="text-sm text-gray-600 dark:text-gray-400 mb-1">
                  Current Usage
                </div>
                <div class="text-3xl font-bold">
                  {{ formatSize(quotaInfo.used_bytes) }}
                </div>
                <div
                  v-if="quotaInfo.effective_quota_bytes"
                  class="text-sm text-gray-500 dark:text-gray-400 mt-1"
                >
                  of {{ formatSize(quotaInfo.effective_quota_bytes) }}
                </div>
              </div>

              <div
                v-if="
                  quotaInfo.percentage_used !== null &&
                  quotaInfo.percentage_used !== undefined
                "
              >
                <el-progress
                  :percentage="
                    Math.min(
                      100,
                      Math.round(quotaInfo.percentage_used * 100) / 100,
                    )
                  "
                  :color="getProgressColor(quotaInfo.percentage_used)"
                  :stroke-width="8"
                  :format="(percentage) => `${percentage.toFixed(2)}%`"
                />
              </div>

              <el-button
                @click="handleRecalculateStorage"
                :loading="recalculating"
              >
                <div class="i-carbon-renew inline-block mr-1" />
                Recalculate Storage
              </el-button>
            </div>
            <div v-else class="text-center py-8">
              <el-icon class="is-loading" :size="40">
                <div class="i-carbon-loading" />
              </el-icon>
              <p class="mt-4 text-gray-500 dark:text-gray-400">
                Loading storage info...
              </p>
            </div>
          </div>

          <!-- Repository Quota Card -->
          <div class="card">
            <h2 class="text-xl font-semibold mb-4">Repository Quota</h2>
            <div v-if="quotaInfo" class="space-y-4">
              <el-form label-position="top">
                <el-form-item label="Storage Limit">
                  <el-radio-group v-model="quotaSettings.mode">
                    <div class="space-y-2">
                      <el-radio label="inherit">
                        Inherit from {{ route.params.namespace }}
                        <span
                          v-if="quotaInfo.namespace_quota_bytes"
                          class="text-sm text-gray-500"
                        >
                          ({{ formatSize(quotaInfo.namespace_quota_bytes) }})
                        </span>
                        <span v-else class="text-sm text-gray-500">
                          (unlimited)
                        </span>
                      </el-radio>
                      <el-radio label="custom">Custom Limit</el-radio>
                    </div>
                  </el-radio-group>
                </el-form-item>

                <el-form-item
                  v-if="quotaSettings.mode === 'custom'"
                  label="Custom Limit (GB)"
                >
                  <el-input-number
                    v-model="quotaSettings.quota_gb"
                    :min="0"
                    :max="maxQuotaGB"
                    :step="0.1"
                    :precision="2"
                  />
                  <div
                    v-if="quotaInfo.namespace_available_bytes !== null"
                    class="text-sm text-gray-500 dark:text-gray-400 mt-1"
                  >
                    Maximum available: {{ formatSize(maxQuotaBytes) }} (from
                    namespace)
                  </div>
                  <div
                    v-else
                    class="text-sm text-gray-500 dark:text-gray-400 mt-1"
                  >
                    Namespace has unlimited quota
                  </div>
                </el-form-item>

                <el-button
                  type="primary"
                  @click="saveQuotaSettings"
                  :loading="savingQuota"
                >
                  Save Quota Settings
                </el-button>
              </el-form>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from "vue-router";
import { repoAPI, settingsAPI, validationAPI, quotaAPI } from "@/utils/api";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

const activeTab = ref("general");
const settings = ref({
  private: false,
});
const moveToRepo = ref("");
const moveToRepoValidation = ref({
  checking: false,
  available: null,
  message: "",
});
const newBranch = ref({
  name: "",
  revision: "",
});
const newTag = ref({
  name: "",
  revision: "",
  message: "",
});
const quotaInfo = ref(null);
const quotaSettings = ref({
  mode: "inherit", // "inherit" or "custom"
  quota_gb: 0,
});
const recalculating = ref(false);
const savingQuota = ref(false);
const lfsSettings = ref(null);
const savingLfs = ref(false);

const repoId = computed(() => `${route.params.namespace}/${route.params.name}`);
const repoType = computed(() => route.params.type);

const maxQuotaBytes = computed(() => {
  if (!quotaInfo.value) return 0;
  // If namespace has unlimited quota, allow any value
  if (quotaInfo.value.namespace_available_bytes === null) {
    return Number.MAX_SAFE_INTEGER / 1000 ** 3; // Very large GB value
  }
  // Add back current repo quota if it exists (we're replacing it)
  let available = quotaInfo.value.namespace_available_bytes;
  if (quotaInfo.value.quota_bytes !== null) {
    available += quotaInfo.value.quota_bytes;
  }
  return available;
});

const maxQuotaGB = computed(() => {
  return Math.floor((maxQuotaBytes.value / 1000 ** 3) * 100) / 100;
});

async function loadRepoInfo() {
  try {
    const { data } = await repoAPI.getInfo(
      repoType.value,
      route.params.namespace,
      route.params.name,
    );
    settings.value.private = data.private || false;
    moveToRepo.value = repoId.value;
  } catch (err) {
    console.error("Failed to load repo info:", err);
    ElMessage.error("Failed to load repository information");
  }
}

async function saveGeneralSettings() {
  try {
    await settingsAPI.updateRepoSettings(
      repoType.value,
      route.params.namespace,
      route.params.name,
      {
        private: settings.value.private,
      },
    );
    ElMessage.success("Settings updated successfully");
  } catch (err) {
    console.error("Failed to update settings:", err);
    ElMessage.error("Failed to update settings");
  }
}

let validateTimeout = null;
async function handleMoveToRepoChange() {
  // Clear previous timeout
  if (validateTimeout) {
    clearTimeout(validateTimeout);
  }

  const newRepoId = moveToRepo.value.trim();

  // Reset validation state
  moveToRepoValidation.value = {
    checking: false,
    available: null,
    message: "",
  };

  if (!newRepoId) {
    return;
  }

  // Normalize and check if same as current
  const normalizedCurrent = repoId.value.toLowerCase();
  const normalizedNew = newRepoId.toLowerCase();

  if (normalizedCurrent === normalizedNew) {
    moveToRepoValidation.value = {
      checking: false,
      available: false,
      message: "New repository ID is the same as current ID",
    };
    return;
  }

  // Check format
  if (!newRepoId.includes("/")) {
    moveToRepoValidation.value = {
      checking: false,
      available: false,
      message: "Repository ID must be in format: namespace/name",
    };
    return;
  }

  // Debounce validation API call
  validateTimeout = setTimeout(async () => {
    moveToRepoValidation.value.checking = true;

    try {
      const [namespace, name] = newRepoId.split("/");
      const { data } = await validationAPI.checkName({
        name: name,
        namespace: namespace,
        type: repoType.value,
      });

      moveToRepoValidation.value = {
        checking: false,
        available: data.available,
        message: data.message,
      };
    } catch (err) {
      console.error("Name validation failed:", err);
      moveToRepoValidation.value = {
        checking: false,
        available: false,
        message: "Failed to validate name",
      };
    }
  }, 500); // 500ms debounce
}

async function handleMoveRepo() {
  if (!moveToRepo.value) {
    ElMessage.warning("Please enter a new repository ID");
    return;
  }

  // Normalize repository IDs for comparison (lowercase, trim)
  const normalizedCurrent = repoId.value.toLowerCase().trim();
  const normalizedNew = moveToRepo.value.toLowerCase().trim();

  if (normalizedCurrent === normalizedNew) {
    ElMessage.warning("New repository ID is the same as current ID");
    return;
  }

  try {
    await ElMessageBox.confirm(
      `This will move the repository to ${moveToRepo.value}. Old links will redirect to the new location. Continue?`,
      "Move Repository",
      {
        type: "warning",
        confirmButtonText: "Move",
        cancelButtonText: "Cancel",
      },
    );

    await settingsAPI.moveRepo({
      fromRepo: repoId.value,
      toRepo: moveToRepo.value,
      type: repoType.value,
    });

    ElMessage.success("Repository moved successfully");

    // Redirect to new location
    const [newNamespace, newName] = moveToRepo.value.split("/");
    router.push(`/${repoType.value}s/${newNamespace}/${newName}`);
  } catch (err) {
    if (err !== "cancel") {
      console.error("Failed to move repository:", err);
      const errorMsg =
        err.response?.data?.detail?.error || "Failed to move repository";
      ElMessage.error(errorMsg);
    }
  }
}

async function handleSquashRepo() {
  try {
    await ElMessageBox.confirm(
      `This will clear all commit history for ${repoId.value} and optimize storage. Only the current state will be preserved. This action cannot be undone!`,
      "Squash Repository",
      {
        type: "warning",
        confirmButtonText: "Squash",
        cancelButtonText: "Cancel",
      },
    );

    // Second confirmation
    await ElMessageBox.prompt(
      `Please type the repository name "${route.params.name}" to confirm`,
      "Confirm Squash",
      {
        confirmButtonText: "Squash",
        cancelButtonText: "Cancel",
        inputPattern: new RegExp(`^${route.params.name}$`),
        inputErrorMessage: "Repository name does not match",
      },
    );

    const loading = ElMessage({
      message: "Squashing repository... This may take a few minutes.",
      type: "info",
      duration: 0,
    });

    try {
      await settingsAPI.squashRepo({
        repo: repoId.value,
        type: repoType.value,
      });

      loading.close();
      ElMessage.success("Repository squashed successfully");

      // Reload page to show updated state
      setTimeout(() => {
        window.location.reload();
      }, 1000);
    } catch (err) {
      loading.close();
      throw err;
    }
  } catch (err) {
    if (err !== "cancel" && err !== "close") {
      console.error("Failed to squash repository:", err);
      const errorMsg =
        err.response?.data?.detail?.error || "Failed to squash repository";
      ElMessage.error(errorMsg);
    }
  }
}

async function handleDeleteRepo() {
  try {
    await ElMessageBox.confirm(
      `Are you sure you want to delete ${repoId.value}? This action cannot be undone!`,
      "Delete Repository",
      {
        type: "error",
        confirmButtonText: "Delete",
        cancelButtonText: "Cancel",
        confirmButtonClass: "el-button--danger",
      },
    );

    // Second confirmation
    await ElMessageBox.prompt(
      `Please type the repository name "${route.params.name}" to confirm`,
      "Confirm Deletion",
      {
        confirmButtonText: "Delete",
        cancelButtonText: "Cancel",
        inputPattern: new RegExp(`^${route.params.name}$`),
        inputErrorMessage: "Repository name does not match",
      },
    );

    await repoAPI.delete({
      type: repoType.value,
      name: route.params.name,
      organization: route.params.namespace,
    });

    ElMessage.success("Repository deleted successfully");
    router.push("/");
  } catch (err) {
    if (err !== "cancel" && err !== "close") {
      console.error("Failed to delete repository:", err);
      ElMessage.error("Failed to delete repository");
    }
  }
}

async function handleCreateBranch() {
  if (!newBranch.value.name) {
    ElMessage.warning("Please enter a branch name");
    return;
  }

  try {
    await settingsAPI.createBranch(
      repoType.value,
      route.params.namespace,
      route.params.name,
      {
        branch: newBranch.value.name,
        revision: newBranch.value.revision || undefined,
      },
    );

    ElMessage.success(`Branch '${newBranch.value.name}' created successfully`);
    newBranch.value = { name: "", revision: "" };
  } catch (err) {
    console.error("Failed to create branch:", err);
    ElMessage.error("Failed to create branch");
  }
}

async function handleCreateTag() {
  if (!newTag.value.name) {
    ElMessage.warning("Please enter a tag name");
    return;
  }

  try {
    await settingsAPI.createTag(
      repoType.value,
      route.params.namespace,
      route.params.name,
      {
        tag: newTag.value.name,
        revision: newTag.value.revision || undefined,
        message: newTag.value.message || undefined,
      },
    );

    ElMessage.success(`Tag '${newTag.value.name}' created successfully`);
    newTag.value = { name: "", revision: "", message: "" };
  } catch (err) {
    console.error("Failed to create tag:", err);
    ElMessage.error("Failed to create tag");
  }
}

async function loadQuotaInfo() {
  try {
    const { data } = await quotaAPI.getRepoQuota(
      repoType.value,
      route.params.namespace,
      route.params.name,
    );
    quotaInfo.value = data;

    // Set initial quota settings based on current quota
    if (data.quota_bytes === null) {
      quotaSettings.value.mode = "inherit";
      quotaSettings.value.quota_gb = 0;
    } else {
      quotaSettings.value.mode = "custom";
      quotaSettings.value.quota_gb =
        Math.round((data.quota_bytes / 1000 ** 3) * 100) / 100;
    }
  } catch (err) {
    console.error("Failed to load quota info:", err);
    ElMessage.error("Failed to load quota information");
  }
}

async function handleRecalculateStorage() {
  recalculating.value = true;
  try {
    const { data } = await quotaAPI.recalculateRepoStorage(
      repoType.value,
      route.params.namespace,
      route.params.name,
    );
    quotaInfo.value = data;
    ElMessage.success("Storage recalculated successfully");
  } catch (err) {
    console.error("Failed to recalculate storage:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to recalculate storage";
    ElMessage.error(errorMsg);
  } finally {
    recalculating.value = false;
  }
}

async function saveQuotaSettings() {
  savingQuota.value = true;
  try {
    const quota_bytes =
      quotaSettings.value.mode === "inherit"
        ? null
        : Math.floor(quotaSettings.value.quota_gb * 1000 ** 3);

    const { data } = await quotaAPI.setRepoQuota(
      repoType.value,
      route.params.namespace,
      route.params.name,
      { quota_bytes },
    );

    quotaInfo.value = data;
    ElMessage.success("Quota settings saved successfully");
  } catch (err) {
    console.error("Failed to save quota settings:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to save quota settings";
    ElMessage.error(errorMsg);
  } finally {
    savingQuota.value = false;
  }
}

function formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1000 * 1000) return (bytes / 1000).toFixed(1) + " KB";
  if (bytes < 1000 * 1000 * 1000)
    return (bytes / (1000 * 1000)).toFixed(1) + " MB";
  return (bytes / (1000 * 1000 * 1000)).toFixed(2) + " GB";
}

function getProgressColor(percentage) {
  if (percentage >= 90) return "#f56c6c"; // Red
  if (percentage >= 75) return "#e6a23c"; // Orange
  return "#67c23a"; // Green
}

async function loadLfsSettings() {
  try {
    const { data } = await settingsAPI.getLfsSettings(
      repoType.value,
      route.params.namespace,
      route.params.name,
    );

    // Initialize local state from API response
    lfsSettings.value = {
      // Server defaults
      server_defaults: data.server_defaults,

      // Configured values
      lfs_threshold_bytes: data.lfs_threshold_bytes,
      lfs_keep_versions: data.lfs_keep_versions,
      lfs_suffix_rules: data.lfs_suffix_rules,

      // Effective values (for display)
      lfs_threshold_bytes_effective: data.lfs_threshold_bytes_effective,
      lfs_threshold_bytes_source: data.lfs_threshold_bytes_source,
      lfs_keep_versions_effective: data.lfs_keep_versions_effective,
      lfs_keep_versions_source: data.lfs_keep_versions_source,
      lfs_suffix_rules_effective: data.lfs_suffix_rules_effective,
      lfs_suffix_rules_source: data.lfs_suffix_rules_source,

      // UI state
      threshold_mode:
        data.lfs_threshold_bytes === null ? "server_default" : "custom",
      threshold_mb: data.lfs_threshold_bytes
        ? Math.round(data.lfs_threshold_bytes / (1000 * 1000))
        : 5,
      versions_mode:
        data.lfs_keep_versions === null ? "server_default" : "custom",
      keep_versions: data.lfs_keep_versions || 5,
      suffix_rules: data.lfs_suffix_rules || [],
    };
  } catch (err) {
    console.error("Failed to load LFS settings:", err);
    ElMessage.error("Failed to load LFS settings");
  }
}

async function saveLfsSettings() {
  savingLfs.value = true;
  try {
    const payload = {};

    // Threshold
    if (lfsSettings.value.threshold_mode === "custom") {
      payload.lfs_threshold_bytes =
        lfsSettings.value.threshold_mb * 1000 * 1000;
    } else {
      payload.lfs_threshold_bytes = null; // Use server default
    }

    // Keep versions
    if (lfsSettings.value.versions_mode === "custom") {
      payload.lfs_keep_versions = lfsSettings.value.keep_versions;
    } else {
      payload.lfs_keep_versions = null; // Use server default
    }

    // Suffix rules (filter out empty strings)
    const cleanedRules = lfsSettings.value.suffix_rules.filter(
      (r) => r && r.trim(),
    );
    payload.lfs_suffix_rules = cleanedRules.length > 0 ? cleanedRules : null;

    await settingsAPI.updateRepoSettings(
      repoType.value,
      route.params.namespace,
      route.params.name,
      payload,
    );

    ElMessage.success("LFS settings saved successfully");

    // Reload to show updated effective values
    await loadLfsSettings();
  } catch (err) {
    console.error("Failed to save LFS settings:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to save LFS settings";
    ElMessage.error(errorMsg);
  } finally {
    savingLfs.value = false;
  }
}

function addSuffixRule() {
  lfsSettings.value.suffix_rules.push("");
}

function removeSuffixRule(index) {
  lfsSettings.value.suffix_rules.splice(index, 1);
}

watch(
  () => activeTab.value,
  (newTab) => {
    if (newTab === "quota" && !quotaInfo.value) {
      loadQuotaInfo();
    }
    if (newTab === "lfs" && !lfsSettings.value) {
      loadLfsSettings();
    }
  },
);

onMounted(() => {
  if (!authStore.isAuthenticated) {
    ElMessage.error("You must be logged in to access settings");
    router.push("/login");
    return;
  }
  loadRepoInfo();
  // Load quota info if starting on quota tab
  if (activeTab.value === "quota") {
    loadQuotaInfo();
  }
  // Load LFS settings if starting on LFS tab
  if (activeTab.value === "lfs") {
    loadLfsSettings();
  }
});
</script>

<style scoped>
/* Visibility options container */
.visibility-options {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: 100%;
}

/* Individual visibility option */
.visibility-option {
  display: grid;
  grid-template-columns: auto auto 1fr;
  gap: 0.75rem;
  align-items: start;
  padding: 1rem;
  border: 2px solid #e5e7eb;
  border-radius: 0.5rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.dark .visibility-option {
  border-color: #374151;
}

.visibility-option:hover {
  background-color: #f9fafb;
  border-color: #d1d5db;
}

.dark .visibility-option:hover {
  background-color: #1f2937;
  border-color: #4b5563;
}

.visibility-option.selected {
  border-color: #3b82f6;
  background-color: #eff6ff;
}

.dark .visibility-option.selected {
  border-color: #60a5fa;
  background-color: #1e3a8a;
}

/* Custom radio circle */
.option-radio {
  display: flex;
  align-items: center;
  padding-top: 0.125rem;
}

.radio-circle {
  width: 1.25rem;
  height: 1.25rem;
  border: 2px solid #d1d5db;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.dark .radio-circle {
  border-color: #6b7280;
}

.radio-circle.checked {
  border-color: #3b82f6;
  background-color: #3b82f6;
}

.dark .radio-circle.checked {
  border-color: #60a5fa;
  background-color: #60a5fa;
}

.radio-dot {
  width: 0.5rem;
  height: 0.5rem;
  background-color: white;
  border-radius: 50%;
}

/* Icon styling */
.option-icon {
  display: flex;
  align-items: center;
  color: #6b7280;
  padding-top: 0.125rem;
}

.dark .option-icon {
  color: #9ca3af;
}

.visibility-option.selected .option-icon {
  color: #3b82f6;
}

.dark .visibility-option.selected .option-icon {
  color: #60a5fa;
}

/* Content area */
.option-content {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 0;
}

.option-title {
  font-weight: 600;
  font-size: 0.9375rem;
  color: #111827;
  line-height: 1.4;
}

.dark .option-title {
  color: #f9fafb;
}

.option-description {
  font-size: 0.875rem;
  color: #6b7280;
  line-height: 1.4;
}

.dark .option-description {
  color: #9ca3af;
}
</style>
