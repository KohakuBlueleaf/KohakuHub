import { fileURLToPath, URL } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";

const testRoot = fileURLToPath(
  new URL("../../test/kohaku-hub-admin", import.meta.url),
);
const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const adminRoot = dirname(fileURLToPath(import.meta.url));
const adminNodeModules = resolve(adminRoot, "node_modules");

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      imports: ["vue", "pinia", "vue-router"],
      dts: false,
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      vue: resolve(adminNodeModules, "vue/dist/vue.runtime.esm-bundler.js"),
      pinia: resolve(adminNodeModules, "pinia/dist/pinia.mjs"),
      "vue-router": resolve(adminNodeModules, "vue-router/dist/vue-router.mjs"),
      "@vue/test-utils": resolve(
        adminNodeModules,
        "@vue/test-utils/dist/vue-test-utils.esm-bundler.mjs",
      ),
    },
    dedupe: ["vue", "pinia"],
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
      reportsDirectory: "../../coverage-ui-admin",
      include: [
        "src/App.vue",
        "src/components/AdminLayout.vue",
        "src/pages/login.vue",
        "src/stores/admin.js",
        "src/stores/theme.js",
        "src/utils/api.js",
        "src/utils/clipboard.js",
      ],
      exclude: [
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
