<!-- src/kohaku-hub-ui/src/pages/[type]s/[namespace]/[name]/upload/[branch].vue -->
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
      <el-breadcrumb-item>Upload Files</el-breadcrumb-item>
    </el-breadcrumb>

    <!-- Page Header -->
    <div class="card mb-6">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="i-carbon-cloud-upload text-3xl text-blue-500" />
          <div>
            <h1 class="text-2xl font-bold">Upload Files</h1>
            <div class="text-sm text-gray-600 dark:text-gray-400 mt-1">
              Upload files to <strong>{{ namespace }}/{{ name }}</strong> on
              branch <el-tag size="small">{{ branch }}</el-tag>
            </div>
          </div>
        </div>
        <el-button @click="goBack">
          <div class="i-carbon-arrow-left inline-block mr-1" />
          Back to Repository
        </el-button>
      </div>
    </div>

    <!-- Upload Form -->
    <div class="card">
      <!-- LFS Info -->
      <el-alert type="info" :closable="false" class="mb-4">
        <template #title>
          <div class="flex items-center gap-2">
            <div class="i-carbon-information" />
            Automatic LFS Detection
          </div>
        </template>
        <div class="text-sm">
          Large files will be automatically uploaded using Git LFS for efficient
          storage. LFS files are deduplicated and shared across repositories.
        </div>
      </el-alert>

      <!-- Use FileUploader Component -->
      <FileUploader
        :repo-type="repoType"
        :namespace="namespace"
        :name="name"
        :branch="branch"
        @upload-success="handleUploadSuccess"
        @upload-error="handleUploadError"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuthStore } from "@/stores/auth";
import FileUploader from "@/components/repo/FileUploader.vue";

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

// Computed
const repoTypeLabel = computed(() => {
  const labels = { model: "Models", dataset: "Datasets", space: "Spaces" };
  return labels[repoType.value] || "Models";
});

// Methods
function handleUploadSuccess() {
  ElMessage.success("Files uploaded successfully");
  // Wait a bit to show success message
  setTimeout(() => {
    router.push(`/${repoType.value}s/${namespace.value}/${name.value}`);
  }, 1000);
}

function handleUploadError(error) {
  const errorMsg = error.response?.data?.detail || "Failed to upload files";
  ElMessage.error(errorMsg);
  console.error("Upload error:", error);
}

function goBack() {
  router.push(`/${repoType.value}s/${namespace.value}/${name.value}`);
}

// Lifecycle
onMounted(() => {
  // Check if user has permission to upload
  if (!authStore.isAuthenticated) {
    ElMessage.error("You must be logged in to upload files");
    router.push(`/${repoType.value}s/${namespace.value}/${name.value}`);
    return;
  }

  if (!authStore.canWriteToNamespace(namespace.value)) {
    ElMessage.error("You don't have permission to upload to this repository");
    router.push(`/${repoType.value}s/${namespace.value}/${name.value}`);
    return;
  }
});
</script>
