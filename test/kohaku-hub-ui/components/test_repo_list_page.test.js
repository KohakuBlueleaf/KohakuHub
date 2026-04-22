import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { http } from "@/testing/msw";
import {
  ElementPlusStubs,
  InvalidElFormStub,
  RouterLinkStub,
} from "../helpers/vue";
import {
  cloneFixture,
  jsonResponse,
  readJsonBody,
  uiApiFixtures,
} from "../helpers/api-fixtures";
import { server } from "../setup/msw-server";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
  },
  route: {
    params: {},
    query: {},
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
  const requests = {
    create: [],
    listRepos: [],
    userOrgs: [],
  };

  function defaultModelRepos() {
    return [
      cloneFixture(uiApiFixtures.repo.info),
      {
        ...cloneFixture(uiApiFixtures.repo.info),
        id: "alice/other-model",
        author: "alice",
      },
    ];
  }

  function installHandlers({
    modelRepos = defaultModelRepos(),
    datasetRepos = cloneFixture(uiApiFixtures.userOverview.datasets),
    spaceRepos = cloneFixture(uiApiFixtures.userOverview.spaces),
    createStatus = 200,
    createResponse = cloneFixture(uiApiFixtures.repo.create),
    userOrgsStatus = 200,
    userOrgsResponse = cloneFixture(uiApiFixtures.organizations.userOrgs),
  } = {}) {
    requests.create.length = 0;
    requests.listRepos.length = 0;
    requests.userOrgs.length = 0;

    server.use(
      http.get("/api/models", ({ request }) => {
        const url = new URL(request.url);
        requests.listRepos.push({
          type: "model",
          params: Object.fromEntries(url.searchParams.entries()),
        });
        return jsonResponse(modelRepos);
      }),
      http.get("/api/datasets", ({ request }) => {
        const url = new URL(request.url);
        requests.listRepos.push({
          type: "dataset",
          params: Object.fromEntries(url.searchParams.entries()),
        });
        return jsonResponse(datasetRepos);
      }),
      http.get("/api/spaces", ({ request }) => {
        const url = new URL(request.url);
        requests.listRepos.push({
          type: "space",
          params: Object.fromEntries(url.searchParams.entries()),
        });
        return jsonResponse(spaceRepos);
      }),
      http.get("/org/users/:username/orgs", ({ request, params }) => {
        const url = new URL(request.url);
        requests.userOrgs.push({
          username: params.username,
          params: Object.fromEntries(url.searchParams.entries()),
        });
        return jsonResponse(userOrgsResponse, { status: userOrgsStatus });
      }),
      http.post("/api/repos/create", async ({ request }) => {
        requests.create.push(await readJsonBody(request));
        return jsonResponse(createResponse, { status: createStatus });
      }),
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    mocks.repoSortPreference.getRepoSortPreference.mockReturnValue("likes");
    installHandlers();
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

  it("loads repos through the API client, filters them, persists sort preference, and creates a new repo", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };

    const wrapper = mountPage();
    await flushPromises();

    expect(requests.listRepos).toEqual([
      {
        type: "model",
        params: {
          limit: "100",
          sort: "likes",
          fallback: "false",
        },
      },
    ]);
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
    expect(requests.listRepos.at(-1)).toEqual({
      type: "model",
      params: {
        limit: "100",
        sort: "recent",
        fallback: "false",
      },
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

    expect(requests.userOrgs).toEqual([
      {
        username: "alice",
        params: {},
      },
    ]);
    expect(requests.create).toEqual([
      {
        type: "model",
        name: "fresh-model",
        organization: "acme",
        private: true,
      },
    ]);
    expect(mocks.router.push).toHaveBeenCalledWith("/models/acme/fresh-model");
  });

  it("handles list loading failures and hides creation controls for visitors", async () => {
    installHandlers({ modelRepos: { detail: "boom" }, createStatus: 200 });
    server.use(
      http.get("/api/models", () => jsonResponse({ detail: "boom" }, { status: 500 })),
    );

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).not.toContain("New Model");
  });

  it("falls back to the current user when the backend omits repo_id", async () => {
    installHandlers({
      createResponse: cloneFixture(uiApiFixtures.repo.createWithoutId),
    });

    const authStore = useAuthStore();
    authStore.user = { username: "mai_lin" };
    authStore.userOrganizations = [];

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

    expect(requests.create).toEqual([
      {
        type: "model",
        name: "fresh-model",
        organization: null,
        private: false,
      },
    ]);
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

    expect(requests.listRepos).toEqual([
      {
        type: "space",
        params: {
          limit: "100",
          sort: "likes",
          fallback: "false",
        },
      },
    ]);
    expect(wrapper.text()).toContain("Spaces");
    expect(wrapper.text()).toContain("Discover ML demos and applications");
    expect(wrapper.text()).toContain("New Space");
  });

  it("filters by author and reports organization or creation failures", async () => {
    installHandlers({
      modelRepos: [
        {
          ...cloneFixture(uiApiFixtures.repo.info),
          id: "team/project",
          author: "alice",
        },
      ],
      userOrgsStatus: 500,
      userOrgsResponse: {},
      createStatus: 500,
      createResponse: { detail: "boom" },
    });

    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };

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

    expect(requests.userOrgs).toEqual([
      {
        username: "alice",
        params: {},
      },
    ]);

    await wrapper.get('input[placeholder="my-model"]').setValue("broken-model");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Create Model"))
      .trigger("click");
    await flushPromises();

    expect(requests.create).toEqual([
      {
        type: "model",
        name: "broken-model",
        organization: null,
        private: false,
      },
    ]);
    expect(mocks.router.push).not.toHaveBeenCalledWith(
      "/models/alice/broken-model",
    );
  });

  it("defaults missing organization payloads and stops invalid create submissions", async () => {
    installHandlers({
      userOrgsResponse: {},
    });

    const authStore = useAuthStore();
    authStore.user = {
      username: "alice",
    };

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

    expect(requests.userOrgs).toEqual([
      {
        username: "alice",
        params: {},
      },
    ]);
    expect(requests.create).toEqual([]);
  });
});
