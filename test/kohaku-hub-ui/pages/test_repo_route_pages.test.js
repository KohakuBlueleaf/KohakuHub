import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RouterLinkStub } from "../helpers/vue";

const mocks = vi.hoisted(() => ({
  route: {
    path: "/models/mai_lin/demo",
    params: {
      namespace: "mai_lin",
      name: "demo",
      branch: "main",
    },
    query: {},
  },
}));

vi.mock("vue-router/auto", () => ({
  useRoute: () => mocks.route,
}));

vi.mock("@/components/pages/RepoListPage.vue", () => ({
  default: {
    name: "RepoListPage",
    props: ["repoType"],
    template: '<div data-repo-list-page="true">{{ repoType }}</div>',
  },
}));

vi.mock("@/components/repo/RepoViewer.vue", () => ({
  default: {
    name: "RepoViewer",
    props: ["repoType", "namespace", "name", "tab", "branch", "currentPath"],
    template:
      '<div data-repo-viewer="true">{{ repoType }}|{{ namespace }}|{{ name }}|{{ tab }}|{{ branch || "" }}|{{ currentPath || "" }}</div>',
  },
}));

import DatasetPage from "@/pages/datasets.vue";
import ModelPage from "@/pages/models.vue";
import SpacePage from "@/pages/spaces.vue";
import RepoIndexPage from "@/pages/[type]s/[namespace]/[name]/index.vue";
import RepoTreePage from "@/pages/[type]s/[namespace]/[name]/tree/[branch]/index.vue";

describe("repo route pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.route.path = "/models/mai_lin/demo";
    mocks.route.params = {
      namespace: "mai_lin",
      name: "demo",
      branch: "main",
    };
    mocks.route.query = {};
  });

  function mountPage(component) {
    return mount(component, {
      global: {
        stubs: {
          RouterLink: RouterLinkStub,
        },
      },
    });
  }

  it("binds the correct repository type for list pages", () => {
    expect(mountPage(ModelPage).text()).toContain("model");
    expect(mountPage(DatasetPage).text()).toContain("dataset");
    expect(mountPage(SpacePage).text()).toContain("space");
  });

  it("passes route-derived props into repo viewer wrappers", () => {
    mocks.route.path = "/datasets/aurora-labs/vision-set";
    mocks.route.params = {
      namespace: "aurora-labs",
      name: "vision-set",
      branch: "release",
    };
    mocks.route.query = { tab: "files" };

    const indexWrapper = mountPage(RepoIndexPage);
    expect(indexWrapper.text()).toContain(
      "dataset|aurora-labs|vision-set|files||",
    );

    mocks.route.path = "/spaces/mai_lin/demo/tree/dev";
    const treeWrapper = mountPage(RepoTreePage);
    expect(treeWrapper.text()).toContain("space|aurora-labs|vision-set|files|release|");
  });
});
