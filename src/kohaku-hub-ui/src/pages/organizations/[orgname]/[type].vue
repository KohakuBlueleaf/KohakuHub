<!-- src/pages/organizations/[orgname]/[type].vue -->
<template>
  <div class="container-main">
    <!-- Loading State -->
    <div v-if="loading" class="text-center py-20">
      <div
        class="inline-block animate-spin rounded-full h-16 w-16 border-4 border-gray-300 border-t-blue-600 mb-4"
      ></div>
      <p class="text-xl text-gray-600 dark:text-gray-400">
        Loading repositories...
      </p>
    </div>

    <!-- Organization Not Found -->
    <div v-else-if="orgNotFound" class="text-center py-20">
      <div
        class="i-carbon-group text-8xl text-gray-300 dark:text-gray-600 mb-6 inline-block"
      />
      <h1 class="text-4xl font-bold mb-4">Organization Not Found</h1>
      <p class="text-xl text-gray-600 dark:text-gray-400 mb-8">
        The organization "<span
          class="font-mono text-blue-600 dark:text-blue-400"
          >{{ orgname }}</span
        >" does not exist.
      </p>
      <el-button type="primary" @click="$router.push('/')">
        <div class="i-carbon-home mr-2" />
        Go to Homepage
      </el-button>
    </div>

    <div v-else class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
      <!-- Sidebar with members -->
      <aside class="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <div class="card">
          <div class="flex items-center gap-3 mb-4">
            <div class="i-carbon-group text-5xl text-gray-400" />
            <div>
              <h2 class="text-xl font-bold">{{ orgname }}</h2>
              <p class="text-sm text-gray-600 dark:text-gray-400">
                Organization
              </p>
            </div>
          </div>

          <div v-if="orgInfo" class="space-y-2 text-sm">
            <div class="text-gray-600 dark:text-gray-400">
              {{ orgInfo.description || "No description" }}
            </div>
            <div
              class="flex items-center gap-2 text-gray-600 dark:text-gray-400"
            >
              <div class="i-carbon-calendar" />
              Created {{ formatDate(orgInfo.created_at) }}
            </div>
          </div>
        </div>

        <!-- Members List -->
        <div class="card">
          <h3 class="font-semibold mb-3 flex items-center gap-2">
            <div class="i-carbon-user-multiple" />
            Members
            <span class="text-sm text-gray-500"
              >({{ members.length || 0 }})</span
            >
          </h3>
          <div v-if="members.length > 0" class="space-y-2">
            <div
              v-for="member in members"
              :key="member.user"
              class="flex items-center gap-2 p-2 rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer"
              @click="goToUser(member.user)"
            >
              <div class="i-carbon-user-avatar text-2xl text-gray-400" />
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium truncate">
                  {{ member.user }}
                </div>
                <div class="text-xs text-gray-500">
                  {{ member.role }}
                </div>
              </div>
            </div>
          </div>
          <div
            v-else
            class="text-center py-4 text-sm text-gray-500 dark:text-gray-400"
          >
            No members yet
          </div>
        </div>

        <!-- Stats Summary / Tab Navigation -->
        <div class="card">
          <h3 class="font-semibold mb-3">Repositories</h3>
          <div class="space-y-1">
            <RouterLink
              :to="`/organizations/${orgname}`"
              :class="[
                'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                'hover:bg-gray-100 dark:hover:bg-gray-700',
              ]"
            >
              <div class="flex items-center gap-2 text-sm">
                <div class="i-carbon-grid text-gray-500 dark:text-gray-400" />
                <span>Overview</span>
              </div>
            </RouterLink>

            <RouterLink
              :to="`/organizations/${orgname}/models`"
              :class="[
                'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                currentType === 'models'
                  ? 'bg-gray-100 dark:bg-gray-700'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-700',
              ]"
            >
              <div class="flex items-center gap-2 text-sm">
                <div class="i-carbon-model text-blue-500" />
                <span :class="currentType === 'models' ? 'font-semibold' : ''"
                  >Models</span
                >
              </div>
              <span
                class="text-sm font-semibold text-gray-600 dark:text-gray-400"
              >
                {{ repos.model?.length || 0 }}
              </span>
            </RouterLink>

            <RouterLink
              :to="`/organizations/${orgname}/datasets`"
              :class="[
                'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                currentType === 'datasets'
                  ? 'bg-gray-100 dark:bg-gray-700'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-700',
              ]"
            >
              <div class="flex items-center gap-2 text-sm">
                <div class="i-carbon-data-table text-green-500" />
                <span :class="currentType === 'datasets' ? 'font-semibold' : ''"
                  >Datasets</span
                >
              </div>
              <span
                class="text-sm font-semibold text-gray-600 dark:text-gray-400"
              >
                {{ repos.dataset?.length || 0 }}
              </span>
            </RouterLink>

            <RouterLink
              :to="`/organizations/${orgname}/spaces`"
              :class="[
                'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                currentType === 'spaces'
                  ? 'bg-gray-100 dark:bg-gray-700'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-700',
              ]"
            >
              <div class="flex items-center gap-2 text-sm">
                <div class="i-carbon-application text-purple-500" />
                <span :class="currentType === 'spaces' ? 'font-semibold' : ''"
                  >Spaces</span
                >
              </div>
              <span
                class="text-sm font-semibold text-gray-600 dark:text-gray-400"
              >
                {{ repos.space?.length || 0 }}
              </span>
            </RouterLink>
          </div>
        </div>
      </aside>

      <!-- Main Content -->
      <main class="space-y-8">
        <section>
          <div
            class="flex items-center justify-between mb-4 pb-3 border-b-2"
            :class="borderColor"
          >
            <div class="flex items-center gap-2">
              <div :class="iconClass" class="text-xl md:text-2xl" />
              <h2 class="text-xl md:text-2xl font-bold">{{ typeTitle }}</h2>
            </div>
            <el-tag :type="tagType" size="large">{{ repoCount }}</el-tag>
          </div>

          <div v-if="repoCount > 0" class="space-y-4">
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div
                v-for="repo in currentRepos"
                :key="repo.id"
                class="card hover:shadow-md transition-shadow cursor-pointer"
                @click="goToRepo(repo)"
              >
                <div class="flex items-start gap-2 mb-2">
                  <div :class="iconClass" class="text-xl flex-shrink-0" />
                  <div class="flex-1 min-w-0">
                    <h3 class="font-semibold">
                      <RouterLink
                        :to="getRepoPath(repo)"
                        class="block hover:underline truncate"
                        :class="titleColor"
                        @click.stop
                      >
                        {{ repo.id }}
                      </RouterLink>
                    </h3>
                    <div class="text-xs text-gray-600 dark:text-gray-400 mt-1">
                      Updated {{ formatDate(repo.lastModified) }}
                    </div>
                  </div>
                </div>

                <div
                  v-if="repo.tags && repo.tags.length"
                  class="flex gap-1 mb-2 flex-wrap"
                >
                  <el-tag
                    v-for="tag in repo.tags.slice(0, 2)"
                    :key="tag"
                    size="small"
                    effect="plain"
                  >
                    {{ tag }}
                  </el-tag>
                </div>

                <!-- External Source Badge + Link -->
                <div
                  v-if="repo._source && repo._source !== 'local'"
                  class="mb-2"
                >
                  <el-button
                    size="small"
                    type="primary"
                    plain
                    @click.stop="openExternalRepo(repo)"
                  >
                    <div class="i-carbon-launch inline-block mr-1" />
                    View on {{ repo._source }}
                  </el-button>
                </div>

                <div
                  class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400"
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
            </div>
          </div>

          <div
            v-else
            class="text-center py-12 text-gray-500 dark:text-gray-400"
          >
            <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
            <p>No {{ currentType }} yet</p>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup>
import { repoAPI, orgAPI } from "@/utils/api";
import axios from "axios";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

const route = useRoute();
const router = useRouter();
const orgname = computed(() => route.params.orgname);
const currentType = computed(() => route.params.type);

const loading = ref(true);
const orgNotFound = ref(false);
const orgInfo = ref(null);
const members = ref([]);
const repos = ref({ model: [], dataset: [], space: [] });

const typeMapping = {
  models: "model",
  datasets: "dataset",
  spaces: "space",
};

const currentRepos = computed(() => {
  const type = typeMapping[currentType.value] || "model";
  return repos.value[type] || [];
});

const repoCount = computed(() => currentRepos.value.length);

const typeTitle = computed(() => {
  return currentType.value.charAt(0).toUpperCase() + currentType.value.slice(1);
});

const iconClass = computed(() => {
  switch (currentType.value) {
    case "models":
      return "i-carbon-model text-blue-500";
    case "datasets":
      return "i-carbon-data-table text-green-500";
    case "spaces":
      return "i-carbon-application text-purple-500";
    default:
      return "i-carbon-model text-blue-500";
  }
});

const titleColor = computed(() => {
  switch (currentType.value) {
    case "models":
      return "text-blue-600 dark:text-blue-400";
    case "datasets":
      return "text-green-600 dark:text-green-400";
    case "spaces":
      return "text-purple-600 dark:text-purple-400";
    default:
      return "text-blue-600 dark:text-blue-400";
  }
});

const borderColor = computed(() => {
  switch (currentType.value) {
    case "models":
      return "border-blue-500";
    case "datasets":
      return "border-green-500";
    case "spaces":
      return "border-purple-500";
    default:
      return "border-blue-500";
  }
});

const tagType = computed(() => {
  switch (currentType.value) {
    case "models":
      return "info";
    case "datasets":
      return "success";
    case "spaces":
      return "warning";
    default:
      return "info";
  }
});

function formatDate(date) {
  return date ? dayjs(date).fromNow() : "never";
}

function getRepoPath(repo) {
  const type = typeMapping[currentType.value] || "model";
  const [namespace, name] = repo.id.split("/");
  return `/${type}s/${namespace}/${name}`;
}

function goToRepo(repo) {
  router.push(getRepoPath(repo));
}

function goToUser(username) {
  router.push(`/${username}`);
}

function openExternalRepo(repo) {
  if (!repo._source_url) return;

  // Check if source is HuggingFace
  const isHF =
    repo._source &&
    (repo._source.toLowerCase().includes("huggingface") ||
      repo._source_url.includes("huggingface.co"));

  const typeMapping = {
    models: "model",
    datasets: "dataset",
    spaces: "space",
  };
  const repoType = typeMapping[currentType.value] || "model";

  let url;
  if (isHF) {
    // HuggingFace URLs: models have no prefix, datasets and spaces have prefix
    if (repoType === "model") {
      url = `${repo._source_url}/${repo.id}`;
    } else {
      url = `${repo._source_url}/${currentType.value}/${repo.id}`;
    }
  } else {
    // KohakuHub and other sources: always use type prefix
    url = `${repo._source_url}/${currentType.value}/${repo.id}`;
  }

  window.open(url, "_blank");
}

async function checkOrgExists() {
  try {
    // Check org exists (WITH fallback to check all sources)
    const { data } = await axios.get(`/org/${orgname.value}`, {
      params: { fallback: true },
    });
    orgInfo.value = data;
    return true;
  } catch (err) {
    if (err.response?.status === 404) {
      // Not found in local or any fallback source
      orgNotFound.value = true;
      return false;
    }
    console.error("Failed to load org info:", err);
    return true; // Continue on non-404 errors
  }
}

async function loadMembers() {
  try {
    const { data } = await orgAPI.listMembers(orgname.value);
    members.value = data.members || [];
  } catch (err) {
    console.error("Failed to load members:", err);
  }
}

async function loadRepos() {
  try {
    const [models, datasets, spaces] = await Promise.all([
      repoAPI.listRepos("model", { author: orgname.value, limit: 100000 }),
      repoAPI.listRepos("dataset", { author: orgname.value, limit: 100000 }),
      repoAPI.listRepos("space", { author: orgname.value, limit: 100000 }),
    ]);

    repos.value = {
      model: models.data,
      dataset: datasets.data,
      space: spaces.data,
    };
  } catch (err) {
    console.error("Failed to load repos:", err);
  }
}

onMounted(async () => {
  try {
    loading.value = true;

    // Validate type parameter (must be models, datasets, or spaces)
    if (!["models", "datasets", "spaces"].includes(currentType.value)) {
      // Invalid type - redirect to org index page
      router.replace(`/organizations/${orgname.value}`);
      return;
    }

    // Check if org exists (with fallback)
    const orgExists = await checkOrgExists();
    if (!orgExists) {
      // Organization not found - stop loading other data
      return;
    }

    // Organization exists - load members and repos
    loadMembers();
    await loadRepos();
  } finally {
    loading.value = false;
  }
});
</script>
