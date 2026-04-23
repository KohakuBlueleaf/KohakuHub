<!-- src/pages/[type]s/[namespace]/[name]/commit/[commit_id].vue -->
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
          :to="`/${type}s`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{
            type === "model"
              ? "Models"
              : type === "dataset"
                ? "Datasets"
                : "Spaces"
          }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>
        <RouterLink
          :to="`/${namespace}`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ namespace }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>
        <RouterLink
          :to="`/${type}s/${namespace}/${name}`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ name }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item
        >Commit {{ commitId?.substring(0, 8) }}</el-breadcrumb-item
      >
    </el-breadcrumb>

    <!-- Loading State -->
    <div v-if="loading" class="flex justify-center items-center py-20">
      <el-icon class="is-loading" size="48">
        <div class="i-carbon-circle-dash" />
      </el-icon>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="text-center py-20">
      <div class="i-carbon-warning text-6xl text-red-500 mb-4 inline-block" />
      <h2 class="text-2xl font-bold mb-2">Failed to Load Commit</h2>
      <p class="text-gray-600 dark:text-gray-400">{{ error }}</p>
      <el-button type="primary" @click="$router.back()" class="mt-4">
        Go Back
      </el-button>
    </div>

    <!-- Commit Details -->
    <div v-else-if="commitData">
      <!-- Header -->
      <div class="card mb-6">
        <div class="flex items-start gap-4 mb-4">
          <div class="i-carbon-commit text-4xl text-blue-500 flex-shrink-0" />
          <div class="flex-1 min-w-0">
            <h1 class="text-2xl font-bold mb-2 break-words">
              {{ commitData.message }}
            </h1>
            <div
              class="flex flex-wrap gap-4 text-sm text-gray-600 dark:text-gray-400"
            >
              <div class="flex items-center gap-2">
                <div class="i-carbon-user-avatar" />
                <RouterLink
                  :to="`/${commitData.author}`"
                  class="text-blue-600 dark:text-blue-400 hover:underline"
                >
                  {{ commitData.author }}
                </RouterLink>
              </div>
              <div class="flex items-center gap-2">
                <div class="i-carbon-time" />
                {{ formatDate(commitData.date) }}
              </div>
              <div class="flex items-center gap-2">
                <div class="i-carbon-id" />
                <code
                  class="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-xs"
                >
                  {{ commitData.commit_id?.substring(0, 8) }}
                </code>
              </div>
            </div>
            <div
              v-if="commitData.description"
              class="mt-3 text-gray-700 dark:text-gray-300"
            >
              {{ commitData.description }}
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex gap-3 mb-4">
          <el-button size="small" @click="showRevertDialog" class="btn-revert">
            <div class="i-carbon-undo inline-block mr-1" />
            Revert Commit
          </el-button>
          <el-button type="primary" size="small" @click="showResetDialog">
            <div class="i-carbon-reset inline-block mr-1" />
            Reset to This State
          </el-button>
        </div>

        <!-- Parent commit link -->
        <div
          v-if="commitData.parent_commit"
          class="pt-3 border-t border-gray-200 dark:border-gray-700"
        >
          <div class="flex items-center gap-2 text-sm">
            <span class="text-gray-600 dark:text-gray-400">Parent:</span>
            <code
              class="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-xs"
            >
              {{ commitData.parent_commit?.substring(0, 8) }}
            </code>
          </div>
        </div>
      </div>

      <!-- Revert Dialog -->
      <el-dialog
        v-model="revertDialogVisible"
        title="Revert Commit"
        width="500px"
      >
        <div class="space-y-4">
          <p class="text-gray-700 dark:text-gray-300">
            This will create a new commit that undoes the changes from commit
            <code
              class="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-xs"
            >
              {{ commitData?.commit_id?.substring(0, 8) }}
            </code>
          </p>

          <el-alert type="warning" :closable="false" show-icon>
            <template #title>
              Revert creates a new commit that undoes changes. It does not
              delete history.
            </template>
          </el-alert>

          <div>
            <label class="block text-sm font-medium mb-2"
              >Branch to revert on:</label
            >
            <el-select
              v-model="selectedBranch"
              placeholder="Select branch"
              class="w-full"
            >
              <el-option value="main" label="main" />
            </el-select>
          </div>

          <div>
            <el-checkbox v-model="revertForce">
              Force revert (ignore conflicts)
            </el-checkbox>
          </div>
        </div>

        <template #footer>
          <el-button @click="revertDialogVisible = false">Cancel</el-button>
          <el-button type="warning" @click="doRevert" :loading="reverting">
            Revert
          </el-button>
        </template>
      </el-dialog>

      <!-- Reset Dialog -->
      <el-dialog
        v-model="resetDialogVisible"
        title="Reset Branch to This Commit"
        width="500px"
      >
        <div class="space-y-4">
          <p class="text-gray-700 dark:text-gray-300">
            This will create a new commit that restores the branch to the state
            of commit
            <code
              class="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-xs"
            >
              {{ commitData?.commit_id?.substring(0, 8) }}
            </code>
          </p>

          <el-alert type="info" :closable="false" show-icon>
            <template #title>
              Reset creates a new commit. History is preserved - newer commits
              remain accessible.
            </template>
          </el-alert>

          <div>
            <label class="block text-sm font-medium mb-2"
              >Branch to reset:</label
            >
            <el-select
              v-model="selectedBranch"
              placeholder="Select branch"
              class="w-full"
            >
              <el-option value="main" label="main" />
            </el-select>
          </div>

          <div>
            <label class="block text-sm font-medium mb-2"
              >Commit Message (optional):</label
            >
            <el-input
              v-model="resetMessage"
              placeholder="Reset to previous state"
              type="textarea"
              :rows="2"
            />
          </div>

          <div>
            <el-checkbox v-model="resetForce">
              Force reset (required for main branch)
            </el-checkbox>
          </div>
        </div>

        <template #footer>
          <el-button @click="resetDialogVisible = false">Cancel</el-button>
          <el-button type="primary" @click="doReset" :loading="resetting">
            Create Reset Commit
          </el-button>
        </template>
      </el-dialog>

      <!-- Files Changed -->
      <div class="card">
        <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
          <div class="i-carbon-document text-blue-500" />
          Files Changed
          <el-tag type="info" size="small">{{
            commitData.files?.length || 0
          }}</el-tag>
        </h2>

        <div
          v-if="!commitData.files || commitData.files.length === 0"
          class="text-center py-12 text-gray-500 dark:text-gray-400"
        >
          <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
          <p>No files changed in this commit</p>
        </div>

        <el-collapse v-else class="commit-diff-collapse">
          <el-collapse-item
            v-for="file in commitData.files"
            :key="file.path"
            :name="file.path"
          >
            <!-- File header (custom title slot) -->
            <template #title>
              <div class="flex items-center gap-3 flex-1 py-1">
                <!-- Change type badge -->
                <el-tag
                  :type="getChangeType(file.type).type"
                  size="small"
                  class="flex-shrink-0"
                >
                  {{ getChangeType(file.type).label }}
                </el-tag>

                <!-- File path -->
                <code class="text-sm font-mono flex-1 truncate">{{
                  file.path
                }}</code>

                <!-- Badges -->
                <div class="flex items-center gap-2 flex-shrink-0">
                  <el-tag v-if="file.is_lfs" type="warning" size="small"
                    >LFS</el-tag
                  >
                  <el-tag v-if="file.diff" type="info" size="small"
                    >Diff</el-tag
                  >
                </div>

                <!-- Size change -->
                <span class="text-xs text-gray-500 flex-shrink-0">
                  <span
                    v-if="
                      file.previous_size &&
                      file.size_bytes !== file.previous_size
                    "
                  >
                    {{ formatBytes(file.previous_size) }} →
                    {{ formatBytes(file.size_bytes) }}
                  </span>
                  <span v-else>
                    {{ formatBytes(file.size_bytes) }}
                  </span>
                </span>
              </div>
            </template>

            <!-- File diff content -->
            <div class="p-4 bg-gray-50 dark:bg-gray-900">
              <!-- Image comparison (for image files) -->
              <div v-if="isImageFile(file.path)" class="space-y-4">
                <div
                  class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3"
                >
                  Image Comparison
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <!-- Before image (for modified/removed files) -->
                  <div
                    v-if="
                      (file.type === 'changed' || file.type === 'removed') &&
                      commitData.parent_commit
                    "
                    class="space-y-2"
                  >
                    <div
                      class="text-xs font-semibold text-red-600 dark:text-red-400"
                    >
                      {{ file.type === "removed" ? "Removed" : "Before" }}
                      <span
                        v-if="file.previous_size"
                        class="ml-2 text-gray-500"
                      >
                        ({{ formatBytes(file.previous_size) }})
                      </span>
                    </div>
                    <div
                      class="border-2 border-red-200 dark:border-red-800 rounded p-2 bg-white dark:bg-gray-800"
                    >
                      <img
                        :src="getImageUrl(file.path, commitData.parent_commit)"
                        :alt="`${file.path} (before)`"
                        class="max-w-full h-auto mx-auto"
                        style="max-height: 400px"
                      />
                    </div>
                  </div>

                  <!-- After image (for added/modified files) -->
                  <div
                    v-if="file.type !== 'removed'"
                    :class="file.type === 'changed' ? '' : 'md:col-span-2'"
                    class="space-y-2"
                  >
                    <div
                      class="text-xs font-semibold text-green-600 dark:text-green-400"
                    >
                      {{ file.type === "added" ? "Added" : "After" }}
                      <span v-if="file.size_bytes" class="ml-2 text-gray-500">
                        ({{ formatBytes(file.size_bytes) }})
                      </span>
                    </div>
                    <div
                      class="border-2 border-green-200 dark:border-green-800 rounded p-2 bg-white dark:bg-gray-800"
                    >
                      <img
                        :src="getImageUrl(file.path, commitData.commit_id)"
                        :alt="`${file.path} (after)`"
                        class="max-w-full h-auto mx-auto"
                        style="max-height: 400px"
                      />
                    </div>
                  </div>
                </div>

                <!-- LFS badge and metadata for images -->
                <div
                  v-if="file.is_lfs"
                  class="mt-4 p-3 bg-amber-50 dark:bg-amber-900/20 rounded border border-amber-200 dark:border-amber-800"
                >
                  <div class="flex items-center gap-2 text-sm">
                    <el-tag type="warning" size="small">LFS</el-tag>
                    <span class="text-gray-700 dark:text-gray-300"
                      >This image is stored using Git LFS</span
                    >
                  </div>
                  <div
                    v-if="file.sha256"
                    class="mt-2 text-xs text-gray-600 dark:text-gray-400"
                  >
                    <span class="font-semibold">SHA256:</span>
                    <code class="ml-2 font-mono break-all">{{
                      file.sha256
                    }}</code>
                  </div>
                </div>
              </div>

              <!-- Text file diff (if diff is available and renderable) -->
              <div
                v-else-if="file.diff && isTextRenderable(file.diff, file.path)"
                class="diff-viewer"
              >
                <pre
                  class="text-xs overflow-x-auto p-4 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700"
                ><code v-html="renderDiff(file.diff)"></code></pre>
              </div>

              <!-- LFS/Binary file metadata (for non-image binary files) -->
              <div
                v-else-if="file.is_lfs || isBinaryFile(file.path) || !file.diff"
                class="space-y-3"
              >
                <div
                  class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3"
                >
                  {{ file.is_lfs ? "Large File (LFS)" : "Binary File" }}
                </div>

                <!-- Previous state (for changed/removed) -->
                <div
                  v-if="file.previous_sha256 || file.previous_size"
                  class="space-y-2"
                >
                  <div
                    class="text-xs font-semibold text-gray-600 dark:text-gray-400"
                  >
                    Before:
                  </div>
                  <div
                    class="bg-red-50 dark:bg-red-900/20 p-3 rounded border border-red-200 dark:border-red-800"
                  >
                    <div v-if="file.previous_size" class="text-sm mb-2">
                      <span class="text-gray-600 dark:text-gray-400"
                        >Size:</span
                      >
                      <code class="ml-2 text-red-700 dark:text-red-400">{{
                        formatBytes(file.previous_size)
                      }}</code>
                    </div>
                    <div v-if="file.previous_sha256" class="text-sm">
                      <span class="text-gray-600 dark:text-gray-400"
                        >SHA256:</span
                      >
                      <code
                        class="ml-2 font-mono text-xs text-red-700 dark:text-red-400 break-all"
                        >{{ file.previous_sha256 }}</code
                      >
                    </div>
                  </div>
                </div>

                <!-- Current state (for added/changed) -->
                <div v-if="file.sha256 || file.size_bytes" class="space-y-2">
                  <div
                    class="text-xs font-semibold text-gray-600 dark:text-gray-400"
                  >
                    After:
                  </div>
                  <div
                    class="bg-green-50 dark:bg-green-900/20 p-3 rounded border border-green-200 dark:border-green-800"
                  >
                    <div v-if="file.size_bytes" class="text-sm mb-2">
                      <span class="text-gray-600 dark:text-gray-400"
                        >Size:</span
                      >
                      <code class="ml-2 text-green-700 dark:text-green-400">{{
                        formatBytes(file.size_bytes)
                      }}</code>
                    </div>
                    <div v-if="file.sha256" class="text-sm">
                      <span class="text-gray-600 dark:text-gray-400"
                        >SHA256:</span
                      >
                      <code
                        class="ml-2 font-mono text-xs text-green-700 dark:text-green-400 break-all"
                        >{{ file.sha256 }}</code
                      >
                    </div>
                  </div>
                </div>

                <!-- View file button -->
                <div v-if="file.type !== 'removed'" class="mt-3">
                  <el-button
                    size="small"
                    @click="viewFile(file.path)"
                    :icon="'View'"
                  >
                    View File
                  </el-button>
                </div>
              </div>

              <!-- Added files (no previous content) -->
              <div v-else-if="file.type === 'added'">
                <div class="text-xs text-green-600 dark:text-green-400 mb-2">
                  New file created
                </div>
                <el-button
                  size="small"
                  @click="viewFile(file.path)"
                  :icon="'View'"
                >
                  View File
                </el-button>
              </div>

              <!-- Removed files -->
              <div v-else-if="file.type === 'removed'">
                <div class="text-xs text-red-600 dark:text-red-400">
                  File deleted
                </div>
              </div>

              <!-- Fallback for files too large or diff unavailable -->
              <div v-else class="text-xs text-gray-500">
                <div class="mb-2">
                  {{
                    file.size_bytes > 1000000
                      ? "File too large to show diff (>1MB)"
                      : "Diff not available"
                  }}
                </div>
                <el-button
                  v-if="file.type !== 'removed'"
                  size="small"
                  @click="viewFile(file.path)"
                  :icon="'View'"
                >
                  View File
                </el-button>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
  </div>
</template>

<script setup>
import axios from "axios";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import { ElMessage } from "element-plus";

dayjs.extend(relativeTime);

const route = useRoute();
const router = useRouter();

const type = computed(() => route.params.type);
const namespace = computed(() => route.params.namespace);
const name = computed(() => route.params.name);
const commitId = computed(() => route.params.commit_id);
const repoId = computed(() => `${namespace.value}/${name.value}`);

const loading = ref(true);
const error = ref(null);
const commitData = ref(null);

// Revert state
const revertDialogVisible = ref(false);
const reverting = ref(false);
const revertForce = ref(false);

// Reset state
const resetDialogVisible = ref(false);
const resetting = ref(false);
const resetForce = ref(false);
const resetMessage = ref("");

// Selected branch for operations
const selectedBranch = ref("main");

async function loadCommitDetails() {
  loading.value = true;
  error.value = null;

  try {
    // Fetch commit detail
    const detailResponse = await axios.get(
      `/api/${type.value}s/${namespace.value}/${name.value}/commit/${commitId.value}`,
    );

    // Fetch commit diff
    const diffResponse = await axios.get(
      `/api/${type.value}s/${namespace.value}/${name.value}/commit/${commitId.value}/diff`,
    );

    commitData.value = {
      ...detailResponse.data,
      ...diffResponse.data,
    };
  } catch (err) {
    console.error("Failed to load commit:", err);
    error.value =
      err.response?.data?.detail?.error || "Failed to load commit details";
  } finally {
    loading.value = false;
  }
}

function showRevertDialog() {
  selectedBranch.value = "main";
  revertForce.value = false;
  revertDialogVisible.value = true;
}

function showResetDialog() {
  selectedBranch.value = "main";
  resetForce.value = false;
  resetMessage.value = "";
  resetDialogVisible.value = true;
}

async function doRevert() {
  reverting.value = true;

  try {
    await axios.post(
      `/api/${type.value}s/${repoId.value}/branch/${selectedBranch.value}/revert`,
      {
        ref: commitId.value,
        parent_number: 1,
        force: revertForce.value,
        allow_empty: false,
      },
    );

    ElMessage.success("Commit reverted successfully!");
    revertDialogVisible.value = false;

    // Redirect to commits page
    router.push(
      `/${type.value}s/${namespace.value}/${name.value}/commits/${selectedBranch.value}`,
    );
  } catch (err) {
    console.error("Revert failed:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to revert commit";

    if (err.response?.status === 409) {
      ElMessage.error("Revert conflict: " + errorMsg);
    } else {
      ElMessage.error(errorMsg);
    }
  } finally {
    reverting.value = false;
  }
}

async function doReset() {
  resetting.value = true;

  try {
    const payload = {
      ref: commitId.value,
      force: resetForce.value,
    };

    // Add custom message if provided
    if (resetMessage.value.trim()) {
      payload.message = resetMessage.value.trim();
    }

    await axios.post(
      `/api/${type.value}s/${repoId.value}/branch/${selectedBranch.value}/reset`,
      payload,
    );

    ElMessage.success("Reset commit created successfully!");
    resetDialogVisible.value = false;

    // Redirect to commits page to see the new commit
    router.push(
      `/${type.value}s/${namespace.value}/${name.value}/commits/${selectedBranch.value}`,
    );
  } catch (err) {
    console.error("Reset failed:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to reset branch";

    if (err.response?.status === 400 && errorMsg.includes("LFS")) {
      ElMessage.error("LFS files missing: " + errorMsg);
    } else {
      ElMessage.error(errorMsg);
    }
  } finally {
    resetting.value = false;
  }
}

function formatDate(timestamp) {
  if (!timestamp) return "Unknown";
  return dayjs.unix(timestamp).format("YYYY-MM-DD HH:mm:ss");
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const k = 1000;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function getChangeType(type) {
  switch (type) {
    case "added":
      return { label: "Added", type: "success" };
    case "removed":
      return { label: "Removed", type: "danger" };
    case "changed":
      return { label: "Modified", type: "warning" };
    default:
      return { label: type, type: "info" };
  }
}

function viewFile(path) {
  router.push(
    `/${type.value}s/${namespace.value}/${name.value}/blob/main/${path}`,
  );
}

function isBinaryFile(path) {
  // Check file extension to determine if it's a binary file
  const ext = path.split(".").pop()?.toLowerCase();

  const binaryExtensions = new Set([
    // Images
    "png",
    "jpg",
    "jpeg",
    "gif",
    "bmp",
    "webp",
    "ico",
    "svg",
    "tiff",
    "tif",
    // Videos
    "mp4",
    "avi",
    "mov",
    "wmv",
    "flv",
    "webm",
    "mkv",
    "m4v",
    // Audio
    "mp3",
    "wav",
    "flac",
    "aac",
    "m4a",
    "ogg",
    "wma",
    // Archives
    "zip",
    "tar",
    "gz",
    "bz2",
    "7z",
    "rar",
    "xz",
    // Executables
    "exe",
    "dll",
    "so",
    "dylib",
    "bin",
    // Documents
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    // Models & Data
    "safetensors",
    "pt",
    "pth",
    "ckpt",
    "pkl",
    "bin",
    "h5",
    "pb",
    // Fonts
    "ttf",
    "otf",
    "woff",
    "woff2",
    "eot",
  ]);

  return binaryExtensions.has(ext);
}

function isImageFile(path) {
  const ext = path.split(".").pop()?.toLowerCase();
  const imageExtensions = new Set([
    "png",
    "jpg",
    "jpeg",
    "gif",
    "bmp",
    "webp",
    "ico",
    "svg",
    "tiff",
    "tif",
  ]);
  return imageExtensions.has(ext);
}

function getImageUrl(path, commitId) {
  // Construct URL to view file at specific commit
  if (!commitId) return null;
  return `/api/${type.value}s/${namespace.value}/${name.value}/resolve/${commitId}/${path}`;
}

function isTextRenderable(diff, filePath) {
  // Check if diff contains mostly printable characters
  if (!diff) return false;

  // First check: if file extension indicates binary, don't render as text
  if (filePath && isBinaryFile(filePath)) {
    return false;
  }

  // Second check: look for null bytes and non-printable characters
  // Use proper binary detection by checking character codes
  let nonPrintable = 0;
  const sampleSize = Math.min(diff.length, 8000); // Check first 8KB

  for (let i = 0; i < sampleSize; i++) {
    const code = diff.charCodeAt(i);
    // Count null bytes and other non-printable chars (except newlines, tabs, etc.)
    if (code === 0 || (code < 32 && code !== 9 && code !== 10 && code !== 13)) {
      nonPrintable++;
    }
  }

  // If more than 5% non-printable characters, treat as binary
  return nonPrintable < sampleSize * 0.05;
}

function renderDiff(diff) {
  if (!diff) return "";

  // Escape HTML
  const escaped = diff
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Color diff lines
  const lines = escaped.split("\n");
  const colored = lines.map((line) => {
    if (line.startsWith("+") && !line.startsWith("+++")) {
      return `<span style="color: #22c55e; background: rgba(34, 197, 94, 0.1);">${line}</span>`;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      return `<span style="color: #ef4444; background: rgba(239, 68, 68, 0.1);">${line}</span>`;
    } else if (line.startsWith("@@")) {
      return `<span style="color: #3b82f6; font-weight: 600;">${line}</span>`;
    } else if (line.startsWith("+++") || line.startsWith("---")) {
      return `<span style="color: #6b7280; font-weight: 600;">${line}</span>`;
    } else {
      return line;
    }
  });

  return colored.join("\n");
}

onMounted(() => {
  loadCommitDetails();
});
</script>

<style scoped>
.is-loading {
  animation: rotate 1s linear infinite;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* Custom button styling for revert button */
.btn-revert {
  background-color: #f97316 !important;
  border-color: #f97316 !important;
  color: white !important;
}

.btn-revert:hover {
  background-color: #ea580c !important;
  border-color: #ea580c !important;
}

.btn-revert:active {
  background-color: #c2410c !important;
  border-color: #c2410c !important;
}

/* Dark mode */
:deep(.dark) .btn-revert {
  background-color: #ea580c !important;
  border-color: #ea580c !important;
}

:deep(.dark) .btn-revert:hover {
  background-color: #c2410c !important;
  border-color: #c2410c !important;
}

/* Diff viewer styling */
:deep(.commit-diff-collapse .el-collapse-item__header) {
  font-family: inherit;
  padding: 12px 16px;
  background: transparent;
}

:deep(.commit-diff-collapse .el-collapse-item__wrap) {
  border: none;
}

:deep(.commit-diff-collapse .el-collapse-item__content) {
  padding: 0;
}

.diff-viewer {
  font-family: "Courier New", monospace;
  font-size: 12px;
  line-height: 1.5;
}

/* Syntax highlighting for diff */
.diff-viewer :deep(code) {
  display: block;
  white-space: pre;
  color: inherit;
}

/* Diff line colors */
:deep(.diff-viewer code) {
  background: transparent !important;
}
</style>
