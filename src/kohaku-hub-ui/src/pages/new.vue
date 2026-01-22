<!-- src/pages/new.vue -->
<template>
  <div class="container-main">
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl md:text-3xl font-bold mb-2">Create New Repository</h1>
      <p
        class="text-sm md:text-base text-gray-600 dark:text-gray-400 mb-6 md:mb-8"
      >
        A repository contains all project files, including revision history.
      </p>

      <div class="card">
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent="handleSubmit"
        >
          <!-- Repository Type -->
          <el-form-item label="Repository Type" prop="type">
            <div class="w-full">
              <el-radio-group
                v-model="form.type"
                size="large"
                class="w-full grid grid-cols-1 sm:grid-cols-3 gap-2"
              >
                <el-radio-button value="model">
                  <div class="flex items-center justify-center gap-2 py-2">
                    <div class="i-carbon-model text-xl" />
                    <span>Model</span>
                  </div>
                </el-radio-button>
                <el-radio-button value="dataset">
                  <div class="flex items-center justify-center gap-2 py-2">
                    <div class="i-carbon-data-table text-xl" />
                    <span>Dataset</span>
                  </div>
                </el-radio-button>
                <el-radio-button value="space">
                  <div class="flex items-center justify-center gap-2 py-2">
                    <div class="i-carbon-application text-xl" />
                    <span>Space</span>
                  </div>
                </el-radio-button>
              </el-radio-group>
            </div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mt-2">
              {{ getTypeDescription(form.type) }}
            </div>
          </el-form-item>

          <!-- Owner -->
          <el-form-item label="Owner" prop="owner">
            <el-select
              v-model="form.owner"
              placeholder="Select owner"
              size="large"
              class="w-full"
            >
              <el-option :label="currentUser" :value="currentUser">
                <div class="flex items-center gap-2">
                  <div class="i-carbon-user-avatar" />
                  <span>{{ currentUser }}</span>
                  <span class="text-xs text-gray-500">(Personal)</span>
                </div>
              </el-option>
              <el-option
                v-for="org in userOrgs"
                :key="org.name"
                :label="org.name"
                :value="org.name"
              >
                <div class="flex items-center gap-2">
                  <div class="i-carbon-enterprise" />
                  <span>{{ org.name }}</span>
                  <span class="text-xs text-gray-500">(Organization)</span>
                </div>
              </el-option>
            </el-select>
          </el-form-item>

          <!-- Repository Name -->
          <el-form-item :label="`${typeLabel} Name`" prop="name">
            <el-input
              v-model="form.name"
              :placeholder="`my-awesome-${form.type}`"
              size="large"
            >
              <template #prepend>
                <span class="text-gray-600">{{ form.owner }}/</span>
              </template>
            </el-input>
            <div class="text-xs text-gray-500 mt-1">
              <div class="i-carbon-information inline-block mr-1" />
              Use letters, numbers, hyphens, underscores, and dots only
            </div>
          </el-form-item>

          <!-- Visibility -->
          <el-form-item label="Visibility">
            <el-radio-group v-model="form.private" size="large">
              <el-radio :value="false" class="mb-3">
                <div class="flex items-start gap-2">
                  <div class="i-carbon-unlocked text-xl text-green-500" />
                  <div>
                    <div class="font-semibold">Public</div>
                    <div class="text-xs text-gray-600 dark:text-gray-400">
                      Anyone on the internet can see this repository
                    </div>
                  </div>
                </div>
              </el-radio>
              <el-radio :value="true">
                <div class="flex items-start gap-2">
                  <div class="i-carbon-locked text-xl text-orange-500" />
                  <div>
                    <div class="font-semibold">Private</div>
                    <div class="text-xs text-gray-600 dark:text-gray-400">
                      You choose who can see and commit to this repository
                    </div>
                  </div>
                </div>
              </el-radio>
            </el-radio-group>
          </el-form-item>

          <!-- Actions -->
          <div
            class="flex flex-col-reverse sm:flex-row gap-3 mt-8 pt-6 border-t border-gray-200 dark:border-gray-700"
          >
            <el-button
              size="large"
              @click="$router.back()"
              class="w-full sm:w-auto"
            >
              Cancel
            </el-button>
            <el-button
              type="primary"
              size="large"
              :loading="creating"
              @click="handleSubmit"
              class="w-full sm:w-auto"
            >
              <div class="i-carbon-add inline-block mr-1" />
              Create {{ typeLabel }}
            </el-button>
          </div>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { repoAPI } from "@/utils/api";
import { useAuthStore } from "@/stores/auth";
import { ElMessage } from "element-plus";

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();
const { username: currentUser, organizations: userOrgs } =
  storeToRefs(authStore);

const formRef = ref(null);
const creating = ref(false);

const form = reactive({
  type: route.query.type || "model",
  owner: currentUser.value,
  name: "",
  private: false,
});

const rules = {
  type: [
    {
      required: true,
      message: "Please select repository type",
      trigger: "change",
    },
  ],
  owner: [
    { required: true, message: "Please select owner", trigger: "change" },
  ],
  name: [
    {
      required: true,
      message: "Please enter repository name",
      trigger: "blur",
    },
    {
      pattern: /^[a-zA-Z0-9_.-]+$/,
      message: "Only letters, numbers, hyphens, underscores, and dots allowed",
      trigger: "blur",
    },
    {
      min: 2,
      message: "Name must be at least 2 characters",
      trigger: "blur",
    },
  ],
};

const typeLabel = computed(() => {
  const labels = { model: "Model", dataset: "Dataset", space: "Space" };
  return labels[form.type] || "Repository";
});

function getTypeDescription(type) {
  const descriptions = {
    model: "Store and share machine learning models with the community",
    dataset: "Store and share datasets for training and evaluation",
    space: "Create interactive ML demos and applications",
  };
  return descriptions[type] || "";
}

async function handleSubmit() {
  if (!formRef.value) return;

  await formRef.value.validate(async (valid) => {
    if (!valid) return;

    creating.value = true;
    try {
      // Backend expects:
      // {
      //   type: "model" | "dataset" | "space",
      //   name: string,
      //   organization: string | null,
      //   private: boolean,
      //   sdk: string | null (optional)
      // }
      const payload = {
        type: form.type,
        name: form.name,
        organization: form.owner !== currentUser.value ? form.owner : null,
        private: form.private,
      };

      const { data } = await repoAPI.create(payload);

      ElMessage.success(`${typeLabel.value} created successfully`);

      // Navigate to the new repository
      // Backend returns: { url: string, repo_id: string }
      const repoId = data.repo_id || `${form.owner}/${form.name}`;
      router.push(`/${form.type}s/${repoId}`);
    } catch (err) {
      ElMessage.error(
        err.response?.data?.detail || `Failed to create ${form.type}`,
      );
      console.error("Create repository error:", err);
    } finally {
      creating.value = false;
    }
  });
}

// Watch for type changes from query params
watch(
  () => route.query.type,
  (newType) => {
    if (newType && ["model", "dataset", "space"].includes(newType)) {
      form.type = newType;
    }
  },
);
</script>
