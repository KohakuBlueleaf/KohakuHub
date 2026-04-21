import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ElementPlusStubs,
  InvalidElFormStub,
  RouterLinkStub,
} from "../helpers/vue";
import repoInfo from "../fixtures/repo-info.json";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
  },
  route: {
    params: {},
    query: {},
  },
  repoApi: {
    listRepos: vi.fn(),
    create: vi.fn(),
  },
  orgApi: {
    getUserOrgs: vi.fn(),
  },
  repoSortPreference: {
    getRepoSortPreference: vi.fn(),
    setRepoSortPreference: vi.fn(),
  },
  elMessage: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("vue-router/auto", () => ({
  useRouter: () => mocks.router,
  useRoute: () => mocks.route,
}));

vi.mock("@/utils/api", () => ({
  repoAPI: mocks.repoApi,
  orgAPI: mocks.orgApi,
}));

vi.mock("@/utils/repoSortPreference", () => ({
  getRepoSortPreference: mocks.repoSortPreference.getRepoSortPreference,
  setRepoSortPreference: mocks.repoSortPreference.setRepoSortPreference,
}));

vi.mock("element-plus", () => ({
  ElMessage: mocks.elMessage,
}));

import RepoListPage from "@/components/pages/RepoListPage.vue";
import { useAuthStore } from "@/stores/auth";

describe("RepoListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    mocks.repoSortPreference.getRepoSortPreference.mockReturnValue("likes");
    mocks.repoApi.listRepos.mockResolvedValue({
      data: [
        repoInfo,
        { ...repoInfo, id: "alice/other-model", author: "alice" },
      ],
    });
    mocks.orgApi.getUserOrgs.mockResolvedValue({
      data: {
        organizations: [{ name: "acme" }],
      },
    });
    mocks.repoApi.create.mockResolvedValue({
      data: {
        repo_id: "acme/fresh-model",
      },
    });
  });

  function mountPage(repoType = "model", extraStubs = {}) {
    return mount(RepoListPage, {
      props: {
        repoType,
      },
      global: {
        stubs: {
          ...ElementPlusStubs,
          ...extraStubs,
          RouterLink: RouterLinkStub,
        },
      },
    });
  }

  it("loads repos, filters them, persists sort preference, and creates a new repo", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };

    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.repoApi.listRepos).toHaveBeenCalledWith("model", {
      limit: 100,
      sort: "likes",
      fallback: false,
    });
    expect(wrapper.text()).toContain("mai_lin/lineart-caption-base");
    expect(wrapper.text()).toContain("alice/other-model");
    expect(wrapper.text()).toContain("New Model");

    const searchInput = wrapper.get('input[placeholder="Search models..."]');
    await searchInput.setValue("other");
    expect(wrapper.text()).toContain("alice/other-model");
    expect(wrapper.text()).not.toContain("mai_lin/lineart-caption-base");

    const sortSelect = wrapper.get('select[data-el-select="true"]');
    await sortSelect.setValue("recent");
    await flushPromises();

    expect(mocks.repoSortPreference.setRepoSortPreference).toHaveBeenCalledWith(
      {
        scope: "repo",
        repoType: "model",
        value: "recent",
      },
    );
    expect(mocks.repoApi.listRepos).toHaveBeenLastCalledWith("model", {
      limit: 100,
      sort: "recent",
      fallback: false,
    });

    const createButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("New Model"));
    await createButton.trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-el-dialog="Create New Model"]').exists()).toBe(
      true,
    );

    await wrapper.get('input[placeholder="my-model"]').setValue("fresh-model");
    await wrapper
      .get('select[aria-label="Select organization or leave empty"]')
      .setValue("acme");
    await wrapper.get('input[type="checkbox"]').setValue(true);

    const createDialogButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Create Model"));
    await createDialogButton.trigger("click");
    await flushPromises();

    expect(mocks.orgApi.getUserOrgs).toHaveBeenCalledWith("alice");
    expect(mocks.repoApi.create).toHaveBeenCalledWith({
      type: "model",
      name: "fresh-model",
      organization: "acme",
      private: true,
    });
    expect(mocks.router.push).toHaveBeenCalledWith("/models/acme/fresh-model");
  });

  it("handles list loading failures and hides creation controls for visitors", async () => {
    mocks.repoApi.listRepos.mockRejectedValue(new Error("boom"));

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).not.toContain("New Model");
  });

  it("falls back to the current user when the backend omits repo_id", async () => {
    const authStore = useAuthStore();
    authStore.user = { username: "mai_lin" };
    authStore.userOrganizations = [];
    mocks.repoApi.create.mockResolvedValue({
      data: {},
    });

    const wrapper = mountPage();
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("New Model"));
    await createButton.trigger("click");
    await flushPromises();

    await wrapper.get('input[placeholder="my-model"]').setValue("fresh-model");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Create Model"))
      .trigger("click");
    await flushPromises();

    expect(mocks.repoApi.create).toHaveBeenCalledWith({
      type: "model",
      name: "fresh-model",
      organization: null,
      private: false,
    });
    expect(mocks.router.push).toHaveBeenCalledWith(
      "/models/mai_lin/fresh-model",
    );
  });

  it("renders alternate repository type labels for non-model pages", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };

    const wrapper = mountPage("space");
    await flushPromises();

    expect(wrapper.text()).toContain("Spaces");
    expect(wrapper.text()).toContain("Discover ML demos and applications");
    expect(wrapper.text()).toContain("New Space");
  });

  it("filters by author and reports organization or creation failures", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };
    mocks.repoApi.listRepos.mockResolvedValue({
      data: [{ ...repoInfo, id: "team/project", author: "alice" }],
    });
    mocks.orgApi.getUserOrgs.mockRejectedValue(new Error("boom"));
    mocks.repoApi.create.mockRejectedValue(new Error("boom"));

    const wrapper = mountPage();
    await flushPromises();

    await wrapper
      .get('input[placeholder="Search models..."]')
      .setValue("alice");
    expect(wrapper.text()).toContain("team/project");

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("New Model"))
      .trigger("click");
    await flushPromises();

    expect(mocks.orgApi.getUserOrgs).toHaveBeenCalledWith("alice");

    await wrapper.get('input[placeholder="my-model"]').setValue("broken-model");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Create Model"))
      .trigger("click");
    await flushPromises();

    expect(mocks.repoApi.create).toHaveBeenCalledWith({
      type: "model",
      name: "broken-model",
      organization: null,
      private: false,
    });
    expect(mocks.router.push).not.toHaveBeenCalledWith(
      "/models/alice/broken-model",
    );
  });

  it("defaults missing organization payloads and stops invalid create submissions", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };
    mocks.orgApi.getUserOrgs.mockResolvedValue({
      data: {},
    });

    const wrapper = mountPage("model", {
      ElForm: InvalidElFormStub,
    });
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("New Model"))
      .trigger("click");
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Create Model"))
      .trigger("click");
    await flushPromises();

    expect(mocks.orgApi.getUserOrgs).toHaveBeenCalledWith("alice");
    expect(mocks.repoApi.create).not.toHaveBeenCalled();
  });
});
