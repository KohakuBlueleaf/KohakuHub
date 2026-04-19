<!-- src/kohaku-hub-ui/src/pages/[type]s/[namespace]/[name]/blob/[branch]/[...file].vue -->
<template>
  <div class="container-main">
    <!-- Breadcrumb -->
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
          {{ repoTypeLabel }}
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
          :to="`/${repoType}s/${namespace}/${name}`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ name }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>{{ fileName }}</el-breadcrumb-item>
    </el-breadcrumb>

    <div v-if="loading" class="text-center py-20">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
    </div>

    <div v-else-if="error" class="text-center py-20">
      <div class="i-carbon-warning text-6xl text-red-500 mb-4" />
      <h2 class="text-2xl font-bold mb-2">File Not Found</h2>
      <p class="text-gray-600 dark:text-gray-400 mb-4">{{ error }}</p>
      <el-button @click="$router.back()">Go Back</el-button>
    </div>

    <div v-else>
      <!-- File Header -->
      <div class="card mb-6">
        <div
          class="flex flex-col sm:flex-row items-start justify-between gap-4"
        >
          <div class="flex items-start gap-3 min-w-0 flex-1">
            <div
              :class="getFileIcon(fileName)"
              class="text-2xl sm:text-3xl flex-shrink-0"
            />
            <div class="min-w-0 flex-1">
              <h1 class="text-lg sm:text-xl md:text-2xl font-bold break-words">
                {{ fileName }}
              </h1>
              <div
                class="text-xs sm:text-sm text-gray-600 dark:text-gray-400 mt-1"
              >
                {{ formatSize(fileSize) }}
                <span v-if="fileSize" class="mx-2">•</span>
                <span>{{ fileExtension || "No extension" }}</span>
              </div>
            </div>
          </div>

          <div
            class="flex flex-wrap sm:flex-nowrap items-center gap-2 w-full sm:w-auto"
          >
            <el-button
              v-if="canEdit"
              @click="editFile"
              size="small"
              class="flex-1 sm:flex-initial"
            >
              <div class="i-carbon-edit inline-block mr-1" />
              <span class="hidden sm:inline">Edit</span>
              <span class="sm:hidden">Edit</span>
            </el-button>
            <el-button
              @click="copyFileUrl"
              size="small"
              class="flex-1 sm:flex-initial"
            >
              <div class="i-carbon-link inline-block mr-1" />
              <span class="hidden sm:inline">Copy URL</span>
              <span class="sm:hidden">Copy</span>
            </el-button>
            <el-button
              type="primary"
              @click="downloadFile"
              size="small"
              class="flex-1 sm:flex-initial"
            >
              <div class="i-carbon-download inline-block mr-1" />
              Download
            </el-button>
            <el-button
              v-if="canDelete"
              @click="confirmDeleteFile"
              type="danger"
              size="small"
              class="flex-1 sm:flex-initial"
              :loading="deleting"
            >
              <div class="i-carbon-trash-can inline-block mr-1" />
              Delete
            </el-button>
          </div>
        </div>

        <!-- File path breadcrumb -->
        <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
          <div class="flex items-center gap-2 text-sm">
            <el-tag size="small">{{ branch }}</el-tag>
            <span class="text-gray-400 dark:text-gray-500">/</span>
            <el-breadcrumb
              separator="/"
              class="text-gray-700 dark:text-gray-300"
            >
              <el-breadcrumb-item
                v-for="(segment, idx) in pathSegments"
                :key="idx"
              >
                <RouterLink
                  v-if="idx < pathSegments.length - 1"
                  :to="getFolderPath(pathSegments.slice(0, idx + 1).join('/'))"
                  class="text-blue-600 dark:text-blue-400 hover:underline"
                >
                  {{ segment }}
                </RouterLink>
                <span v-else class="text-gray-700 dark:text-gray-300">{{
                  segment
                }}</span>
              </el-breadcrumb-item>
            </el-breadcrumb>
          </div>
        </div>
      </div>

      <!-- File Content -->
      <div class="card">
        <!-- Image Preview -->
        <div v-if="isImage" class="text-center">
          <img
            :src="fileUrl"
            :alt="fileName"
            class="max-w-full h-auto mx-auto"
            style="max-height: 800px"
          />
        </div>

        <!-- Video Preview -->
        <div v-else-if="isVideo" class="text-center">
          <video
            :src="fileUrl"
            controls
            class="max-w-full h-auto mx-auto"
            style="max-height: 600px"
            :aria-label="`Video: ${fileName}`"
          >
            <track kind="captions" :label="`${fileName} captions`" />
            Your browser does not support video playback.
          </video>
        </div>

        <!-- Audio Preview -->
        <div v-else-if="isAudio" class="text-center py-8">
          <div
            class="i-carbon-music text-6xl text-gray-400 mb-4 inline-block"
          />
          <audio :src="fileUrl" controls class="w-full max-w-xl mx-auto" />
        </div>

        <!-- PDF Preview -->
        <div v-else-if="isPDF" class="h-[800px]">
          <iframe
            :src="fileUrl"
            class="w-full h-full border-0"
            title="PDF Preview"
          />
        </div>

        <!-- Text/Code Preview -->
        <div v-else-if="canPreviewText">
          <CodeViewer :code="fileContent" :language="fileExtension" />
        </div>

        <!-- Markdown Preview -->
        <div v-else-if="isMarkdown">
          <div
            class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 mb-4 pb-3 border-b border-gray-200 dark:border-gray-700"
          >
            <el-radio-group
              v-model="markdownView"
              size="small"
              class="w-full sm:w-auto"
            >
              <el-radio-button label="preview" class="flex-1 sm:flex-initial"
                >Preview</el-radio-button
              >
              <el-radio-button label="source" class="flex-1 sm:flex-initial"
                >Source</el-radio-button
              >
            </el-radio-group>
            <div class="flex gap-2 w-full sm:w-auto">
              <el-button
                v-if="canEdit"
                size="small"
                type="primary"
                @click="editFile"
                class="flex-1 sm:flex-initial"
              >
                <div class="i-carbon-edit inline-block mr-1" />
                Edit
              </el-button>
              <el-button
                v-if="markdownView === 'source'"
                size="small"
                @click="copyContent"
                class="flex-1 sm:flex-initial"
              >
                <div class="i-carbon-copy inline-block mr-1" />
                Copy
              </el-button>
            </div>
          </div>

          <div v-if="markdownView === 'preview'">
            <MarkdownViewer :content="fileContent" />
          </div>
          <pre
            v-else
            class="text-sm overflow-x-auto bg-gray-50 dark:bg-gray-900 p-4 rounded"
          ><code>{{ fileContent }}</code></pre>
        </div>

        <!-- Too Large / Binary -->
        <div v-else class="text-center py-16">
          <div
            :class="getFileIcon(fileName)"
            class="text-8xl text-gray-300 dark:text-gray-600 mb-4 inline-block"
          />
          <h3 class="text-xl font-semibold mb-2">{{ tooLargeMessage }}</h3>
          <p class="text-gray-600 dark:text-gray-400 mb-6">
            {{
              fileSize > maxPreviewSize
                ? `File is ${formatSize(fileSize)}, too large to preview`
                : "This file type cannot be previewed"
            }}
          </p>
          <el-button type="primary" size="large" @click="downloadFile">
            <div class="i-carbon-download inline-block mr-1" />
            Download File
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import MarkdownViewer from "@/components/common/MarkdownViewer.vue";
import CodeViewer from "@/components/common/CodeViewer.vue";
import { copyToClipboard } from "@/utils/clipboard";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "@/stores/auth";
import { repoAPI } from "@/utils/api";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

// Route params
const repoType = computed(() => {
  const path = route.path;
  if (path.includes("/models/")) return "model";
  if (path.includes("/datasets/")) return "dataset";
  if (path.includes("/spaces/")) return "space";
  return "model";
});
const namespace = computed(() => route.params.namespace);
const name = computed(() => route.params.name);
const branch = computed(() => route.params.branch || "main");
const filePath = computed(() => route.params.file || "");

// Constants
const maxPreviewSize = 100 * 1000; // 100KB

// State
const loading = ref(true);
const error = ref(null);
const fileContent = ref("");
const fileSize = ref(0);
const fileHeaders = ref({});
const markdownView = ref("preview");
const deleting = ref(false);

// Computed
const repoTypeLabel = computed(() => {
  const labels = { model: "Models", dataset: "Datasets", space: "Spaces" };
  return labels[repoType.value] || "Models";
});

const fileName = computed(() => {
  const parts = filePath.value.split("/");
  return parts[parts.length - 1] || "unknown";
});

const fileExtension = computed(() => {
  const match = fileName.value.match(/\.([^.]+)$/);
  return match ? match[1].toLowerCase() : "";
});

const pathSegments = computed(() => {
  return filePath.value ? filePath.value.split("/").filter(Boolean) : [];
});

const fileUrl = computed(() => {
  return `/${repoType.value}s/${namespace.value}/${name.value}/resolve/${branch.value}/${filePath.value}`;
});

const languageLabel = computed(() => {
  const langMap = {
    js: "JavaScript",
    ts: "TypeScript",
    py: "Python",
    java: "Java",
    cpp: "C++",
    c: "C",
    cs: "C#",
    go: "Go",
    rs: "Rust",
    rb: "Ruby",
    php: "PHP",
    swift: "Swift",
    kt: "Kotlin",
    scala: "Scala",
    html: "HTML",
    css: "CSS",
    scss: "SCSS",
    json: "JSON",
    xml: "XML",
    yaml: "YAML",
    yml: "YAML",
    toml: "TOML",
    ini: "INI",
    sh: "Shell",
    bash: "Bash",
    sql: "SQL",
    r: "R",
    m: "MATLAB",
    jl: "Julia",
  };
  return langMap[fileExtension.value] || fileExtension.value.toUpperCase();
});

const lineCount = computed(() => {
  return fileContent.value ? fileContent.value.split("\n").length : 0;
});

const isImage = computed(() => {
  return ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"].includes(
    fileExtension.value,
  );
});

const isVideo = computed(() => {
  return ["mp4", "webm", "ogg", "mov", "avi"].includes(fileExtension.value);
});

const isAudio = computed(() => {
  return ["mp3", "wav", "ogg", "flac", "m4a", "aac"].includes(
    fileExtension.value,
  );
});

const isPDF = computed(() => {
  return fileExtension.value === "pdf";
});

const isMarkdown = computed(() => {
  return ["md", "markdown"].includes(fileExtension.value);
});

const isTextFile = computed(() => {
  const textExtensions = [
    "txt",
    "log",
    "csv",
    "tsv",
    "js",
    "ts",
    "jsx",
    "tsx",
    "vue",
    "py",
    "java",
    "cpp",
    "c",
    "h",
    "hpp",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "swift",
    "kt",
    "scala",
    "m",
    "r",
    "jl",
    "html",
    "htm",
    "css",
    "scss",
    "sass",
    "less",
    "json",
    "xml",
    "yaml",
    "yml",
    "toml",
    "ini",
    "cfg",
    "conf",
    "sh",
    "bash",
    "zsh",
    "fish",
    "ps1",
    "bat",
    "cmd",
    "sql",
    "graphql",
    "proto",
    "dockerfile",
    "makefile",
    "gitignore",
    "env",
    "editorconfig",
    "eslintrc",
    "prettierrc",
  ];
  return (
    textExtensions.includes(fileExtension.value) ||
    fileName.value.toLowerCase().includes("dockerfile")
  );
});

const canPreviewText = computed(() => {
  return (
    isTextFile.value && fileSize.value <= maxPreviewSize && !isMarkdown.value
  );
});

const tooLargeMessage = computed(() => {
  if (fileSize.value > maxPreviewSize && isTextFile.value) {
    return "File Too Large";
  }
  if (isImage.value || isVideo.value || isAudio.value || isPDF.value) {
    return "Media File";
  }
  return "Binary File";
});

const canEdit = computed(() => {
  // Only allow editing text files that are not too large
  // Check if user can write to this namespace (own repo or org member)
  return (
    authStore.isAuthenticated &&
    authStore.canWriteToNamespace(namespace.value) &&
    (isTextFile.value || isMarkdown.value) &&
    fileSize.value <= maxPreviewSize
  );
});

const canDelete = computed(() => {
  // Allow deleting files if user has write access to the namespace
  return (
    authStore.isAuthenticated && authStore.canWriteToNamespace(namespace.value)
  );
});

// Methods
function getFileIcon(filename) {
  const ext = filename.split(".").pop()?.toLowerCase();

  if (isImage.value) return "i-carbon-image text-purple-500";
  if (isVideo.value) return "i-carbon-video text-red-500";
  if (isAudio.value) return "i-carbon-music text-green-500";
  if (isPDF.value) return "i-carbon-document-pdf text-red-600";
  if (isMarkdown.value) return "i-carbon-logo-markdown text-blue-500";
  if (["js", "ts", "jsx", "tsx"].includes(ext))
    return "i-carbon-code text-yellow-500";
  if (["py"].includes(ext)) return "i-carbon-code text-blue-600";
  if (["json", "xml", "yaml", "yml"].includes(ext))
    return "i-carbon-data-structured text-orange-500";
  if (["zip", "tar", "gz", "rar", "7z"].includes(ext))
    return "i-carbon-zip-archive text-gray-500";

  return "i-carbon-document text-gray-500";
}

function formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1000 * 1000) return (bytes / 1000).toFixed(1) + " KB";
  if (bytes < 1000 * 1000 * 1000)
    return (bytes / (1000 * 1000)).toFixed(1) + " MB";
  return (bytes / (1000 * 1000 * 1000)).toFixed(1) + " GB";
}

async function loadFileInfo() {
  loading.value = true;
  error.value = null;

  try {
    // Note: Presigned URLs are signed for GET method only
    // HEAD requests will fail with 403/signature mismatch
    // For text/markdown files, we fetch content directly (GET)
    // For media files, size is not critical - browser will handle it

    // Load content if it's previewable text
    if (canPreviewText.value || isMarkdown.value) {
      let retries = 0;
      const maxRetries = 10;

      while (retries < maxRetries) {
        const response = await fetch(fileUrl.value);

        if (response.ok) {
          fileSize.value = parseInt(
            response.headers.get("content-length") || "0",
          );
          fileHeaders.value = Object.fromEntries(response.headers.entries());
          fileContent.value = await response.text();
          break;
        }

        // If 404, might be commit still processing - retry
        if (response.status === 404 && retries < maxRetries - 1) {
          console.log(
            `File not ready yet (attempt ${retries + 1}/${maxRetries}), retrying...`,
          );
          await new Promise((resolve) => setTimeout(resolve, 500)); // Wait 500ms
          retries++;
          continue;
        }

        // Other errors or max retries reached
        throw new Error("File not found");
      }
    } else {
      // For media/binary files, we don't need size upfront
      // Just set a flag that file exists (img/video tags will handle loading)
      // If they fail to load, browser will show broken image/video
      fileSize.value = 0; // Unknown, will be loaded by browser
      fileHeaders.value = {};
    }
  } catch (err) {
    error.value = err.message || "Failed to load file";
    console.error("Failed to load file:", err);
  } finally {
    loading.value = false;
  }
}

function downloadFile() {
  window.open(fileUrl.value, "_blank");
}

async function copyFileUrl() {
  const fullUrl = window.location.origin + fileUrl.value;
  const success = await copyToClipboard(fullUrl);
  if (success) {
    ElMessage.success("File URL copied to clipboard");
  } else {
    ElMessage.error("Failed to copy");
  }
}

async function copyContent() {
  const success = await copyToClipboard(fileContent.value);
  if (success) {
    ElMessage.success("Content copied to clipboard");
  } else {
    ElMessage.error("Failed to copy");
  }
}

function getFolderPath(folderPath) {
  return `/${repoType.value}s/${namespace.value}/${name.value}/tree/${branch.value}/${folderPath}`;
}

function editFile() {
  router.push(
    `/${repoType.value}s/${namespace.value}/${name.value}/edit/${branch.value}/${filePath.value}`,
  );
}

async function confirmDeleteFile() {
  try {
    await ElMessageBox.confirm(
      `Are you sure you want to delete "${fileName.value}"? This action cannot be undone.`,
      "Delete File",
      {
        confirmButtonText: "Delete",
        cancelButtonText: "Cancel",
        type: "warning",
        confirmButtonClass: "el-button--danger",
      },
    );

    // User confirmed, proceed with deletion
    await deleteFile();
  } catch {
    // User cancelled - do nothing
  }
}

async function deleteFile() {
  deleting.value = true;

  try {
    // Create commit with deletedFile operation
    await repoAPI.commitFiles(
      repoType.value,
      namespace.value,
      name.value,
      branch.value,
      {
        message: `Delete ${filePath.value}`,
        operations: [
          {
            operation: "deletedFile",
            path: filePath.value,
          },
        ],
      },
    );

    ElMessage.success(`File "${fileName.value}" deleted successfully`);

    // Navigate back to folder or repo root
    if (pathSegments.value.length > 1) {
      const folderPath = pathSegments.value.slice(0, -1).join("/");
      router.push(
        `/${repoType.value}s/${namespace.value}/${name.value}/tree/${branch.value}/${folderPath}`,
      );
    } else {
      router.push(
        `/${repoType.value}s/${namespace.value}/${name.value}/tree/${branch.value}`,
      );
    }
  } catch (err) {
    console.error("Failed to delete file:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to delete file";
    ElMessage.error(errorMsg);
  } finally {
    deleting.value = false;
  }
}

// Lifecycle
onMounted(() => {
  loadFileInfo();
});
</script>
