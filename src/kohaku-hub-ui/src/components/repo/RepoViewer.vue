<!-- src/components/repo/RepoViewer.vue -->
<template>
  <div class="container-main">
    <el-breadcrumb separator="/" class="mb-6 text-gray-700 dark:text-gray-300">
      <el-breadcrumb-item :to="{ path: '/' }">Home</el-breadcrumb-item>
      <el-breadcrumb-item :to="{ path: `/${repoType}s` }">
        {{ repoTypeLabel }}
      </el-breadcrumb-item>
      <el-breadcrumb-item :to="{ path: `/${namespace}` }">
        {{ namespace }}
      </el-breadcrumb-item>
      <el-breadcrumb-item>{{ name }}</el-breadcrumb-item>
    </el-breadcrumb>

    <div v-if="loading" class="text-center py-20">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
    </div>

    <div v-else-if="error" class="text-center py-20">
      <div class="i-carbon-warning text-6xl text-red-500 mb-4" />
      <h2 class="text-2xl font-bold mb-2">Repository Not Found</h2>
      <p class="text-gray-600 mb-4">{{ error }}</p>
      <el-button @click="$router.back()">Go Back</el-button>
    </div>

    <div v-else class="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6">
      <!-- Main Content -->
      <main class="min-w-0">
        <!-- Repo Header -->
        <div class="card mb-6">
          <div
            class="flex flex-col sm:flex-row items-start justify-between gap-4 mb-4"
          >
            <div class="flex items-start gap-3">
              <div
                :class="getIconClass(repoType)"
                class="text-3xl sm:text-4xl flex-shrink-0"
              />
              <div class="min-w-0">
                <h1
                  class="text-xl sm:text-2xl lg:text-3xl font-bold break-words"
                >
                  {{ repoInfo?.id }}
                </h1>
                <div class="flex items-center gap-2 mt-1">
                  <RouterLink
                    :to="`/${namespace}`"
                    class="text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    {{ namespace }}
                  </RouterLink>
                  <span class="text-gray-400 dark:text-gray-500">/</span>
                  <span class="text-gray-700 dark:text-gray-300">{{
                    name
                  }}</span>
                </div>
              </div>
            </div>

            <div class="flex items-center gap-2">
              <el-tag v-if="repoInfo?.private" type="warning">
                <div class="i-carbon-locked inline-block mr-1" />
                Private
              </el-tag>
              <el-tag v-else type="success">
                <div class="i-carbon-unlocked inline-block mr-1" />
                Public
              </el-tag>
            </div>
          </div>

          <!-- Stats -->
          <div
            class="flex flex-wrap items-center gap-3 sm:gap-6 text-xs sm:text-sm text-gray-600 dark:text-gray-400"
          >
            <div class="flex items-center gap-1">
              <div class="i-carbon-download" />
              <span>{{ repoInfo?.downloads || 0 }} downloads</span>
            </div>
            <div class="flex items-center gap-1">
              <div class="i-carbon-favorite" />
              <span>{{ repoInfo?.likes || 0 }} likes</span>
            </div>
            <div class="flex items-center gap-1">
              <div class="i-carbon-calendar" />
              <span>Updated {{ formatDate(repoInfo?.lastModified) }}</span>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex flex-col sm:flex-row gap-1 mt-4">
            <el-button
              type="primary"
              @click="showCloneDialog = true"
              size="small"
              class="w-full sm:w-auto m-0 sm:m-1"
            >
              <div class="i-carbon-download inline-block mr-1" />
              Clone
            </el-button>
            <div class="w-0 h-0 p-0 m-0"></div>
            <el-button
              @click="downloadRepo"
              size="small"
              class="w-full sm:w-auto m-0 sm:m-1"
              plain
            >
              <div class="i-carbon-document-download inline-block mr-1" />
              Download
            </el-button>
            <div class="w-0 h-0 p-0 m-0"></div>
            <el-button
              v-if="isOwner"
              @click="navigateToSettings"
              size="small"
              class="w-full sm:w-auto m-0 sm:m-1"
              plain
            >
              <div class="i-carbon-settings inline-block mr-1" />
              Settings
            </el-button>
          </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="mb-6 -mx-4 sm:mx-0 px-4 sm:px-0 overflow-x-auto">
          <div
            class="flex gap-1 border-b border-gray-200 dark:border-gray-700 min-w-max sm:min-w-0"
          >
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'card'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('card')"
            >
              {{
                repoType === "model"
                  ? "Model Card"
                  : repoType === "dataset"
                    ? "Dataset Card"
                    : "App"
              }}
            </button>
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'files'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('files')"
            >
              Files
            </button>
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'commits'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('commits')"
            >
              Commits
            </button>
          </div>
        </div>

        <!-- Tab Content -->
        <div v-if="activeTab === 'card'" class="card overflow-hidden">
          <div class="max-w-full overflow-x-auto">
            <div v-if="readmeLoading" class="text-center py-12">
              <el-icon class="is-loading" :size="40">
                <div class="i-carbon-loading" />
              </el-icon>
              <p class="mt-4 text-gray-500 dark:text-gray-400">
                Loading README...
              </p>
            </div>
            <div v-else-if="readmeContent">
              <MarkdownViewer
                :content="readmeContent"
                :repo-type="repoType"
                :namespace="namespace"
                :name="name"
                :branch="currentBranch"
              />
            </div>
            <div
              v-else
              class="text-center py-12 text-gray-500 dark:text-gray-400"
            >
              <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
              <p>No README.md found</p>
              <el-button
                v-if="isOwner"
                class="mt-4"
                type="primary"
                @click="createReadme"
              >
                Create README.md
              </el-button>
            </div>
          </div>
        </div>

        <div v-if="activeTab === 'files'" class="card">
          <div
            class="mb-4 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3"
          >
            <div class="flex items-center gap-2">
              <el-select
                v-model="currentBranch"
                size="small"
                class="w-full sm:w-37"
                @change="handleBranchChange"
              >
                <el-option label="main" value="main" />
              </el-select>
              <span
                class="text-sm text-gray-600 dark:text-gray-400 whitespace-nowrap"
              >
                {{ fileTree.length }}
                {{ fileTree.length === 1 ? "file" : "files" }}
              </span>
            </div>

            <div
              class="flex flex-col sm:flex-row items-stretch sm:items-center gap-2"
            >
              <el-button
                v-if="isOwner"
                size="small"
                type="primary"
                @click="navigateToUpload"
                class="w-full sm:w-auto"
              >
                <div class="i-carbon-cloud-upload inline-block mr-1" />
                Upload Files
              </el-button>
              <el-input
                v-model="fileSearchQuery"
                placeholder="Search files..."
                size="small"
                class="w-full sm:w-50"
                clearable
              >
                <template #prefix>
                  <div class="i-carbon-search" />
                </template>
              </el-input>
            </div>
          </div>

          <!-- Breadcrumb for current path -->
          <div v-if="currentPath" class="mb-3 text-sm">
            <el-breadcrumb
              separator="/"
              class="text-gray-700 dark:text-gray-300"
            >
              <el-breadcrumb-item>
                <RouterLink
                  :to="`/${repoType}s/${namespace}/${name}/tree/${currentBranch}`"
                  class="text-blue-600 dark:text-blue-400 hover:underline"
                >
                  root
                </RouterLink>
              </el-breadcrumb-item>
              <el-breadcrumb-item
                v-for="(segment, idx) in pathSegments"
                :key="idx"
              >
                <RouterLink
                  :to="`/${repoType}s/${namespace}/${name}/tree/${currentBranch}/${pathSegments.slice(0, idx + 1).join('/')}`"
                  class="text-blue-600 dark:text-blue-400 hover:underline"
                >
                  {{ segment }}
                </RouterLink>
              </el-breadcrumb-item>
            </el-breadcrumb>
          </div>

          <!-- File List -->
          <div class="divide-y divide-gray-200 dark:divide-gray-700">
            <div v-if="filesLoading" class="py-12 text-center">
              <el-icon class="is-loading" :size="40">
                <div class="i-carbon-loading" />
              </el-icon>
              <p class="mt-4 text-gray-500 dark:text-gray-400">
                Loading files...
              </p>
            </div>
            <template v-else>
              <!-- Header Row (desktop only) -->
              <div
                class="hidden md:grid md:grid-cols-[auto_1fr_120px_150px] gap-3 py-2 px-2 text-sm font-medium text-gray-600 dark:text-gray-400 border-b"
              >
                <div></div>
                <!-- Icon column -->
                <div>Name</div>
                <div class="text-right">Size</div>
                <div class="text-right">Last Modified</div>
              </div>

              <!-- File Rows -->
              <div
                v-for="file in filteredFiles"
                :key="file.path"
                class="py-3 grid grid-cols-[auto_1fr_auto] md:grid-cols-[auto_1fr_120px_150px] gap-3 items-center hover:bg-gray-50 dark:hover:bg-gray-700 px-2 cursor-pointer transition-colors"
                @click="handleFileClick(file)"
              >
                <div
                  :class="
                    file.type === 'directory'
                      ? 'i-carbon-folder text-blue-500'
                      : 'i-carbon-document text-gray-500 dark:text-gray-400'
                  "
                  class="text-xl flex-shrink-0"
                />
                <div class="min-w-0">
                  <div class="font-medium truncate">
                    {{ getFileName(file.path) }}
                  </div>
                </div>
                <div
                  class="text-sm text-gray-500 dark:text-gray-400 text-right"
                >
                  {{ formatSize(file.size) }}
                </div>
                <div
                  class="hidden md:block text-sm text-gray-500 dark:text-gray-400 text-right"
                >
                  {{ formatLastModified(file.lastModified) }}
                </div>
              </div>

              <div
                v-if="filteredFiles.length === 0"
                class="py-12 text-center text-gray-500 dark:text-gray-400"
              >
                <div
                  class="i-carbon-document-blank text-6xl mb-4 inline-block"
                />
                <p>No files found</p>
              </div>
            </template>
          </div>
        </div>

        <div v-if="activeTab === 'commits'" class="card">
          <h2 class="text-xl font-semibold mb-4">Commit History</h2>

          <div
            v-if="commitsLoading && commits.length === 0"
            class="text-center py-12"
          >
            <el-icon class="is-loading" :size="40">
              <div class="i-carbon-renew" />
            </el-icon>
            <p class="mt-4 text-gray-500 dark:text-gray-400">
              Loading commits...
            </p>
          </div>

          <div v-else-if="commits.length > 0" class="space-y-3">
            <div
              v-for="commit in commits"
              :key="commit.id"
              class="border border-gray-200 dark:border-gray-700 rounded-lg p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors cursor-pointer"
              @click="viewCommit(commit.id)"
            >
              <div class="flex items-start gap-3">
                <div
                  class="i-carbon-commit text-2xl text-blue-500 flex-shrink-0 mt-1"
                />
                <div class="flex-1 min-w-0">
                  <div class="font-medium text-sm mb-1 hover:text-blue-600 dark:hover:text-blue-400 transition-colors">
                    {{ commit.title }}
                  </div>
                  <div
                    class="flex items-center gap-3 text-xs text-gray-600 dark:text-gray-400"
                  >
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-user-avatar" />
                      <span>{{ commit.author }}</span>
                    </div>
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-calendar" />
                      <span>{{ formatCommitDate(commit.date) }}</span>
                    </div>
                    <div class="font-mono text-xs">
                      {{ commit.id.slice(0, 7) }}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Load More Button -->
            <div v-if="commitsHasMore" class="text-center pt-4">
              <el-button
                @click="loadMoreCommits"
                :loading="commitsLoading"
                plain
              >
                Load More Commits
              </el-button>
            </div>
          </div>

          <div
            v-else
            class="text-center py-12 text-gray-500 dark:text-gray-400"
          >
            <div class="i-carbon-branch text-6xl mb-4 inline-block" />
            <p>No commits yet</p>
          </div>
        </div>
      </main>

      <!-- Sidebar -->
      <aside class="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <!-- Owner Info -->
        <div class="card">
          <h3 class="font-semibold mb-3">Owner</h3>
          <RouterLink
            :to="`/${namespace}`"
            class="flex items-center gap-2 hover:bg-gray-50 dark:hover:bg-gray-700 p-2 rounded transition-colors"
          >
            <div
              class="i-carbon-user-avatar text-3xl text-gray-400 dark:text-gray-500"
            />
            <span class="font-medium">{{ namespace }}</span>
          </RouterLink>
        </div>

        <!-- Tags -->
        <div v-if="repoInfo?.tags && repoInfo.tags.length" class="card">
          <h3 class="font-semibold mb-3">Tags</h3>
          <div class="flex flex-wrap gap-2">
            <el-tag v-for="tag in repoInfo.tags" :key="tag" size="small">
              {{ tag }}
            </el-tag>
          </div>
        </div>

        <!-- Metadata -->
        <div class="card">
          <h3 class="font-semibold mb-3">Metadata</h3>
          <div class="space-y-2 text-sm">
            <div>
              <span class="text-gray-600 dark:text-gray-400">Type:</span>
              <span class="ml-2 font-medium">{{ repoTypeLabel }}</span>
            </div>
            <div>
              <span class="text-gray-600 dark:text-gray-400">Created:</span>
              <span class="ml-2">{{ formatDate(repoInfo?.createdAt) }}</span>
            </div>
            <div v-if="repoInfo?.lastModified">
              <span class="text-gray-600 dark:text-gray-400">Updated:</span>
              <span class="ml-2">{{ formatDate(repoInfo?.lastModified) }}</span>
            </div>
            <div v-if="repoInfo?.sha">
              <span class="text-gray-600 dark:text-gray-400">Commit:</span>
              <span class="ml-2 font-mono text-xs">{{
                repoInfo.sha.slice(0, 7)
              }}</span>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- Clone Dialog -->
    <el-dialog v-model="showCloneDialog" title="Clone Repository" width="700px">
      <div class="space-y-4">
        <!-- Git Clone Section -->
        <div>
          <label class="block text-sm font-medium mb-2 flex items-center gap-2">
            <div class="i-carbon-code text-lg" />
            Clone with Git (Recommended)
          </label>
          <el-input :value="gitCloneUrl" readonly class="font-mono text-sm">
            <template #append>
              <el-button @click="copyGitCloneUrl">
                <div class="i-carbon-copy" />
              </el-button>
            </template>
          </el-input>

          <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4 rounded mt-3 text-sm">
            <p class="font-medium text-blue-900 dark:text-blue-100 mb-2">
              <div class="i-carbon-terminal inline-block mr-1" />
              Quick Start:
            </p>
            <pre class="text-xs overflow-x-auto bg-white dark:bg-gray-800 p-2 rounded mb-2 text-gray-800 dark:text-gray-200">git clone {{ gitCloneUrl }}</pre>
            <p class="text-blue-800 dark:text-blue-200 text-xs mt-2">
              This will clone the repository with all files and commit history.
            </p>
          </div>
        </div>

        <!-- Authentication Section -->
        <div class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 p-4 rounded text-sm">
          <p class="font-medium text-yellow-900 dark:text-yellow-100 mb-2">
            <div class="i-carbon-locked inline-block mr-1" />
            Authentication Required
          </p>
          <p class="text-yellow-800 dark:text-yellow-200 text-xs mb-2">
            For private repositories or push operations, you'll need to authenticate using an access token:
          </p>
          <ol class="list-decimal list-inside space-y-1 text-xs text-yellow-900 dark:text-yellow-100 ml-2">
            <li>Generate an access token in your settings</li>
            <li>Use your username and token when prompted</li>
            <li>Username: <code class="bg-white dark:bg-gray-800 px-1 py-0.5 rounded">{{ authStore.user?.username || 'your-username' }}</code></li>
            <li>Password: <code class="bg-white dark:bg-gray-800 px-1 py-0.5 rounded">your-access-token</code></li>
          </ol>
        </div>

        <!-- HuggingFace CLI Alternative -->
        <div>
          <label class="block text-sm font-medium mb-2 flex items-center gap-2">
            <div class="i-carbon-download text-lg" />
            Alternative: HuggingFace CLI
          </label>
          <div class="bg-gray-50 dark:bg-gray-900 p-4 rounded text-sm border border-gray-200 dark:border-gray-700">
            <p class="mb-2 text-gray-600 dark:text-gray-400">Set the endpoint:</p>
            <pre class="text-xs overflow-x-auto bg-white dark:bg-gray-800 p-2 rounded mb-3 text-gray-800 dark:text-gray-200">export HF_ENDPOINT={{ baseUrl }}</pre>
            <p class="text-gray-600 dark:text-gray-400 mb-1">
              Download using
              <code class="bg-white dark:bg-gray-800 px-1 py-0.5 rounded">huggingface-cli</code>:
            </p>
            <pre class="text-xs overflow-x-auto bg-white dark:bg-gray-800 p-2 rounded text-gray-800 dark:text-gray-200">huggingface-cli download {{ repoInfo?.id }}</pre>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { repoAPI } from "@/utils/api";
import { useAuthStore } from "@/stores/auth";
import MarkdownViewer from "@/components/common/MarkdownViewer.vue";
import { ElMessage } from "element-plus";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

/**
 * @typedef {Object} Props
 * @property {string} repoType - Repository type (model/dataset/space)
 * @property {string} namespace - Repository namespace
 * @property {string} name - Repository name
 * @property {string} [branch] - Current branch
 * @property {string} [currentPath] - Current folder path
 * @property {string} [tab] - Active tab (card/files/commits)
 */
const props = defineProps({
  repoType: { type: String, required: true },
  namespace: { type: String, required: true },
  name: { type: String, required: true },
  branch: { type: String, default: "main" },
  currentPath: { type: String, default: "" },
  tab: { type: String, default: "card" },
});

const router = useRouter();
const authStore = useAuthStore();

// State
const loading = ref(true);
const error = ref(null);
const repoInfo = ref(null);
const currentBranch = ref(props.branch);
const fileTree = ref([]);
const commits = ref([]);
const commitsLoading = ref(false);
const commitsHasMore = ref(false);
const commitsNextCursor = ref(null);
const filesLoading = ref(true);
const readmeContent = ref("");
const readmeLoading = ref(true);
const showCloneDialog = ref(false);
const fileSearchQuery = ref("");

const baseUrl = window.location.origin;

// Computed
const activeTab = computed(() => props.tab);

const repoTypeLabel = computed(() => {
  const labels = { model: "Models", dataset: "Datasets", space: "Spaces" };
  return labels[props.repoType] || "Models";
});

const isOwner = computed(() => {
  return authStore.canWriteToNamespace(props.namespace);
});

const cloneUrl = computed(() => {
  return `${baseUrl}/${repoInfo.value?.id}.git`;
});

const gitCloneUrl = computed(() => {
  return `${baseUrl}/${props.namespace}/${props.name}.git`;
});

const pathSegments = computed(() => {
  return props.currentPath ? props.currentPath.split("/").filter(Boolean) : [];
});

const filteredFiles = computed(() => {
  // Backend now provides folder stats, so just filter
  if (!fileSearchQuery.value) return fileTree.value;

  const query = fileSearchQuery.value.toLowerCase();
  return fileTree.value.filter((file) =>
    getFileName(file.path).toLowerCase().includes(query),
  );
});

// Methods
function getIconClass(type) {
  const icons = {
    model: "i-carbon-model text-blue-500",
    dataset: "i-carbon-data-table text-green-500",
    space: "i-carbon-application text-purple-500",
  };
  return icons[type] || icons.model;
}

function formatDate(date) {
  return date ? dayjs(date).fromNow() : "Unknown";
}

function formatSize(bytes) {
  if (!bytes || bytes === 0) return "-";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1024 * 1024 * 1024)
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
}

function formatLastModified(dateString) {
  if (!dateString) return "-";
  try {
    return dayjs(dateString).fromNow();
  } catch (e) {
    return "-";
  }
}

function getFileName(path) {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function navigateToTab(tab) {
  if (tab === "files") {
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}`,
    );
  } else if (tab === "commits") {
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/commits/${currentBranch.value}`,
    );
  } else {
    router.push(`/${props.repoType}s/${props.namespace}/${props.name}`);
  }
}

function navigateToSettings() {
  router.push(`/${props.repoType}s/${props.namespace}/${props.name}/settings`);
}

function navigateToUpload() {
  router.push(
    `/${props.repoType}s/${props.namespace}/${props.name}/upload/${currentBranch.value}`,
  );
}

function viewCommit(commitId) {
  router.push(
    `/${props.repoType}s/${props.namespace}/${props.name}/commit/${commitId}`,
  );
}

function handleBranchChange() {
  if (activeTab.value === "files") {
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}`,
    );
  }
}

async function loadRepoInfo() {
  loading.value = true;
  error.value = null;

  try {
    const { data } = await repoAPI.getInfo(
      props.repoType,
      props.namespace,
      props.name,
    );
    repoInfo.value = data;
  } catch (err) {
    error.value = err.response?.data?.detail || "Failed to load repository";
    console.error("Failed to load repo info:", err);
  } finally {
    loading.value = false;
  }
}

async function loadFileTree() {
  filesLoading.value = true;
  try {
    const { data } = await repoAPI.listTree(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      props.currentPath ? `/${props.currentPath}` : "",
      { recursive: false },
    );

    fileTree.value = data.sort((a, b) => {
      if (a.type === "directory" && b.type !== "directory") return -1;
      if (a.type !== "directory" && b.type === "directory") return 1;
      return a.path.localeCompare(b.path);
    });
  } catch (err) {
    console.error("Failed to load file tree:", err);
    fileTree.value = [];
  } finally {
    filesLoading.value = false;
  }
}

async function loadReadme() {
  readmeLoading.value = true;
  try {
    const readmeFile = fileTree.value.find(
      (f) => f.type === "file" && f.path.toLowerCase().endsWith("readme.md"),
    );

    if (!readmeFile) {
      readmeContent.value = "";
      return;
    }

    const downloadUrl = `/${props.repoType}s/${props.namespace}/${props.name}/resolve/${currentBranch.value}/${readmeFile.path}`;
    const response = await fetch(downloadUrl);

    if (response.ok) {
      readmeContent.value = await response.text();
    }
  } catch (err) {
    console.error("Failed to load README:", err);
    readmeContent.value = "";
  } finally {
    readmeLoading.value = false;
  }
}

async function loadCommits() {
  commitsLoading.value = true;
  try {
    const { data } = await repoAPI.listCommits(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      { limit: 20 },
    );

    commits.value = data.commits || [];
    commitsHasMore.value = data.hasMore || false;
    commitsNextCursor.value = data.nextCursor || null;
  } catch (err) {
    console.error("Failed to load commits:", err);
    commits.value = [];
  } finally {
    commitsLoading.value = false;
  }
}

async function loadMoreCommits() {
  if (!commitsHasMore.value || commitsLoading.value) return;

  commitsLoading.value = true;
  try {
    const { data } = await repoAPI.listCommits(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      { limit: 20, after: commitsNextCursor.value },
    );

    commits.value.push(...(data.commits || []));
    commitsHasMore.value = data.hasMore || false;
    commitsNextCursor.value = data.nextCursor || null;
  } catch (err) {
    console.error("Failed to load more commits:", err);
  } finally {
    commitsLoading.value = false;
  }
}

function handleFileClick(file) {
  if (file.type === "directory") {
    // Navigate to folder using tree route
    const newPath = props.currentPath
      ? `${props.currentPath}/${file.path}`
      : file.path;
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}/${newPath}`,
    );
  } else {
    // Navigate to file viewer using blob route
    const fullPath = props.currentPath
      ? `${props.currentPath}/${file.path}`
      : file.path;
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/blob/${currentBranch.value}/${fullPath}`,
    );
  }
}

function downloadRepo() {
  ElMessage.info("Download functionality coming soon");
}

function formatCommitDate(timestamp) {
  if (!timestamp) return "Unknown";
  return dayjs.unix(timestamp).fromNow();
}

async function createReadme() {
  try {
    const readmeContent = `# ${props.name}\n\nAdd your project description here.\n`;

    // Commit the README file using the commit API
    await repoAPI.commitFiles(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      {
        message: "Create README.md",
        files: [
          {
            path: "README.md",
            content: readmeContent,
          },
        ],
      },
    );

    ElMessage.success("README.md created successfully");

    // Reload file tree and README
    await loadFileTree();
    await loadReadme();
  } catch (err) {
    console.error("Failed to create README:", err);
    ElMessage.error("Failed to create README.md");
  }
}

function copyCloneUrl() {
  navigator.clipboard.writeText(cloneUrl.value);
  ElMessage.success("Clone URL copied to clipboard");
}

function copyGitCloneUrl() {
  navigator.clipboard.writeText(gitCloneUrl.value);
  ElMessage.success("Git clone URL copied to clipboard");
}

// Watchers
watch(
  () => props.currentPath,
  () => {
    if (activeTab.value === "files") {
      loadFileTree();
    }
  },
);

watch(
  () => props.branch,
  (newBranch) => {
    currentBranch.value = newBranch;
    if (activeTab.value === "files") {
      loadFileTree();
    }
  },
);

watch(
  () => props.tab,
  async (newTab) => {
    if (newTab === "files" && fileTree.value.length === 0) {
      await loadFileTree();
    } else if (newTab === "card" && !readmeContent.value) {
      if (fileTree.value.length === 0) {
        await loadFileTree();
      }
      await loadReadme();
    } else if (newTab === "commits" && commits.value.length === 0) {
      await loadCommits();
    }
  },
);

watch(
  fileTree,
  () => {
    if (activeTab.value === "card" && !readmeContent.value) {
      loadReadme();
    }
  },
  { immediate: false },
);

// Lifecycle
onMounted(async () => {
  await loadRepoInfo();

  if (activeTab.value === "files") {
    await loadFileTree();
  } else if (activeTab.value === "card") {
    await loadFileTree();
    await loadReadme();
  } else if (activeTab.value === "commits") {
    await loadCommits();
  }
});
</script>
