<!-- src/kohaku-hub-ui/src/pages/[type]s/[namespace]/[name]/tree/[branch]/[...path].vue -->
<template>
  <RepoViewer
    :repo-type="repoType"
    :namespace="namespace"
    :name="name"
    :branch="branch"
    :current-path="currentPath"
    tab="files"
  />
</template>

<script setup>
import RepoViewer from "@/components/repo/RepoViewer.vue";
import { normalizeCatchAllParam } from "@/utils/repo-paths";

const route = useRoute();

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
const currentPath = computed(() => normalizeCatchAllParam(route.params.path));
</script>
