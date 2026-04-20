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
      pinia: resolve(uiNodeModules, "pinia/dist/pinia.mjs"),
      "vue-router/auto": resolve(uiNodeModules, "vue-router/dist/vue-router.mjs"),
      "@vue/test-utils": resolve(
        uiNodeModules,
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
      reportsDirectory: "../../coverage-ui",
      include: [
        "src/utils/**/*.js",
        "src/stores/**/*.js",
        "src/components/**/*.vue",
      ],
      exclude: [
        "src/components/HelloWorld.vue",
        "src/components.d.ts",
        "src/auto-imports.d.ts",
        "src/typed-router.d.ts",
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
