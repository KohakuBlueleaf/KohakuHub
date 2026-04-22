<!-- src/kohaku-hub-ui/src/components/pages/RepoListPage.vue -->
<template>
  <div class="container-main">
    <div
      class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6"
    >
      <div>
        <h1 class="text-2xl md:text-3xl font-bold mb-2">{{ pageTitle }}</h1>
        <p class="text-sm md:text-base text-gray-600 dark:text-gray-400">
          {{ pageDescription }}
        </p>
      </div>

      <el-button
        v-if="isAuthenticated"
        type="primary"
        size="large"
        @click="showCreateDialog = true"
        class="w-full md:w-auto"
      >
        <div class="i-carbon-add inline-block mr-1" />
        New {{ repoTypeLabel }}
      </el-button>
    </div>

    <!-- Filters -->
    <div class="card mb-6">
      <div
        class="flex flex-col gap-4 md:flex-row md:items-center"
      >
        <div class="w-full md:flex-1 md:min-w-0">
          <el-input
            v-model="searchQuery"
            :placeholder="`Search ${repoType}s...`"
            clearable
            class="w-full"
          >
            <template #prefix>
              <div class="i-carbon-search" />
            </template>
          </el-input>
        </div>

        <div class="w-full md:w-72 md:flex-none md:shrink-0">
          <el-select
            v-model="sortBy"
            placeholder="Sort by"
            class="w-full"
          >
            <el-option label="Recently Created" value="recent" />
            <el-option label="Recently Updated" value="updated" />
            <el-option label="Most Downloads" value="downloads" />
            <el-option label="Most Likes" value="likes" />
          </el-select>
        </div>
      </div>
    </div>

    <!-- Repository List -->
    <el-skeleton :loading="loading" :rows="5" animated>
      <RepoList :repos="filteredRepos" :type="repoType" />
    </el-skeleton>

    <!-- Create Repository Dialog -->
    <el-dialog
      v-model="showCreateDialog"
      :title="`Create New ${repoTypeLabel}`"
      width="500px"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item :label="`${repoTypeLabel} Name`" prop="name">
          <el-input v-model="form.name" :placeholder="`my-${repoType}`" />
          <div class="text-xs text-gray-500 mt-1">
            Full name: {{ currentUser }}/{{ form.name || `${repoType}-name` }}
          </div>
        </el-form-item>

        <el-form-item label="Organization (Optional)" prop="organization">
          <el-select
            v-model="form.organization"
            placeholder="Select organization or leave empty"
            clearable
            class="w-full"
          >
            <el-option
              v-for="org in userOrgs"
              :key="org.name"
              :label="org.name"
              :value="org.name"
            />
          </el-select>
        </el-form-item>

        <el-form-item>
          <el-checkbox v-model="form.private">
            Make this {{ repoType }} private
          </el-checkbox>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateDialog = false">Cancel</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">
          Create {{ repoTypeLabel }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { repoAPI, orgAPI } from "@/utils/api";
import { useAuthStore } from "@/stores/auth";
import RepoList from "@/components/repo/RepoList.vue";
import {
  getRepoSortPreference,
  setRepoSortPreference,
} from "@/utils/repoSortPreference";
import { ElMessage } from "element-plus";

const props = defineProps({
  repoType: {
    type: String,
    required: true,
    validator: (value) => ["model", "dataset", "space"].includes(value),
  },
});

const router = useRouter();
const authStore = useAuthStore();
const { isAuthenticated, username: currentUser } = storeToRefs(authStore);

const repoTypeLabel = computed(() => {
  const labels = { model: "Model", dataset: "Dataset", space: "Space" };
  return labels[props.repoType] || "Model";
});

const pageTitle = computed(() => {
  const titles = { model: "Models", dataset: "Datasets", space: "Spaces" };
  return titles[props.repoType] || "Models";
});

const pageDescription = computed(() => {
  const descriptions = {
    model: "Discover and share machine learning models",
    dataset: "Discover and share datasets for machine learning",
    space: "Discover ML demos and applications",
  };
  return descriptions[props.repoType] || "";
});

const loading = ref(true);
const repos = ref([]);
const searchQuery = ref("");
const sortBy = ref(
  getRepoSortPreference({
    scope: "repo",
    repoType: props.repoType,
    allowedValues: ["recent", "updated", "downloads", "likes"],
    fallback: "recent",
  }),
);
const showCreateDialog = ref(false);
const creating = ref(false);
const userOrgs = ref([]);
const formRef = ref(null);

const form = reactive({
  name: "",
  organization: "",
  private: false,
});

const rules = {
  name: [
    {
      required: true,
      message: `Please enter ${props.repoType} name`,
      trigger: "blur",
    },
    {
      pattern: /^[a-zA-Z0-9_-]+$/,
      message: "Only letters, numbers, hyphens and underscores allowed",
      trigger: "blur",
    },
  ],
};

const filteredRepos = computed(() => {
  let result = [...repos.value];

  // Only filter by search query (sorting is done by backend)
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase();
    result = result.filter(
      (repo) =>
        repo.id.toLowerCase().includes(query) ||
        repo.author.toLowerCase().includes(query),
    );
  }

  return result;
});

async function loadRepos() {
  loading.value = true;
  try {
    const { data } = await repoAPI.listRepos(props.repoType, {
      limit: 100,
      sort: sortBy.value,
      fallback: false, // Don't aggregate external repos on main list pages
    });
    repos.value = data;
  } catch (err) {
    console.error(`Failed to load ${props.repoType}s:`, err);
    ElMessage.error(`Failed to load ${props.repoType}s`);
  } finally {
    loading.value = false;
  }
}

async function loadUserOrgs() {
  if (!currentUser.value) return;

  try {
    const { data } = await orgAPI.getUserOrgs(currentUser.value);
    userOrgs.value = data.organizations || [];
  } catch (err) {
    console.error("Failed to load organizations:", err);
  }
}

async function handleCreate() {
  if (!formRef.value) return;

  await formRef.value.validate(async (valid) => {
    if (!valid) return;

    creating.value = true;
    try {
      const { data } = await repoAPI.create({
        type: props.repoType,
        name: form.name,
        organization: form.organization || null,
        private: form.private,
      });

      ElMessage.success(`${repoTypeLabel.value} created successfully`);
      showCreateDialog.value = false;

      const repoId =
        data.repo_id ||
        `${form.organization || currentUser.value}/${form.name}`;
      router.push(`/${props.repoType}s/${repoId}`);
    } catch (err) {
      // `POST /api/repos/create` returns a 409 with a top-level `{url,
      // repo_id, error}` body when the repo already exists (HF-compatible
      // exist-ok contract). Read `.error` before falling back to the
      // legacy `.detail` shape so the user sees the actual conflict
      // message instead of a generic "Failed to create ..." toast.
      ElMessage.error(
        err.response?.data?.error ||
          err.response?.data?.detail ||
          `Failed to create ${props.repoType}`,
      );
    } finally {
      creating.value = false;
    }
  });
}

watch(showCreateDialog, (val) => {
  if (val) {
    loadUserOrgs();
  } else {
    form.name = "";
    form.organization = "";
    form.private = false;
  }
});

// Reload repos when sort changes
watch(sortBy, () => {
  setRepoSortPreference({
    scope: "repo",
    repoType: props.repoType,
    value: sortBy.value,
  });
  loadRepos();
});

onMounted(() => {
  loadRepos();
});
</script>
