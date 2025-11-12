<script setup>
import { ref, computed, watch } from "vue";
import { ElMessage } from "element-plus";
import DatasetViewer from "@/components/DatasetViewer/DatasetViewer.vue";
import { detectFormat } from "@/components/DatasetViewer/api";

const props = defineProps({
  repoType: {
    type: String,
    required: true,
  },
  namespace: {
    type: String,
    required: true,
  },
  name: {
    type: String,
    required: true,
  },
  branch: {
    type: String,
    default: "main",
  },
  files: {
    type: Array,
    default: () => [],
  },
});

const selectedFile = ref(null);
const fileUrl = ref(null);
const loadingUrl = ref(false);

// Folder navigation state
const selectedFolder = ref(null);
const folderFiles = ref([]);
const loadingFolder = ref(false);

// Query parameters (SQL by default, LIMIT 10 for wide tables)
const sqlQuery = ref("SELECT * FROM dataset LIMIT 10");
const sqlWarning = ref("");

// Trigger key to force reload
const reloadKey = ref(0);

// Row detail state
const selectedRowIndex = ref(null);
const currentPreviewData = ref(null);

// Media modal state
const showMediaModal = ref(false);
const modalMediaUrl = ref("");
const modalMediaType = ref("image");

// Handle row selection
function handleRowSelected(index) {
  selectedRowIndex.value = index;
}

// Handle data loaded
function handleDataLoaded(data) {
  currentPreviewData.value = data;
}

// Open media modal
function openMediaModal(url, type) {
  modalMediaUrl.value = String(url);
  modalMediaType.value = type;
  showMediaModal.value = true;
}

// Close media modal
function closeMediaModal() {
  showMediaModal.value = false;
  modalMediaUrl.value = "";
}

// Format cell value
function formatValue(value) {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// Check if value is an image URL
function isImageUrl(value) {
  if (!value) return false;
  const str = String(value);
  if (str.startsWith("data:image/")) return true;
  if (str.startsWith("http://") || str.startsWith("https://")) {
    const lowerStr = str.toLowerCase();
    const imageExtensions = [
      ".jpg",
      ".jpeg",
      ".png",
      ".gif",
      ".webp",
      ".bmp",
      ".svg",
      ".ico",
    ];
    return imageExtensions.some((ext) => lowerStr.includes(ext));
  }
  return false;
}

// Check if value is a video URL
function isVideoUrl(value) {
  if (!value) return false;
  const str = String(value);
  if (str.startsWith("http://") || str.startsWith("https://")) {
    const lowerStr = str.toLowerCase();
    const videoExtensions = [".mp4", ".webm", ".ogg", ".mov", ".avi", ".mkv"];
    return videoExtensions.some((ext) => lowerStr.includes(ext));
  }
  return false;
}

// Validate and apply query
function applyQuery() {
  sqlWarning.value = "";

  // Clear selected row when starting new query
  selectedRowIndex.value = null;
  currentPreviewData.value = null;

  // Check for LIMIT clause
  const queryUpper = sqlQuery.value.toUpperCase();
  if (!queryUpper.includes("LIMIT")) {
    sqlWarning.value =
      "No LIMIT found - automatically adding LIMIT 10 for safety";
    // Backend will add LIMIT automatically
  }

  reloadKey.value++;
}

// Filter previewable files (only direct files, not in folders)
const previewableFiles = computed(() => {
  return props.files
    .filter((file) => file.type !== "directory")
    .filter((file) => {
      const format = detectFormat(file.path);
      return format && format !== "tar"; // Exclude TAR for now
    })
    .sort((a, b) => a.path.localeCompare(b.path));
});

// Get folders that might contain previewable files
const previewableFolders = computed(() => {
  return props.files
    .filter((file) => file.type === "directory")
    .sort((a, b) => a.path.localeCompare(b.path));
});

// Files to display (root files or folder files if folder selected)
const displayFiles = computed(() => {
  if (selectedFolder.value) {
    return folderFiles.value;
  }
  return previewableFiles.value;
});

// Group files by directory
const groupedFiles = computed(() => {
  const groups = {};

  for (const file of displayFiles.value) {
    const parts = file.path.split("/");
    const dir = parts.length > 1 ? parts.slice(0, -1).join("/") : "/";

    if (!groups[dir]) {
      groups[dir] = [];
    }

    groups[dir].push({
      ...file,
      basename: parts[parts.length - 1],
      format: detectFormat(file.path),
    });
  }

  return groups;
});

// Load files from a folder
async function loadFolder(folder) {
  selectedFolder.value = folder;
  loadingFolder.value = true;
  folderFiles.value = [];

  try {
    // Fetch files from folder with recursive=true
    const response = await fetch(
      `/api/${props.repoType}s/${props.namespace}/${props.name}/tree/${props.branch}/${folder.path}?recursive=true`,
    );

    if (!response.ok) {
      throw new Error(`Failed to load folder: ${response.statusText}`);
    }

    const data = await response.json();

    // Filter for previewable files only
    folderFiles.value = data
      .filter((file) => file.type !== "directory")
      .filter((file) => {
        const format = detectFormat(file.path);
        return format && format !== "tar";
      })
      .map((file) => {
        // `folder.path` usually ends with a slash (e.g. "images/").
        // Guard against double‑slashes just in case.
        const cleanFolder = folder.path.replace(/\/$/, "");
        const cleanFile   = file.path.replace(/^\/?/, ""); // remove leading slash if any
        return {
          ...file,
          path: `${cleanFolder}/${cleanFile}`,
        };
      });
  } catch (err) {
    ElMessage.error({
      message: `Failed to load folder: ${err.message}`,
      duration: 5000,
    });
    selectedFolder.value = null;
  } finally {
    loadingFolder.value = false;
  }
}

// Go back to folder list
function backToFolders() {
  selectedFolder.value = null;
  folderFiles.value = [];
  selectedFile.value = null;
  fileUrl.value = null;
}

// Select a file and get presigned URL
async function selectFile(file) {
  selectedFile.value = file;
  loadingUrl.value = true;
  fileUrl.value = null;

  try {
    // Get presigned URL by making HEAD request (follows redirect)
    const url = `/${props.repoType}s/${props.namespace}/${props.name}/resolve/${props.branch}/${file.path}`;

    const response = await fetch(url, { method: "HEAD" });

    if (!response.ok) {
      throw new Error(`Failed to get file URL: ${response.statusText}`);
    }

    // Get the final URL after redirect
    fileUrl.value = response.url;
  } catch (err) {
    ElMessage.error({
      message: `Failed to load file: ${err.message}`,
      duration: 5000,
    });
    selectedFile.value = null;
  } finally {
    loadingUrl.value = false;
  }
}

// Handle error from DatasetViewer
function handleViewerError(error) {
  ElMessage.error({
    message: `Preview error: ${error}`,
    duration: 5000,
  });
}

// Format file size
function formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + " " + sizes[i];
}

// Do NOT auto-select files to avoid overloading backend
// User must explicitly click to preview a file
</script>

<template>
  <div class="dataset-viewer-tab">
    <!-- Full width layout: File list on left, Viewer on right -->
    <div class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
      <!-- File List Sidebar (no max height - can grow) -->
      <div class="file-sidebar flex flex-col gap-3">
        <!-- File List Card -->
        <div class="card" style="padding: 0">
          <!-- Header -->
          <div
            class="px-2 py-1.5 border-b border-gray-200 dark:border-gray-700"
          >
            <div class="flex items-center justify-between">
              <div>
                <h3 class="text-sm font-semibold">
                  {{ selectedFolder ? "Files in Folder" : "Previewable Files" }}
                </h3>
                <p class="text-xs text-gray-500 dark:text-gray-400">
                  {{ displayFiles.length }} file{{
                    displayFiles.length !== 1 ? "s" : ""
                  }}
                </p>
              </div>
              <!-- Back button when in folder -->
              <button
                v-if="selectedFolder"
                @click="backToFolders"
                class="text-xs px-2 py-1 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 rounded transition-colors"
              >
                <div class="i-carbon-arrow-left inline-block mr-1" />
                Back
              </button>
            </div>
          </div>

          <!-- File List (Scrollable with min height) -->
          <div
            class="file-list-scroll overflow-y-auto"
            style="min-height: 200px; max-height: 400px"
          >
            <!-- No files and no folders -->
            <div
              v-if="
                displayFiles.length === 0 &&
                !selectedFolder &&
                previewableFolders.length === 0
              "
              class="text-center py-8 px-3 text-gray-600 dark:text-gray-400"
            >
              <div class="i-carbon-document-blank text-4xl mb-2 inline-block" />
              <p class="text-sm">No previewable files</p>
              <p class="text-xs mt-1">Supported: CSV, JSON, JSONL, Parquet</p>
            </div>

            <!-- Loading folder -->
            <div
              v-else-if="loadingFolder"
              class="text-center py-8 px-3 text-gray-600 dark:text-gray-400"
            >
              <div
                class="i-carbon-loading inline-block text-2xl animate-spin mb-2"
              />
              <p class="text-sm">Loading folder...</p>
            </div>

            <!-- Folders (show when not in a folder) -->
            <div
              v-else-if="!selectedFolder && previewableFolders.length > 0"
              class="py-1"
            >
              <div
                class="px-3 py-2 text-xs font-semibold text-gray-500 dark:text-gray-400"
              >
                Folders
              </div>
              <div
                v-for="folder in previewableFolders"
                :key="folder.path"
                class="folder-item px-3 py-2 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-all"
                @click="loadFolder(folder)"
              >
                <div class="flex items-center gap-2">
                  <div
                    class="i-carbon-folder text-yellow-600 dark:text-yellow-400"
                  />
                  <div class="flex-1 min-w-0">
                    <div class="text-sm font-medium truncate">
                      {{ folder.path.replace(/\/$/, "") }}
                    </div>
                  </div>
                  <div class="i-carbon-chevron-right text-gray-400" />
                </div>
              </div>

              <!-- Root files section if any -->
              <div v-if="previewableFiles.length > 0" class="mt-2">
                <div
                  class="px-3 py-2 text-xs font-semibold text-gray-500 dark:text-gray-400"
                >
                  Root Files
                </div>
              </div>
            </div>

            <!-- File groups -->
            <div v-if="displayFiles.length > 0" class="py-1">
              <div
                v-for="(files, dir) in groupedFiles"
                :key="dir"
                class="file-group mb-2"
              >
                <!-- Directory header -->
                <div
                  v-if="dir !== '/'"
                  class="text-xs font-semibold text-gray-500 dark:text-gray-400 px-3 py-1"
                >
                  <div class="i-carbon-folder inline-block mr-1" />
                  {{ dir }}
                </div>

                <!-- Files in directory -->
                <div
                  v-for="file in files"
                  :key="file.path"
                  class="file-item px-3 py-2 cursor-pointer transition-all"
                  :class="[
                    selectedFile?.path === file.path
                      ? 'bg-blue-100 dark:bg-blue-900 text-blue-900 dark:text-blue-100'
                      : 'hover:bg-gray-100 dark:hover:bg-gray-800',
                  ]"
                  @click="selectFile(file)"
                >
                  <div class="flex items-center justify-between gap-2">
                    <div class="flex-1 min-w-0">
                      <div class="text-sm font-medium truncate">
                        {{ file.basename }}
                      </div>
                      <div class="text-xs text-gray-500 dark:text-gray-400">
                        {{ formatSize(file.size) }}
                      </div>
                    </div>
                    <div
                      class="px-2 py-0.5 bg-gray-200 dark:bg-gray-700 rounded text-xs font-mono flex-shrink-0"
                    >
                      {{ file.format.toUpperCase() }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Query Card (SQL always enabled) -->
        <div class="card flex-shrink-0" style="padding: 0.75rem">
          <div class="space-y-2">
            <!-- SQL Query Input -->
            <div>
              <label
                class="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1"
              >
                SQL Query
              </label>
              <textarea
                v-model="sqlQuery"
                placeholder="SELECT * FROM dataset LIMIT 10"
                rows="8"
                class="w-full px-2 py-1.5 text-xs font-mono border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-black dark:text-white resize-vertical"
              />
              <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                Use 'dataset' as table name
              </div>

              <!-- SQL Warning -->
              <div
                v-if="sqlWarning"
                class="mt-1 p-2 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700 rounded text-xs text-yellow-800 dark:text-yellow-200"
              >
                ⚠️ {{ sqlWarning }}
              </div>
            </div>

            <!-- Run Query Button -->
            <button
              @click="applyQuery"
              class="w-full px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded font-medium text-sm transition-colors"
              :disabled="!sqlQuery.trim()"
            >
              Run Query
            </button>
          </div>
        </div>
      </div>

      <!-- Viewer Panel (Full Width) -->
      <div class="viewer-panel min-w-0">
        <!-- Loading state -->
        <div v-if="loadingUrl" class="card text-center py-20">
          <el-icon class="is-loading" :size="40">
            <div class="i-carbon-loading" />
          </el-icon>
          <p class="mt-4 text-gray-600 dark:text-gray-400">
            Loading file URL...
          </p>
        </div>

        <!-- No file selected -->
        <div
          v-else-if="!selectedFile"
          class="card text-center py-20 text-gray-600 dark:text-gray-400"
        >
          <div class="i-carbon-data-table text-6xl mb-4 inline-block" />
          <p>Select a file to preview</p>
        </div>

        <!-- Dataset Viewer -->
        <DatasetViewer
          v-else-if="fileUrl"
          :key="reloadKey"
          :file-url="fileUrl"
          :file-name="selectedFile.path"
          :max-rows="10000"
          :sql-query="sqlQuery"
          :use-s-q-l="true"
          @error="handleViewerError"
          @warning="(msg) => (sqlWarning = msg)"
          @row-selected="handleRowSelected"
          @data-loaded="handleDataLoaded"
        />
      </div>
    </div>

    <!-- Detail viewer card (full width, below query + table) -->
    <div
      v-if="selectedRowIndex !== null && currentPreviewData"
      class="mt-4 card"
      style="padding: 1rem"
    >
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-lg font-semibold text-gray-700 dark:text-gray-300">
          Row {{ selectedRowIndex + 1 }} Details
        </h3>
        <el-button size="small" @click="selectedRowIndex = null">
          <div class="i-carbon-close inline-block mr-1" />
          Close
        </el-button>
      </div>

      <!-- Responsive grid layout: adaptive columns based on viewport -->
      <div
        class="detail-grid grid gap-2 max-h-96 overflow-y-auto"
        style="grid-template-columns: repeat(auto-fit, minmax(250px, 1fr))"
      >
        <div
          v-for="(value, colIndex) in currentPreviewData.rows[selectedRowIndex]"
          :key="colIndex"
          class="detail-field p-2 bg-gray-50 dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700"
        >
          <div
            class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1 truncate"
            :title="currentPreviewData.columns[colIndex]"
          >
            {{ currentPreviewData.columns[colIndex] }}
          </div>

          <!-- Image preview in detail -->
          <div v-if="isImageUrl(value)">
            <img
              :src="String(value)"
              alt="Preview"
              class="max-w-full h-auto max-h-48 rounded border border-gray-300 dark:border-gray-600 cursor-pointer hover:opacity-80"
              @click="openMediaModal(value, 'image')"
            />
            <div class="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">
              {{ formatValue(value) }}
            </div>
          </div>

          <!-- Video preview in detail -->
          <div v-else-if="isVideoUrl(value)">
            <video
              :src="String(value)"
              class="max-w-full h-auto max-h-48 rounded border border-gray-300 dark:border-gray-600 cursor-pointer"
              @click="openMediaModal(value, 'video')"
            >
              Your browser does not support the video tag.
            </video>
            <div class="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">
              {{ formatValue(value) }}
            </div>
          </div>

          <!-- Regular text value -->
          <div
            v-else
            class="text-sm text-gray-900 dark:text-gray-100 break-words font-mono whitespace-pre-wrap"
          >
            {{ formatValue(value) }}
          </div>
        </div>
      </div>
    </div>

    <!-- Media Modal -->
    <el-dialog
      v-model="showMediaModal"
      width="90%"
      top="5vh"
      @close="closeMediaModal"
    >
      <template #header>
        <div class="flex items-center justify-between">
          <span class="text-lg font-semibold">
            {{ modalMediaType === "image" ? "Image Preview" : "Video Preview" }}
          </span>
        </div>
      </template>

      <!-- Media container with max height -->
      <div class="media-container" style="max-height: 70vh; overflow: auto">
        <!-- Image -->
        <div v-if="modalMediaType === 'image'" class="text-center">
          <img
            :src="modalMediaUrl"
            alt="Full size preview"
            class="max-w-full h-auto object-contain mx-auto"
            style="max-height: 65vh"
          />
        </div>

        <!-- Video -->
        <div v-else-if="modalMediaType === 'video'" class="text-center">
          <video
            :src="modalMediaUrl"
            controls
            class="max-w-full h-auto mx-auto"
            style="max-height: 65vh"
          >
            Your browser does not support the video tag.
          </video>
        </div>
      </div>

      <!-- URL info -->
      <div
        class="mt-3 p-2 bg-gray-50 dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700"
      >
        <div
          class="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1"
        >
          URL:
        </div>
        <div
          class="text-xs font-mono text-gray-900 dark:text-gray-100 break-all"
        >
          {{ modalMediaUrl }}
        </div>
      </div>
    </el-dialog>

    <!-- Attribution Notice (Kohaku License requirement) -->
    <div
      class="mt-6 p-3 bg-gray-50 dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700"
    >
      <div class="text-xs text-gray-600 dark:text-gray-400">
        <div class="flex items-center gap-2 mb-1">
          <div class="i-carbon-information" />
          <span class="font-semibold">Dataset Viewer</span>
        </div>
        <p class="text-xs">
          Licensed under Kohaku Software License by KohakuBlueLeaf. Commercial
          usage exceeding trial limits ($25k/year OR 3 months) requires a
          commercial license.
          <a
            href="mailto:kohaku@kblueleaf.net"
            class="text-blue-600 dark:text-blue-400 hover:underline"
          >
            Contact for licensing
          </a>
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-item {
  user-select: none;
}

.file-list-scroll {
  scrollbar-width: thin;
  scrollbar-color: rgb(209 213 219) transparent;
}

.dark .file-list-scroll {
  scrollbar-color: rgb(75 85 99) transparent;
}

.file-list-scroll::-webkit-scrollbar {
  width: 6px;
}

.file-list-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.file-list-scroll::-webkit-scrollbar-thumb {
  background-color: rgb(209 213 219);
  border-radius: 3px;
}

.dark .file-list-scroll::-webkit-scrollbar-thumb {
  background-color: rgb(75 85 99);
}
</style>
