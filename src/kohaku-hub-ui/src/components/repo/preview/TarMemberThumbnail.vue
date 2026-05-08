<!--
  TarMemberThumbnail.vue

  Lazy thumbnail for a single image member inside an indexed-tar
  listing. Shows the placeholder icon until the row scrolls into
  view, then drives the strategy chain in `utils/tar-thumbnail`
  (EXIF probe → small/medium full read → canvas resize) to render
  a ~128 px JPEG thumbnail in place of the icon. Failures stay on
  the placeholder silently — never a thrown render or a toast.

  Stable wrapper: the same <div> is always mounted. The IO target
  doesn't change when state flips, so the observer keeps tracking
  the row through every transition.
-->

<script setup>
import { ref, computed } from "vue";
import { useTarThumbnail, useThumbnailToggle } from "@/utils/tar-thumbnail";

const props = defineProps({
  // Resolve URL for the .tar file (member.offset is taken from the
  // sidecar entry, not the URL).
  tarUrl: { type: String, required: true },
  // Tree-builder node — we only need .name, .offset, .size, .path.
  member: { type: Object, required: true },
  // The Carbon icon class the listing already uses for this entry,
  // shown verbatim while the thumbnail is idle / loading / fallback.
  placeholderIcon: { type: String, required: true },
  // Optional fixed pixel size (square). When omitted, the wrapper
  // stretches to its parent's width and forces a 1:1 aspect ratio
  // — useful in grid view where each card sets the container width.
  size: { type: Number, default: null },
});

const rootRef = ref(null);
const { enabled } = useThumbnailToggle();

// useTarThumbnail manages IntersectionObserver, AbortController,
// pool slot, cache lookup, and state transitions. We only consume
// its reactive output.
const { state, thumbUrl } = useTarThumbnail({
  tarUrl: props.tarUrl,
  member: props.member,
  rootRef,
});

// Fixed-size mode (list view) sets explicit width/height in px.
// Auto-size mode (grid view) lets the wrapper stretch to the
// parent's inner width and forces a 1:1 box via aspect-square so
// the thumbnail fills the card horizontally without cropping.
const containerStyle = computed(() =>
  props.size != null
    ? { width: `${props.size}px`, height: `${props.size}px` }
    : null,
);
const containerSizingClass = computed(() =>
  props.size != null ? "" : "w-full aspect-square",
);
// Use a slightly bigger placeholder icon when we're in auto-size
// mode so it scales with the (now larger) container instead of
// staying at the original list-row 20-px font size.
const placeholderSizeClass = computed(() =>
  props.size != null ? "text-xl" : "text-4xl",
);

// Placeholder is rendered at icon-font-size scale to match the
// existing listing icons. Use the same Carbon mask that the panel
// computed for this entry — this way the thumbnailed and
// non-thumbnailed rows look identical when no preview is loaded.
const showThumbnail = computed(
  () => enabled.value && state.value === "ready" && !!thumbUrl.value,
);
</script>

<template>
  <div
    ref="rootRef"
    class="flex-shrink-0 inline-flex items-center justify-center overflow-hidden rounded bg-gray-100 dark:bg-gray-800"
    :class="containerSizingClass"
    :style="containerStyle"
  >
    <img
      v-if="showThumbnail"
      :src="thumbUrl"
      :alt="member.name"
      class="w-full h-full object-contain"
      loading="lazy"
      decoding="async"
      draggable="false"
    />
    <div
      v-else
      :class="[placeholderIcon, placeholderSizeClass]"
    />
  </div>
</template>
