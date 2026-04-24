import { fileURLToPath, URL } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";

const testRoot = fileURLToPath(new URL("../../test/kohaku-hub-ui", import.meta.url));
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const uiRoot = dirname(fileURLToPath(import.meta.url));
const uiNodeModules = resolve(uiRoot, "node_modules");

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      imports: [
        "vue",
        "pinia",
        {
          "vue-router": [
            "onBeforeRouteLeave",
            "onBeforeRouteUpdate",
            "useLink",
          ],
        },
        {
          "vue-router/auto": ["useRoute", "useRouter"],
        },
      ],
      dts: false,
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      vue: resolve(uiNodeModules, "vue/dist/vue.runtime.esm-bundler.js"),
      pinia: resolve(uiNodeModules, "pinia/dist/pinia.mjs"),
      "vue-router/auto": resolve(uiNodeModules, "vue-router/dist/vue-router.mjs"),
      "@vue/test-utils": resolve(
        uiNodeModules,
        "@vue/test-utils/dist/vue-test-utils.esm-bundler.mjs",
      ),
      // Test files live outside `src/kohaku-hub-ui/` so bare imports of
      // `element-plus` do not resolve from their location by default. That
      // matters for `vi.mock("element-plus", ...)` — without a canonical
      // alias, the mock and the component's real import resolve to
      // different specifiers and the mock silently no-ops. Pinning the
      // path here means both sides resolve to the same module id.
      "element-plus": resolve(uiNodeModules, "element-plus"),
    },
    dedupe: ["vue", "pinia", "element-plus"],
    conditions: ["module", "browser", "development"],
  },
  server: {
    fs: {
      allow: [repoRoot],
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [`${testRoot}/setup/vitest.setup.js`],
    include: [`${testRoot}/**/*.test.{js,ts}`],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary", "cobertura"],
      reportsDirectory: "../../coverage-ui",
      include: [
        "src/App.vue",
        "src/stores/auth.js",
        "src/stores/theme.js",
        "src/utils/api.js",
        "src/utils/clipboard.js",
        "src/utils/datetime.js",
        "src/utils/externalTokens.js",
        "src/utils/file-preview.js",
        "src/utils/lfs.js",
        "src/utils/metadata-helpers.js",
        "src/utils/parquet.js",
        "src/utils/repoSortPreference.js",
        "src/utils/safetensors.js",
        "src/utils/tag-parser.js",
        "src/utils/yaml-parser.js",
        "src/components/layout/TheFooter.vue",
        "src/components/layout/TheHeader.vue",
        "src/components/pages/RepoListPage.vue",
        "src/components/profile/SocialLinks.vue",
        "src/components/repo/FileUploader.vue",
        "src/components/repo/RepoList.vue",
        "src/components/repo/RepoViewer.vue",
        "src/components/repo/preview/FilePreviewDialog.vue",
        "src/components/repo/metadata/LanguageCard.vue",
        "src/components/repo/metadata/LicenseCard.vue",
        "src/pages/index.vue",
        "src/pages/login.vue",
        "src/pages/register.vue",
        "src/pages/models.vue",
        "src/pages/datasets.vue",
        "src/pages/spaces.vue",
        "src/pages/new.vue",
        "src/pages/[type]s/[namespace]/[name]/index.vue",
        "src/pages/[type]s/[namespace]/[name]/tree/[branch]/index.vue",
        "src/pages/[type]s/[namespace]/[name]/upload/[branch].vue",
      ],
      exclude: [
        "src/components/HelloWorld.vue",
        "src/components.d.ts",
        "src/auto-imports.d.ts",
        "src/typed-router.d.ts",
        "src/testing/**/*.js",
      ],
    },
  },
  optimizeDeps: {
    include: ["pinia", "vue-router", "@vue/test-utils"],
  },
  ssr: {
    noExternal: ["pinia", "vue-router", "@vue/test-utils"],
  },
});
