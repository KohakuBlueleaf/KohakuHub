import { defineConfig } from "vitest/config";

import baseConfig from "./vitest.config.js";

export default defineConfig({
  ...baseConfig,
  test: {
    ...baseConfig.test,
    coverage: {
      ...baseConfig.test.coverage,
      reportsDirectory: "../../coverage-ui-patch",
      include: [
        "src/components/repo/RepoViewer.vue",
        "src/utils/api.js",
      ],
    },
  },
});
