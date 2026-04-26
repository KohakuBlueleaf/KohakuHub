<!-- src/kohaku-hub-ui/src/pages/[type]s/[namespace]/[name]/edit/[branch]/[...file].vue -->
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
      <el-breadcrumb-item>
        <span class="text-gray-500">Edit:</span> {{ fileName }}
      </el-breadcrumb-item>
    </el-breadcrumb>

    <div v-if="loading" class="text-center py-20">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
    </div>

    <ErrorState
      v-else-if="errorClassification"
      :classification="errorClassification"
      mode="full-page"
      :retry="loadFileContent"
    >
      <template #actions>
        <div class="flex items-center gap-2 mt-4">
          <el-button type="primary" plain @click="loadFileContent">
            Retry
          </el-button>
          <el-button @click="$router.back()">Go Back</el-button>
        </div>
      </template>
    </ErrorState>

    <div v-else>
      <!-- File Header -->
      <div class="card mb-6">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="i-carbon-edit text-3xl text-blue-500" />
            <div>
              <h1 class="text-2xl font-bold">Editing: {{ fileName }}</h1>
              <div class="text-sm text-gray-600 dark:text-gray-400 mt-1">
                <el-breadcrumb separator="/" class="inline">
                  <el-breadcrumb-item
                    v-for="(segment, idx) in pathSegments"
                    :key="idx"
                  >
                    {{ segment }}
                  </el-breadcrumb-item>
                </el-breadcrumb>
              </div>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <el-button @click="cancelEdit">
              <div class="i-carbon-close inline-block mr-1" />
              Cancel
            </el-button>
          </div>
        </div>
      </div>

      <!-- Editor Card -->
      <div class="card">
        <CodeEditor
          ref="editorRef"
          v-model="fileContent"
          :language="fileExtension"
          height="calc(100vh - 350px)"
          @save="handleSave"
        />
      </div>

      <!-- Commit Dialog -->
      <el-dialog
        v-model="showCommitDialog"
        title="Commit Changes"
        width="500px"
      >
        <el-form :model="commitForm" label-position="top">
          <el-form-item label="Commit message">
            <el-input
              v-model="commitForm.message"
              type="textarea"
              :rows="3"
              placeholder="Update file via web editor"
            />
          </el-form-item>
          <el-form-item label="Commit description (optional)">
            <el-input
              v-model="commitForm.description"
              type="textarea"
              :rows="2"
              placeholder="Additional details about this change..."
            />
          </el-form-item>
        </el-form>

        <template #footer>
          <el-button @click="showCommitDialog = false">Cancel</el-button>
          <el-button type="primary" :loading="committing" @click="submitCommit">
            Commit Changes
          </el-button>
        </template>
      </el-dialog>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { useRouter, useRoute, onBeforeRouteLeave } from "vue-router";
import { ElMessage } from "element-plus";
import CodeEditor from "@/components/common/CodeEditor.vue";
import ErrorState from "@/components/common/ErrorState.vue";
import { repoAPI } from "@/utils/api";
import { normalizeCatchAllParam } from "@/utils/repo-paths";
import { classifyError, classifyResponse } from "@/utils/http-errors";
import { useAuthStore } from "@/stores/auth";

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
const filePath = computed(() => normalizeCatchAllParam(route.params.file));

// State
const loading = ref(true);
// HF-aligned error classification (see utils/http-errors.js). Replaces
// the old "error string → generic File Not Found" state so a gated
// upstream surfaces the real remediation instead of a misleading 404.
const errorClassification = ref(null);
const fileContent = ref("");
const originalContent = ref("");
const editorRef = ref(null);
const showCommitDialog = ref(false);
const committing = ref(false);

const commitForm = ref({
  message: `Update ${filePath.value}`,
  description: "",
});

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
  return match ? match[1].toLowerCase() : "txt";
});

const pathSegments = computed(() => {
  return filePath.value ? filePath.value.split("/").filter(Boolean) : [];
});

const fileUrl = computed(() => {
  return `/${repoType.value}s/${namespace.value}/${name.value}/resolve/${branch.value}/${filePath.value}`;
});

const blobUrl = computed(() => {
  return `/${repoType.value}s/${namespace.value}/${name.value}/blob/${branch.value}/${filePath.value}`;
});

// Methods
async function loadFileContent() {
  loading.value = true;
  errorClassification.value = null;

  try {
    const response = await fetch(fileUrl.value);

    if (!response.ok) {
      // Surface the classified response (gated / not-found / upstream
      // unavailable) so ErrorState renders the right copy instead of
      // a bare "File not found" string.
      errorClassification.value = await classifyResponse(response);
      return;
    }

    const content = await response.text();
    fileContent.value = content;
    originalContent.value = content;
  } catch (err) {
    errorClassification.value = classifyError(err);
    console.error("Failed to load file:", err);
  } finally {
    loading.value = false;
  }
}

function handleSave(content, onSuccess, onError) {
  // Refuse to open the commit dialog if the file never loaded
  // successfully — otherwise the user could overwrite a gated / not-
  // found file with empty content and silently nuke whatever the
  // upstream actually has.
  if (errorClassification.value) {
    ElMessage.error(
      "This file did not load successfully; fix the error above before committing changes.",
    );
    if (typeof onError === "function") onError();
    return;
  }
  showCommitDialog.value = true;
}

async function submitCommit() {
  if (!commitForm.value.message.trim()) {
    ElMessage.warning("Please enter a commit message");
    return;
  }

  committing.value = true;

  try {
    // Use HuggingFace Hub API to commit the file
    await repoAPI.commitFiles(
      repoType.value,
      namespace.value,
      name.value,
      branch.value,
      {
        files: [
          {
            path: filePath.value,
            content: fileContent.value,
          },
        ],
        message: commitForm.value.message,
        description: commitForm.value.description,
      },
    );

    ElMessage.success("Changes committed successfully");
    showCommitDialog.value = false;

    // Update original content to prevent "unsaved changes" warning
    originalContent.value = fileContent.value;

    // Navigate back to blob view
    setTimeout(() => {
      router.push(blobUrl.value);
    }, 500);
  } catch (err) {
    const errorMsg = err.response?.data?.detail || "Failed to commit changes";
    ElMessage.error(errorMsg);
    console.error("Commit error:", err);
  } finally {
    committing.value = false;
  }
}

function cancelEdit() {
  if (fileContent.value !== originalContent.value) {
    if (
      confirm(
        "You have unsaved changes. Are you sure you want to leave this page?",
      )
    ) {
      router.push(blobUrl.value);
    }
  } else {
    router.push(blobUrl.value);
  }
}

// Lifecycle
onMounted(() => {
  // Check if user has permission to edit
  if (!authStore.isAuthenticated) {
    ElMessage.error("You must be logged in to edit files");
    router.push(`/${repoType.value}s/${namespace.value}/${name.value}`);
    return;
  }

  if (!authStore.canWriteToNamespace(namespace.value)) {
    ElMessage.error("You don't have permission to edit this file");
    router.push(blobUrl.value);
    return;
  }

  loadFileContent();
});

// Warn on page leave if there are unsaved changes
onBeforeRouteLeave((to, from, next) => {
  if (fileContent.value !== originalContent.value) {
    const answer = confirm(
      "You have unsaved changes. Are you sure you want to leave this page?",
    );
    if (answer) {
      next();
    } else {
      next(false);
    }
  } else {
    next();
  }
});
</script>
