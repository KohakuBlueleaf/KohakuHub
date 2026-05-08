<!--
  TarBrowserDialog.vue

  Modal wrapper around <TarBrowserPanel> for the file-list icon
  shortcut. The same panel renders inline on the standalone blob
  page when the user navigates to a .tar that has a sibling .json,
  so the listing UX, hash banner, member preview and download
  paths stay identical across both surfaces.
-->

<script setup>
import { computed } from "vue";
import TarBrowserPanel from "@/components/repo/preview/TarBrowserPanel.vue";

const props = defineProps({
  visible: { type: Boolean, required: true },
  tarUrl: { type: String, required: true },
  indexUrl: { type: String, required: true },
  filename: { type: String, required: true },
  // Tree-API entry for the .tar — drives the hash banner.
  tarTreeEntry: { type: Object, default: null },
});
const emit = defineEmits(["update:visible"]);

const dialogVisible = computed({
  get: () => props.visible,
  set: (value) => emit("update:visible", value),
});
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    :title="`Indexed tar · ${filename}`"
    width="900px"
    top="6vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <TarBrowserPanel
      v-if="dialogVisible"
      :tar-url="tarUrl"
      :index-url="indexUrl"
      :filename="filename"
      :tar-tree-entry="tarTreeEntry"
    />
    <template #footer>
      <el-button @click="dialogVisible = false">Close</el-button>
    </template>
  </el-dialog>
</template>
