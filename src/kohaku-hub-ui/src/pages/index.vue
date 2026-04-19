<!-- src/pages/index.vue -->
<template>
  <div>
    <!-- Hero Section -->
    <div class="bg-gradient-to-r from-blue-500 to-purple-600 text-white">
      <div class="container-main py-8 md:py-16 text-center">
        <h1 class="text-3xl md:text-4xl lg:text-5xl font-bold mb-4">
          Welcome to KohakuHub
        </h1>
        <p class="text-base md:text-lg lg:text-xl mb-6 md:mb-8 px-4">
          Self-hosted HuggingFace Hub alternative for your AI models and
          datasets
        </p>
        <div class="flex flex-col sm:flex-row gap-2 justify-center px-4">
          <el-button
            size="large"
            type="default"
            class="!bg-white !text-gray-900 hover:!bg-gray-100 !font-semibold !shadow-lg"
            @click="$router.push('/get-started')"
          >
            Get Started
          </el-button>
          <div class="w-0 h-0 p-0 m-0"></div>
          <el-button
            size="large"
            class="!bg-transparent !text-white !border-white !border-2 hover:!bg-white/20 !font-semibold"
            @click="$router.push('/self-hosted')"
          >
            Host Your Own Hub
          </el-button>
        </div>
      </div>
    </div>

    <!-- Recent Repos - Three Columns -->
    <div class="container-main py-8">
      <h2 class="text-2xl md:text-3xl font-bold mb-6 md:mb-8">🔥 Trending</h2>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <!-- Models Column -->
        <div>
          <div
            class="flex items-center justify-between mb-4 pb-3 border-b-2 border-blue-500"
          >
            <div class="flex items-center gap-2">
              <div class="i-carbon-model text-blue-500 text-2xl" />
              <h3 class="text-xl font-bold">Models</h3>
            </div>
            <el-tag type="info" size="large">{{ stats.models }}</el-tag>
          </div>

          <div class="space-y-3">
            <div
              v-for="repo in recentModels"
              :key="repo.id"
              class="card hover:shadow-md transition-shadow cursor-pointer"
              @click="goToRepo('model', repo)"
            >
              <div class="flex items-start gap-2 mb-2">
                <div class="i-carbon-model text-blue-500 flex-shrink-0" />
                <div class="flex-1 min-w-0">
                  <h4 class="font-semibold text-sm">
                    <RouterLink
                      :to="getRepoPath('model', repo)"
                      class="block text-blue-600 hover:underline truncate"
                      @click.stop
                    >
                      {{ repo.id }}
                    </RouterLink>
                  </h4>
                  <div class="text-xs text-gray-600 dark:text-gray-400 mt-1">
                    {{ formatDate(repo.lastModified) }}
                  </div>
                </div>
              </div>

              <div
                class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 mt-2"
              >
                <div class="flex items-center gap-1">
                  <div class="i-carbon-download" />
                  {{ repo.downloads || 0 }}
                </div>
                <div class="flex items-center gap-1">
                  <div class="i-carbon-favorite" />
                  {{ repo.likes || 0 }}
                </div>
              </div>
            </div>

            <el-button class="w-full" @click="$router.push('/models')">
              View all models ->
            </el-button>
          </div>
        </div>

        <!-- Datasets Column -->
        <div>
          <div
            class="flex items-center justify-between mb-4 pb-3 border-b-2 border-green-500"
          >
            <div class="flex items-center gap-2">
              <div class="i-carbon-data-table text-green-500 text-2xl" />
              <h3 class="text-xl font-bold">Datasets</h3>
            </div>
            <el-tag type="success" size="large">{{ stats.datasets }}</el-tag>
          </div>

          <div class="space-y-3">
            <div
              v-for="repo in recentDatasets"
              :key="repo.id"
              class="card hover:shadow-md transition-shadow cursor-pointer"
              @click="goToRepo('dataset', repo)"
            >
              <div class="flex items-start gap-2 mb-2">
                <div class="i-carbon-data-table text-green-500 flex-shrink-0" />
                <div class="flex-1 min-w-0">
                  <h4 class="font-semibold text-sm">
                    <RouterLink
                      :to="getRepoPath('dataset', repo)"
                      class="block text-green-600 hover:underline truncate"
                      @click.stop
                    >
                      {{ repo.id }}
                    </RouterLink>
                  </h4>
                  <div class="text-xs text-gray-600 dark:text-gray-400 mt-1">
                    {{ formatDate(repo.lastModified) }}
                  </div>
                </div>
              </div>

              <div
                class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 mt-2"
              >
                <div class="flex items-center gap-1">
                  <div class="i-carbon-download" />
                  {{ repo.downloads || 0 }}
                </div>
                <div class="flex items-center gap-1">
                  <div class="i-carbon-favorite" />
                  {{ repo.likes || 0 }}
                </div>
              </div>
            </div>

            <el-button class="w-full" @click="$router.push('/datasets')">
              View all datasets ->
            </el-button>
          </div>
        </div>

        <!-- Spaces Column -->
        <div>
          <div
            class="flex items-center justify-between mb-4 pb-3 border-b-2 border-purple-500"
          >
            <div class="flex items-center gap-2">
              <div class="i-carbon-application text-purple-500 text-2xl" />
              <h3 class="text-xl font-bold">Spaces</h3>
            </div>
            <el-tag type="warning" size="large">{{ stats.spaces }}</el-tag>
          </div>

          <div class="space-y-3">
            <div
              v-for="repo in recentSpaces"
              :key="repo.id"
              class="card hover:shadow-md transition-shadow cursor-pointer"
              @click="goToRepo('space', repo)"
            >
              <div class="flex items-start gap-2 mb-2">
                <div
                  class="i-carbon-application text-purple-500 flex-shrink-0"
                />
                <div class="flex-1 min-w-0">
                  <h4 class="font-semibold text-sm">
                    <RouterLink
                      :to="getRepoPath('space', repo)"
                      class="block text-purple-600 hover:underline truncate"
                      @click.stop
                    >
                      {{ repo.id }}
                    </RouterLink>
                  </h4>
                  <div class="text-xs text-gray-600 dark:text-gray-400 mt-1">
                    {{ formatDate(repo.lastModified) }}
                  </div>
                </div>
              </div>

              <div
                class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 mt-2"
              >
                <div class="flex items-center gap-1">
                  <div class="i-carbon-download" />
                  {{ repo.downloads || 0 }}
                </div>
                <div class="flex items-center gap-1">
                  <div class="i-carbon-favorite" />
                  {{ repo.likes || 0 }}
                </div>
              </div>
            </div>

            <el-button class="w-full" @click="$router.push('/spaces')">
              View all spaces ->
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { repoAPI } from "@/utils/api";
import { useAuthStore } from "@/stores/auth";
import { ElMessage } from "element-plus";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();
const { isAuthenticated } = storeToRefs(authStore);

const stats = ref({ models: 0, datasets: 0, spaces: 0 });
const recentModels = ref([]);
const recentDatasets = ref([]);
const recentSpaces = ref([]);

function formatDate(date) {
  return dayjs(date).fromNow();
}

function getRepoPath(type, repo) {
  const [namespace, name] = repo.id.split("/");
  return `/${type}s/${namespace}/${name}`;
}

function goToRepo(type, repo) {
  router.push(getRepoPath(type, repo));
}

async function loadStats() {
  try {
    const [models, datasets, spaces] = await Promise.all([
      repoAPI.listRepos("model", {
        limit: 100,
        sort: "trending",
        fallback: false,
      }),
      repoAPI.listRepos("dataset", {
        limit: 100,
        sort: "trending",
        fallback: false,
      }),
      repoAPI.listRepos("space", {
        limit: 100,
        sort: "trending",
        fallback: false,
      }),
    ]);

    stats.value = {
      models: models.data.length,
      datasets: datasets.data.length,
      spaces: spaces.data.length,
    };

    // Get top 3 trending repos for each type (already sorted by backend)
    recentModels.value = models.data.slice(0, 3);
    recentDatasets.value = datasets.data.slice(0, 3);
    recentSpaces.value = spaces.data.slice(0, 3);
  } catch (err) {
    console.error("Failed to load stats:", err);
  }
}

onMounted(() => {
  // Check for verification error messages in query params
  if (route.query.error) {
    const errorType = route.query.error;
    const message = route.query.message || "An error occurred";

    if (errorType === "invalid_token") {
      ElMessage.error(decodeURIComponent(message));
      // Clean up URL
      router.replace("/");
    } else if (errorType === "user_not_found") {
      ElMessage.error("User account not found");
      router.replace("/");
    }
  }

  loadStats();
});
</script>
