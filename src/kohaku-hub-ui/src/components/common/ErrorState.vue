<!--
  ErrorState.vue — shared "classified error" panel.

  Renders the right icon, title, and remediation copy for a
  classification from `utils/http-errors.js`. Two modes:

  - `mode="full-page"` — centered, large icon, suitable for a whole
    route page replacing its main content (blob / edit routes).
  - `mode="inline-panel"` — smaller, fits inside an in-context panel
    (README slot, tree-listing slot, etc.). Does not assume full
    viewport height.

  If the classification carries `sources[]` from the aggregated
  fallback body, the sources table renders in a collapsed
  `<details>` disclosure underneath the message so power users can
  see exactly which upstream sources were tried and what each one
  answered, without cluttering the default view for non-diagnostic
  users.

  The retry affordance is opt-in: pass `:retry` to render a
  "Retry" button that calls it. Callers can also supply a `slot` to
  add extra CTAs (e.g. "Open account settings" for gated errors).
-->

<script setup>
import { computed } from "vue";
import { defaultCopyFor, ERROR_KIND } from "@/utils/http-errors";

const props = defineProps({
  // Output of classifyResponse / classifyError:
  // { kind, status, errorCode, detail, sources }
  classification: { type: Object, required: true },
  mode: {
    type: String,
    default: "full-page",
    validator: (m) => m === "full-page" || m === "inline-panel",
  },
  // Optional retry callback. When provided, renders a Retry button.
  retry: { type: Function, default: null },
  // Optional override for title / hint copy. Useful when the caller
  // wants to customize wording (e.g. the blob page can say "Go back
  // to repo" instead of the generic advice), without reimplementing
  // kind detection.
  titleOverride: { type: String, default: null },
  hintOverride: { type: String, default: null },
});

const copy = computed(() => defaultCopyFor(props.classification?.kind));
const title = computed(() => props.titleOverride || copy.value.title);
const hint = computed(() => props.hintOverride || copy.value.hint);

const iconClass = computed(() => {
  switch (props.classification?.kind) {
    case ERROR_KIND.GATED:
      return "i-carbon-locked";
    case ERROR_KIND.FORBIDDEN:
      return "i-carbon-misuse";
    case ERROR_KIND.NOT_FOUND:
      return "i-carbon-document-unknown";
    case ERROR_KIND.UPSTREAM_UNAVAILABLE:
      return "i-carbon-cloud-offline";
    case ERROR_KIND.CORS:
      return "i-carbon-warning-alt";
    default:
      return "i-carbon-warning-alt";
  }
});

const iconColor = computed(() => {
  // Gated / forbidden / CORS all surface as amber — they are
  // actionable by the user. Not-found + upstream-unavailable get
  // the muted gray since the user can't do much besides retry.
  switch (props.classification?.kind) {
    case ERROR_KIND.GATED:
    case ERROR_KIND.FORBIDDEN:
    case ERROR_KIND.CORS:
      return "text-amber-500";
    default:
      return "text-gray-500 dark:text-gray-400";
  }
});

const containerClass = computed(() =>
  props.mode === "full-page"
    ? "py-16 flex flex-col items-center text-center"
    : "py-8 flex flex-col items-center text-center",
);
const iconSizeClass = computed(() =>
  props.mode === "full-page" ? "text-6xl" : "text-5xl",
);
const titleSizeClass = computed(() =>
  props.mode === "full-page"
    ? "mt-4 text-lg font-semibold"
    : "mt-4 text-sm font-medium",
);

const sourceRows = computed(() => {
  const sources = props.classification?.sources;
  if (!Array.isArray(sources)) return [];
  return sources.map((src) => ({
    name: src?.name ?? "(unknown)",
    url: src?.url ?? "",
    status: src?.status == null ? "-" : String(src.status),
    category: src?.category ?? "",
    message: typeof src?.message === "string" ? src.message : "",
  }));
});
</script>

<template>
  <div :class="containerClass">
    <div
      :class="`${iconClass} ${iconColor} ${iconSizeClass}`"
      data-testid="error-icon"
    />
    <p
      :class="`${titleSizeClass} text-gray-800 dark:text-gray-100`"
      data-testid="error-title"
    >
      {{ title }}
    </p>
    <p
      class="mt-2 text-xs text-gray-500 dark:text-gray-400 max-w-md break-words"
      data-testid="error-hint"
    >
      {{ hint }}
    </p>
    <p
      v-if="classification?.detail && classification.detail !== hint"
      class="mt-2 text-xs text-gray-400 dark:text-gray-500 max-w-md break-words font-mono"
      data-testid="error-detail"
    >
      {{ classification.detail }}
    </p>

    <slot name="actions">
      <el-button
        v-if="retry"
        class="mt-4"
        type="primary"
        plain
        @click="retry"
      >
        Retry
      </el-button>
    </slot>

    <div v-if="sourceRows.length" class="mt-4 w-full max-w-xl text-left">
      <details class="text-xs">
        <summary class="cursor-pointer text-gray-500 dark:text-gray-400 mb-2">
          Fallback sources tried ({{ sourceRows.length }})
        </summary>
        <el-table :data="sourceRows" size="small" :border="true">
          <el-table-column prop="name" label="Source" width="130" />
          <el-table-column prop="status" label="HTTP" width="70" />
          <el-table-column prop="category" label="Category" width="110" />
          <el-table-column prop="message" label="Message" />
        </el-table>
      </details>
    </div>
  </div>
</template>
