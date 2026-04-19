<!-- src/pages/[username]/storage.vue -->
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
          :to="`/${username}`"
          class="text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ username }}
        </RouterLink>
      </el-breadcrumb-item>
      <el-breadcrumb-item>Storage</el-breadcrumb-item>
    </el-breadcrumb>

    <!-- Header -->
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-3xl font-bold">Storage Breakdown</h1>
      <el-button @click="$router.push(`/${username}`)">
        <div class="i-carbon-arrow-left inline-block mr-1" />
        Back to Profile
      </el-button>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="text-center py-20">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
      <p class="mt-4 text-gray-500 dark:text-gray-400">
        Loading storage information...
      </p>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="text-center py-20">
      <div class="i-carbon-warning text-6xl text-red-500 mb-4" />
      <h2 class="text-2xl font-bold mb-2">Error</h2>
      <p class="text-gray-600 mb-4">{{ error }}</p>
      <el-button @click="$router.push(`/${username}`)">
        <div class="i-carbon-arrow-left inline-block mr-1" />
        Back to Profile
      </el-button>
    </div>

    <!-- Content -->
    <div v-else>
      <!-- Summary Card -->
      <div class="card mb-6">
        <h2 class="text-xl font-semibold mb-4">Summary</h2>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <div class="text-sm text-gray-600 dark:text-gray-400">
              Total Repositories
            </div>
            <div class="text-2xl font-bold">
              {{ storageData?.total_repos || 0 }}
            </div>
          </div>
          <div>
            <div class="text-sm text-gray-600 dark:text-gray-400">
              Total Storage Used
            </div>
            <div class="text-2xl font-bold">{{ totalStorage }}</div>
          </div>
          <div>
            <div class="text-sm text-gray-600 dark:text-gray-400">
              Largest Repository
            </div>
            <div class="text-2xl font-bold">{{ largestRepoSize }}</div>
          </div>
        </div>
      </div>

      <!-- Repositories Table -->
      <div class="card">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-xl font-semibold">Repositories by Storage</h2>
          <el-input
            v-model="searchQuery"
            placeholder="Search repositories..."
            class="w-64"
            clearable
          >
            <template #prefix>
              <div class="i-carbon-search" />
            </template>
          </el-input>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-800 border-b">
              <tr>
                <th class="px-4 py-3 text-left font-semibold">Repository</th>
                <th class="px-4 py-3 text-left font-semibold">Type</th>
                <th class="px-4 py-3 text-left font-semibold">Visibility</th>
                <th class="px-4 py-3 text-right font-semibold">Storage Used</th>
                <th class="px-4 py-3 text-right font-semibold">Quota</th>
                <th class="px-4 py-3 text-right font-semibold">% Used</th>
                <th class="px-4 py-3 text-left font-semibold">Created</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="repo in filteredRepos"
                :key="repo.repo_id"
                class="border-b hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                @click="goToRepo(repo)"
              >
                <td class="px-4 py-3">
                  <div class="flex items-center gap-2">
                    <div
                      :class="getRepoIcon(repo.repo_type)"
                      class="text-lg flex-shrink-0"
                    />
                    <div>
                      <div class="font-medium">{{ repo.name }}</div>
                      <div
                        v-if="repo.is_inheriting"
                        class="text-xs text-gray-500"
                      >
                        <div class="i-carbon-information inline-block mr-1" />
                        Inheriting quota
                      </div>
                    </div>
                  </div>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-400">
                  <el-tag size="small">{{ repo.repo_type }}</el-tag>
                </td>
                <td class="px-4 py-3">
                  <el-tag v-if="repo.private" type="warning" size="small">
                    <div class="i-carbon-locked inline-block mr-1" />
                    Private
                  </el-tag>
                  <el-tag v-else type="success" size="small">
                    <div class="i-carbon-unlocked inline-block mr-1" />
                    Public
                  </el-tag>
                </td>
                <td class="px-4 py-3 text-right font-mono">
                  {{ formatSize(repo.used_bytes) }}
                </td>
                <td
                  class="px-4 py-3 text-right font-mono text-gray-600 dark:text-gray-400"
                >
                  <span v-if="repo.quota_bytes">
                    {{ formatSize(repo.quota_bytes) }}
                  </span>
                  <span v-else class="text-gray-400">Inherit</span>
                </td>
                <td class="px-4 py-3 text-right">
                  <div
                    v-if="
                      repo.percentage_used !== null &&
                      repo.percentage_used !== undefined
                    "
                  >
                    <div class="flex items-center justify-end gap-2">
                      <div class="w-20">
                        <el-progress
                          :percentage="
                            Math.min(
                              100,
                              Math.round(repo.percentage_used * 100) / 100,
                            )
                          "
                          :color="getProgressColor(repo.percentage_used)"
                          :stroke-width="6"
                          :show-text="false"
                        />
                      </div>
                      <span class="font-mono text-xs w-14 text-right">
                        {{ repo.percentage_used.toFixed(2) }}%
                      </span>
                    </div>
                  </div>
                  <span v-else class="text-gray-400">-</span>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-400">
                  {{ formatDate(repo.created_at) }}
                </td>
              </tr>

              <tr v-if="filteredRepos.length === 0">
                <td
                  colspan="7"
                  class="px-4 py-12 text-center text-gray-500 dark:text-gray-400"
                >
                  <div
                    class="i-carbon-document-blank text-6xl mb-4 inline-block"
                  />
                  <p>No repositories found</p>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { quotaAPI } from "@/utils/api";
import { ElMessage } from "element-plus";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

const route = useRoute();
const router = useRouter();
const username = computed(() => route.params.username);

const loading = ref(true);
const error = ref(null);
const storageData = ref(null);
const searchQuery = ref("");

const filteredRepos = computed(() => {
  if (!storageData.value?.repositories) return [];
  if (!searchQuery.value) return storageData.value.repositories;

  const query = searchQuery.value.toLowerCase();
  return storageData.value.repositories.filter((repo) =>
    repo.name.toLowerCase().includes(query),
  );
});

const totalStorage = computed(() => {
  if (!storageData.value?.repositories) return "0 B";
  const total = storageData.value.repositories.reduce(
    (sum, repo) => sum + repo.used_bytes,
    0,
  );
  return formatSize(total);
});

const largestRepoSize = computed(() => {
  if (
    !storageData.value?.repositories ||
    storageData.value.repositories.length === 0
  ) {
    return "0 B";
  }
  const largest = storageData.value.repositories[0]; // Already sorted
  return formatSize(largest.used_bytes);
});

function formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1000 * 1000) return (bytes / 1000).toFixed(1) + " KB";
  if (bytes < 1000 * 1000 * 1000)
    return (bytes / (1000 * 1000)).toFixed(1) + " MB";
  return (bytes / (1000 * 1000 * 1000)).toFixed(2) + " GB";
}

function formatDate(dateStr) {
  if (!dateStr) return "Unknown";
  return dayjs(dateStr).fromNow();
}

function getRepoIcon(type) {
  const icons = {
    model: "i-carbon-model text-blue-500",
    dataset: "i-carbon-data-table text-green-500",
    space: "i-carbon-application text-purple-500",
  };
  return icons[type] || icons.model;
}

function getProgressColor(percentage) {
  if (percentage >= 90) return "#f56c6c"; // Red
  if (percentage >= 75) return "#e6a23c"; // Orange
  return "#67c23a"; // Green
}

function goToRepo(repo) {
  const [namespace, name] = repo.repo_id.split("/");
  router.push(`/${repo.repo_type}s/${namespace}/${name}`);
}

async function loadStorageData() {
  loading.value = true;
  error.value = null;

  try {
    const { data } = await quotaAPI.getNamespaceRepoStorage(username.value);
    storageData.value = data;
  } catch (err) {
    console.error("Failed to load storage data:", err);
    if (err.response?.status === 403) {
      error.value = "You don't have permission to view this storage breakdown";
    } else if (err.response?.status === 404) {
      error.value = "User not found";
    } else {
      error.value =
        err.response?.data?.detail?.error || "Failed to load storage data";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadStorageData();
});
</script>
