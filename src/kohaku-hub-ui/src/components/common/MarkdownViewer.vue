<!-- src/kohaku-hub-ui/src/components/common/MarkdownViewer.vue -->
<template>
  <div
    ref="markdownContainer"
    class="markdown-body markdown-container"
    v-html="renderedHTML"
  />
</template>

<script setup>
import { renderMarkdown } from "@/utils/markdown";
import { useThemeStore } from "@/stores/theme";
import mermaid from "mermaid";
import Panzoom from "panzoom";

const themeStore = useThemeStore();

/**
 * @typedef {Object} Props
 * @property {string} content - Markdown content to render
 * @property {boolean} stripFrontmatter - Strip YAML frontmatter (default: true for repo cards)
 * @property {string} repoType - Repository type (model/dataset/space) for image path resolution
 * @property {string} namespace - Repository namespace for image path resolution
 * @property {string} name - Repository name for image path resolution
 * @property {string} branch - Repository branch for image path resolution (default: 'main')
 */
const props = defineProps({
  content: {
    type: String,
    default: "",
  },
  stripFrontmatter: {
    type: Boolean,
    default: true,
  },
  repoType: {
    type: String,
    default: "",
  },
  namespace: {
    type: String,
    default: "",
  },
  name: {
    type: String,
    default: "",
  },
  branch: {
    type: String,
    default: "main",
  },
});

const markdownContainer = ref(null);

const renderedHTML = computed(() => {
  return renderMarkdown(props.content, {
    stripFrontmatter: props.stripFrontmatter,
    repoContext:
      props.repoType && props.namespace && props.name
        ? {
            repoType: props.repoType,
            namespace: props.namespace,
            name: props.name,
            branch: props.branch,
          }
        : null,
  });
});

// Get theme from store (reactive)
const isDark = computed(() => themeStore.isDark);

// Use Mermaid's built-in themes - they're designed properly
const getMermaidConfig = (dark) => ({
  startOnLoad: false,
  securityLevel: "loose",
  fontFamily: "ui-sans-serif, system-ui, sans-serif",
  theme: dark ? "dark" : "default",
  logLevel: "fatal", // Only log fatal errors, suppress warnings
});

// Initialize Mermaid
mermaid.initialize(getMermaidConfig(isDark.value));

// Render mermaid diagrams after content updates
async function renderMermaidDiagrams() {
  await nextTick();

  if (!markdownContainer.value) return;

  // Re-initialize with current theme before rendering
  mermaid.initialize(getMermaidConfig(isDark.value));

  // Check if this is initial render or theme change
  const mermaidBlocks = markdownContainer.value.querySelectorAll(
    "pre code.language-mermaid",
  );
  const existingWrappers =
    markdownContainer.value.querySelectorAll(".mermaid-wrapper");

  if (mermaidBlocks.length > 0) {
    // Initial render: convert code blocks to wrappers
    for (let i = 0; i < mermaidBlocks.length; i++) {
      const block = mermaidBlocks[i];
      const code = block.textContent;
      const pre = block.parentElement;

      try {
        // Create wrapper for diagram + controls
        const wrapper = document.createElement("div");
        wrapper.className = "mermaid-wrapper";
        wrapper.setAttribute("data-mermaid-code", code); // Store code for re-rendering

        // Replace immediately
        pre.replaceWith(wrapper);

        // Render diagram into wrapper
        await renderSingleDiagram(wrapper, code, i);
      } catch (err) {
        console.error("Mermaid rendering error:", err);
        const errorDiv = document.createElement("div");
        errorDiv.className = "mermaid-error";
        errorDiv.innerHTML = `
          <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-4 text-sm">
            <div class="font-semibold text-red-600 dark:text-red-400 mb-2">Mermaid Rendering Error</div>
            <div class="text-red-700 dark:text-red-300">${err.message}</div>
          </div>
        `;
        pre.parentElement?.insertBefore(errorDiv, pre);
      }
    }
  } else if (existingWrappers.length > 0) {
    // Theme change: re-render existing diagrams
    for (let i = 0; i < existingWrappers.length; i++) {
      const wrapper = existingWrappers[i];
      const code = wrapper.getAttribute("data-mermaid-code");
      if (code) {
        // Remove old diagram and controls
        wrapper
          .querySelectorAll(".mermaid-diagram, .mermaid-controls")
          .forEach((el) => el.remove());
        // Re-render with new theme
        await renderSingleDiagram(wrapper, code, i);
      }
    }
  }
}

// Helper function to render a single diagram
async function renderSingleDiagram(wrapper, code, index) {
  // Create container for mermaid diagram
  const id = `mermaid-${Date.now()}-${index}`;
  const container = document.createElement("div");
  container.className = "mermaid-diagram";
  container.id = id;

  // Render mermaid with current theme
  let svg;
  try {
    const result = await mermaid.render(id, code);
    svg = result.svg;
  } catch (err) {
    // Don't show Mermaid's error diagram, show our own error message
    console.error("Mermaid render error:", err);
    container.innerHTML = `
      <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-4 text-sm">
        <div class="font-semibold text-red-600 dark:text-red-400 mb-2">Diagram Syntax Error</div>
        <div class="text-red-700 dark:text-red-300 text-xs">This diagram has a syntax error and cannot be rendered.</div>
      </div>
    `;
    wrapper.appendChild(container);
    return; // Exit early
  }

  container.innerHTML = svg;

  // Get SVG element
  const svgElement = container.querySelector("svg");

  if (svgElement) {
    // Remove any inline background from Mermaid
    svgElement.style.backgroundColor = "transparent";
  }

  // Create zoom controls
  const controls = document.createElement("div");
  controls.className = "mermaid-controls";
  controls.innerHTML = `
    <button class="mermaid-zoom-btn mermaid-zoom-in" title="Zoom In">
      <div class="i-carbon-zoom-in"></div>
    </button>
    <button class="mermaid-zoom-btn mermaid-zoom-out" title="Zoom Out">
      <div class="i-carbon-zoom-out"></div>
    </button>
    <button class="mermaid-zoom-btn mermaid-zoom-reset" title="Reset Zoom">
      <div class="i-carbon-zoom-reset"></div>
    </button>
    <button class="mermaid-zoom-btn mermaid-fullscreen" title="Fullscreen">
      <div class="i-carbon-maximize"></div>
    </button>
  `;

  // Initialize panzoom on SVG element
  let panzoomInstance = null;
  if (svgElement) {
    panzoomInstance = Panzoom(svgElement, {
      maxZoom: 3,
      minZoom: 0.3,
      bounds: false,
      boundsPadding: 0.1,
    });

    // Enable mouse wheel zoom on container
    container.addEventListener("wheel", (e) =>
      panzoomInstance.zoomWithWheel(e),
    );
  }

  // Zoom button handlers
  controls.querySelector(".mermaid-zoom-in").addEventListener("click", (e) => {
    e.stopPropagation();
    if (panzoomInstance) panzoomInstance.zoomIn();
  });

  controls.querySelector(".mermaid-zoom-out").addEventListener("click", (e) => {
    e.stopPropagation();
    if (panzoomInstance) panzoomInstance.zoomOut();
  });

  controls
    .querySelector(".mermaid-zoom-reset")
    .addEventListener("click", (e) => {
      e.stopPropagation();
      if (panzoomInstance) {
        panzoomInstance.moveTo(0, 0);
        panzoomInstance.zoomAbs(0, 0, 1);
      }
    });

  controls
    .querySelector(".mermaid-fullscreen")
    .addEventListener("click", (e) => {
      e.stopPropagation();
      if (!document.fullscreenElement) {
        wrapper.requestFullscreen?.() || wrapper.webkitRequestFullscreen?.();
      } else {
        document.exitFullscreen?.() || document.webkitExitFullscreen?.();
      }
    });

  // Assemble and insert
  wrapper.appendChild(controls);
  wrapper.appendChild(container);
}

// Watch for content changes and re-render mermaid
watch(
  () => props.content,
  async () => {
    await nextTick();
    await renderMermaidDiagrams();
  },
  { flush: "post" },
);

// Watch for theme changes and re-initialize mermaid
watch(isDark, (newValue) => {
  mermaid.initialize(getMermaidConfig(newValue));
  // Re-render diagrams when theme changes
  renderMermaidDiagrams();
});

onMounted(() => {
  renderMermaidDiagrams();
});
</script>

<style scoped>
/* Strict containment for markdown content */
.markdown-container {
  /* Prevent overflow but don't break layout */
  max-width: 100%;
  overflow-x: auto;
}

/* GitHub-like markdown styles */
.markdown-body {
  font-size: 14px;
  line-height: 1.6;
  word-wrap: break-word;
}

@media (min-width: 640px) {
  .markdown-body {
    font-size: 15px;
  }
}

@media (min-width: 768px) {
  .markdown-body {
    font-size: 16px;
  }
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  margin-top: 24px;
  margin-bottom: 16px;
  font-weight: 600;
  line-height: 1.25;
}

.markdown-body :deep(h1) {
  font-size: 1.6em;
  border-bottom: 1px solid #eaecef;
  padding-bottom: 0.3em;
}

@media (min-width: 768px) {
  .markdown-body :deep(h1) {
    font-size: 2em;
  }
}

.markdown-body :deep(h2) {
  font-size: 1.3em;
  border-bottom: 1px solid #eaecef;
  padding-bottom: 0.3em;
}

@media (min-width: 768px) {
  .markdown-body :deep(h2) {
    font-size: 1.5em;
  }
}

.markdown-body :deep(h3) {
  font-size: 1.15em;
}

@media (min-width: 768px) {
  .markdown-body :deep(h3) {
    font-size: 1.25em;
  }
}

.markdown-body :deep(p) {
  margin-top: 0;
  margin-bottom: 16px;
}

.markdown-body :deep(code) {
  padding: 0.2em 0.4em;
  margin: 0;
  font-size: 80%;
  background-color: rgba(175, 184, 193, 0.2);
  border-radius: 6px;
  font-family: ui-monospace, monospace;
}

@media (min-width: 768px) {
  .markdown-body :deep(code) {
    font-size: 85%;
  }
}

.markdown-body :deep(pre) {
  padding: 12px;
  overflow: auto;
  font-size: 80%;
  line-height: 1.45;
  background-color: #f6f8fa;
  border-radius: 6px;
  margin-bottom: 16px;
}

@media (min-width: 768px) {
  .markdown-body :deep(pre) {
    padding: 16px;
    font-size: 85%;
  }
}

.markdown-body :deep(pre code) {
  display: inline;
  max-width: auto;
  padding: 0;
  margin: 0;
  overflow: visible;
  line-height: inherit;
  word-wrap: normal;
  background-color: transparent;
  border: 0;
}

.markdown-body :deep(a) {
  color: #0969da;
  text-decoration: none;
}

.markdown-body :deep(a:hover) {
  text-decoration: underline;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 2em;
  margin-bottom: 16px;
  list-style: revert;
}

.markdown-body :deep(li) {
  margin-bottom: 0.25em;
}

.markdown-body :deep(blockquote) {
  padding: 0 1em;
  color: #57606a;
  border-left: 0.25em solid #d0d7de;
  margin-bottom: 16px;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 16px;
}

.markdown-body :deep(table th),
.markdown-body :deep(table td) {
  padding: 6px 13px;
  border: 1px solid #d0d7de;
}

.markdown-body :deep(table th) {
  font-weight: 600;
  background-color: #f6f8fa;
}

.markdown-body :deep(img) {
  max-width: 100%;
  height: auto;
}

.markdown-body :deep(hr) {
  height: 0.25em;
  padding: 0;
  margin: 24px 0;
  background-color: #d0d7de;
  border: 0;
}

/* Ensure all custom content stays within bounds */
.markdown-body :deep(*) {
  max-width: 100%;
  box-sizing: border-box;
}

/* Allow radio inputs for galleries but keep them functional */
.markdown-body :deep(input[type="radio"]) {
  /* Radio inputs can be visible or hidden based on custom styles */
  pointer-events: auto;
}

/* Style task list checkboxes */
.markdown-body :deep(input[type="checkbox"].task-list-checkbox) {
  margin-right: 0.5em;
  vertical-align: middle;
  pointer-events: none; /* Disabled checkboxes */
}

/* Style list items containing task list checkboxes */
.markdown-body :deep(li:has(> input[type="checkbox"].task-list-checkbox)) {
  list-style-type: none;
}

/* For browsers that don't support :has(), use a fallback */
.markdown-body :deep(ul li input[type="checkbox"].task-list-checkbox) {
  margin-left: -1.5em;
}

/* Ensure custom containers don't overflow */
.markdown-body :deep(div),
.markdown-body :deep(section),
.markdown-body :deep(article) {
  max-width: 100%;
  overflow-x: auto;
}

/* Fix for gallery containers that use position: relative */
.markdown-body :deep(.gallery-container),
.markdown-body :deep([class*="gallery"]) {
  max-width: 100%;
  overflow: hidden;
}

/* Ensure slides and content areas stay contained */
.markdown-body :deep(.content-area),
.markdown-body :deep(.slide) {
  max-width: 100%;
  box-sizing: border-box;
}

/* Dark mode support for markdown elements */
.markdown-body :deep(h1),
.markdown-body :deep(h2) {
  border-bottom-color: #eaecef;
}

@media (prefers-color-scheme: dark) {
  .markdown-body :deep(h1),
  .markdown-body :deep(h2) {
    border-bottom-color: rgba(255, 255, 255, 0.1);
  }

  .markdown-body :deep(pre) {
    background-color: rgba(0, 0, 0, 0.2);
  }

  .markdown-body :deep(code) {
    background-color: rgba(255, 255, 255, 0.1);
  }

  .markdown-body :deep(table th) {
    background-color: rgba(0, 0, 0, 0.2);
  }

  .markdown-body :deep(table th),
  .markdown-body :deep(table td) {
    border-color: rgba(255, 255, 255, 0.1);
  }

  .markdown-body :deep(blockquote) {
    color: #8b949e;
    border-left-color: rgba(255, 255, 255, 0.2);
  }
}

/* Handle dark mode class from theme store */
:global(.dark) .markdown-body :deep(h1),
:global(.dark) .markdown-body :deep(h2) {
  border-bottom-color: rgba(255, 255, 255, 0.1);
}

:global(.dark) .markdown-body :deep(pre) {
  background-color: rgba(0, 0, 0, 0.3);
}

:global(.dark) .markdown-body :deep(code) {
  background-color: rgba(255, 255, 255, 0.1);
}

:global(.dark) .markdown-body :deep(table th) {
  background-color: rgba(0, 0, 0, 0.3);
}

:global(.dark) .markdown-body :deep(table th),
:global(.dark) .markdown-body :deep(table td) {
  border-color: rgba(255, 255, 255, 0.1);
}

:global(.dark) .markdown-body :deep(blockquote) {
  color: #8b949e;
  border-left-color: rgba(255, 255, 255, 0.2);
}

:global(.dark) .markdown-body :deep(a) {
  color: #58a6ff;
}

/* Mermaid wrapper with controls - light mode default */
.markdown-body :deep(.mermaid-wrapper) {
  position: relative;
  margin: 24px 0;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  background-color: #ffffff !important;
  overflow: hidden;
}

/* Dark mode wrapper */
.dark .markdown-body :deep(.mermaid-wrapper) {
  border-color: #374151 !important;
  background-color: #1f2937 !important;
}

/* Ensure proper background in SVG */
.markdown-body :deep(.mermaid-wrapper svg) {
  background: transparent !important;
}

/* Fullscreen mode - full width */
.markdown-body :deep(.mermaid-wrapper:fullscreen) {
  background-color: white !important;
  display: flex;
  flex-direction: column;
  width: 100vw !important;
  height: 100vh !important;
  padding: 20px !important;
  align-items: center;
  justify-content: center;
}

.dark .markdown-body :deep(.mermaid-wrapper:fullscreen) {
  background-color: #111827 !important;
}

/* Zoom controls - light mode */
.markdown-body :deep(.mermaid-controls) {
  position: absolute;
  top: 12px;
  right: 12px;
  display: flex;
  gap: 4px;
  z-index: 10;
  background-color: rgba(255, 255, 255, 0.95) !important;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

/* Dark mode controls */
.dark .markdown-body :deep(.mermaid-controls) {
  background-color: rgba(31, 41, 55, 0.95) !important;
  border-color: #4b5563 !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
}

/* Zoom buttons - light mode */
.markdown-body :deep(.mermaid-zoom-btn) {
  width: 32px;
  height: 32px;
  border: none;
  background-color: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: #4b5563 !important;
  transition: all 0.2s;
}

.markdown-body :deep(.mermaid-zoom-btn:hover) {
  background-color: rgba(59, 130, 246, 0.15) !important;
  color: #3b82f6 !important;
}

/* Dark mode buttons */
.dark .markdown-body :deep(.mermaid-zoom-btn) {
  color: #d1d5db !important;
}

.dark .markdown-body :deep(.mermaid-zoom-btn:hover) {
  background-color: rgba(59, 130, 246, 0.3) !important;
  color: #60a5fa !important;
}

.markdown-body :deep(.mermaid-zoom-btn div) {
  width: 20px;
  height: 20px;
}

/* Mermaid diagram container */
.markdown-body :deep(.mermaid-diagram) {
  text-align: center;
  background: transparent;
  overflow: visible;
  min-height: 200px;
  max-height: 600px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  cursor: grab;
}

.markdown-body :deep(.mermaid-diagram:active) {
  cursor: grabbing;
}

.markdown-body :deep(.mermaid-diagram svg) {
  max-width: 100%;
  max-height: 600px;
  width: auto;
  height: auto;
}

/* Fullscreen: remove limits and expand */
.markdown-body :deep(.mermaid-wrapper:fullscreen .mermaid-diagram) {
  max-height: none !important;
  max-width: none !important;
  width: 100% !important;
  height: 100% !important;
  flex: 1;
}

/* Mermaid error styling */
.markdown-body :deep(.mermaid-error) {
  margin: 16px 0;
}
</style>
