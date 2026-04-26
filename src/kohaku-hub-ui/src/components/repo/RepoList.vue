<!-- src/kohaku-hub-ui/src/components/repo/RepoList.vue -->
<template>
  <div class="space-y-4">
    <div
      v-for="repo in repos"
      :key="repo.id"
      class="card hover:shadow-md transition-shadow cursor-pointer"
      @click="goToRepo(repo)"
    >
      <div class="flex flex-col sm:flex-row items-start justify-between gap-3">
        <div class="flex-1 w-full">
          <div class="flex items-center gap-2 mb-2 flex-wrap">
            <div :class="getIconClass(type)" />
            <h3 class="text-base sm:text-lg font-semibold">
              <RouterLink
                :to="getRepoPath(repo)"
                class="block text-blue-600 dark:text-blue-400 hover:underline break-all"
                @click.stop
              >
                {{ repo.id }}
              </RouterLink>
            </h3>
            <el-tag v-if="repo.private" size="small" type="warning">
              Private
            </el-tag>
          </div>

          <div
            class="text-xs sm:text-sm text-gray-600 dark:text-gray-400 flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4"
          >
            <span>by {{ repo.author }}</span>
            <span v-if="repo.lastModified">
              Updated {{ formatDate(repo.lastModified) }}
            </span>
          </div>

          <div
            v-if="repo.tags && repo.tags.length"
            class="mt-2 flex gap-2 flex-wrap"
          >
            <el-tag
              v-for="tag in repo.tags.slice(0, 3)"
              :key="tag"
              size="small"
              effect="plain"
            >
              {{ tag }}
            </el-tag>
          </div>
        </div>

        <div
          class="flex items-center gap-4 text-xs sm:text-sm text-gray-500 dark:text-gray-400 w-full sm:w-auto justify-end"
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

    <div
      v-if="!repos || repos.length === 0"
      class="text-center py-12 text-gray-500 dark:text-gray-400"
    >
      No repositories found
    </div>
  </div>
</template>

<script setup>
import { formatRelativeTime } from "@/utils/datetime";

/**
 * @typedef {Object} Props
 * @property {Array} repos - Repository list
 * @property {'model'|'dataset'|'space'} type - Repository type
 */
const props = defineProps({
  repos: {
    type: Array,
    default: () => [],
  },
  type: {
    type: String,
    default: "model",
    validator: (v) => ["model", "dataset", "space"].includes(v),
  },
});

const router = useRouter();

function getIconClass(type) {
  const icons = {
    model: "i-carbon-model text-blue-500",
    dataset: "i-carbon-data-table text-green-500",
    space: "i-carbon-application text-purple-500",
  };
  return icons[type] || icons.model;
}

function formatDate(date) {
  return formatRelativeTime(date, "never");
}

function getRepoPath(repo) {
  const [namespace, name] = repo.id.split("/");
  return `/${props.type}s/${namespace}/${name}`;
}

function goToRepo(repo) {
  router.push(getRepoPath(repo));
}
</script>
