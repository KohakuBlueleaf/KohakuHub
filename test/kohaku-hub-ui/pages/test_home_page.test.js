import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";
import repoInfo from "../fixtures/repo-info.json";
import userOverview from "../fixtures/user-overview.json";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
    replace: vi.fn(),
  },
  route: {
    params: {},
    query: {},
  },
  repoApi: {
    listRepos: vi.fn(),
  },
  repoSortPreference: {
    getRepoSortPreference: vi.fn(),
    setRepoSortPreference: vi.fn(),
  },
  elMessage: {
    error: vi.fn(),
  },
}));

vi.mock("vue-router/auto", () => ({
  useRouter: () => mocks.router,
  useRoute: () => mocks.route,
}));

vi.mock("@/utils/api", () => ({
  repoAPI: mocks.repoApi,
}));

vi.mock("@/utils/repoSortPreference", () => ({
  getRepoSortPreference: mocks.repoSortPreference.getRepoSortPreference,
  setRepoSortPreference: mocks.repoSortPreference.setRepoSortPreference,
}));

vi.mock("element-plus", () => ({
  ElMessage: mocks.elMessage,
}));

import HomePage from "@/pages/index.vue";

describe("home page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    mocks.route.query = {};
    mocks.repoSortPreference.getRepoSortPreference.mockReturnValue("trending");
    mocks.repoApi.listRepos.mockImplementation(async (type) => {
      if (type === "model") return { data: [repoInfo] };
      if (type === "dataset") return { data: userOverview.datasets };
      return { data: userOverview.spaces };
    });
  });

  function mountPage() {
    return mount(HomePage, {
      global: {
        mocks: {
          $router: mocks.router,
        },
        stubs: {
          ...ElementPlusStubs,
          RouterLink: RouterLinkStub,
        },
      },
    });
  }

  it("loads repo stats, routes hero and repo actions, and persists sort changes", async () => {
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.repoApi.listRepos).toHaveBeenNthCalledWith(1, "model", {
      limit: 100,
      sort: "trending",
      fallback: false,
    });
    expect(mocks.repoApi.listRepos).toHaveBeenNthCalledWith(2, "dataset", {
      limit: 100,
      sort: "trending",
      fallback: false,
    });
    expect(mocks.repoApi.listRepos).toHaveBeenNthCalledWith(3, "space", {
      limit: 100,
      sort: "trending",
      fallback: false,
    });

    expect(wrapper.text()).toContain("Welcome to KohakuHub");
    expect(wrapper.text()).toContain("🔥 Trending");
    expect(wrapper.text()).toContain("mai_lin/lineart-caption-base");
    expect(wrapper.text()).toContain("mai_lin/street-sign-zh-en");
    expect(wrapper.text()).toContain("mai_lin/mai_lin");

    const buttons = wrapper.findAll("button");
    await buttons.find((button) => button.text().includes("Get Started")).trigger(
      "click",
    );
    await buttons
      .find((button) => button.text().includes("Host Your Own Hub"))
      .trigger("click");
    await buttons
      .find((button) => button.text().includes("View all models"))
      .trigger("click");

    await wrapper.get('select[data-el-select="true"]').setValue("likes");
    await flushPromises();

    expect(mocks.repoSortPreference.setRepoSortPreference).toHaveBeenCalledWith({
      scope: "home",
      repoType: "all",
      value: "likes",
    });
    expect(mocks.router.push).toHaveBeenCalledWith("/get-started");
    expect(mocks.router.push).toHaveBeenCalledWith("/self-hosted");
    expect(mocks.router.push).toHaveBeenCalledWith("/models");
  });

it("handles verification error query params and cleans up the URL", async () => {
    mocks.route.query = {
      error: "invalid_token",
      message: encodeURIComponent("Invitation expired"),
    };

    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.router.replace).toHaveBeenCalledWith("/");
    expect(wrapper.text()).toContain("Welcome to KohakuHub");
  });
});
