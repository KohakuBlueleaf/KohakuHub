import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { http, HttpResponse } from "@/testing/msw";
import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";
import repoInfo from "../fixtures/repo-info.json";
import { server } from "../setup/msw-server";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
    back: vi.fn(),
  },
  repoApi: {
    getInfo: vi.fn(),
    listTree: vi.fn(),
    listCommits: vi.fn(),
  },
  likesApi: {
    checkLiked: vi.fn(),
    like: vi.fn(),
    unlike: vi.fn(),
  },
  axios: {
    get: vi.fn(),
  },
}));

vi.mock("vue-router/auto", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/utils/api", () => ({
  repoAPI: mocks.repoApi,
  likesAPI: mocks.likesApi,
}));

vi.mock("axios", () => ({
  get: mocks.axios.get,
  default: {
    get: mocks.axios.get,
  },
}));

import RepoViewer from "@/components/repo/RepoViewer.vue";

describe("RepoViewer path handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());

    mocks.repoApi.getInfo.mockResolvedValue({
      data: {
        ...repoInfo,
        id: "open-media-lab/hierarchy-crawl-fixtures",
      },
    });
    mocks.repoApi.listCommits.mockResolvedValue({
      data: {
        commits: [],
        hasMore: false,
        nextCursor: null,
      },
    });
    server.use(
      http.get("/api/users/open-media-lab/type", () =>
        HttpResponse.json({ type: "org" }),
      ),
    );
  });

  function mountViewer(props, treeEntries) {
    mocks.repoApi.listTree.mockResolvedValue({
      data: treeEntries,
    });

    return mount(RepoViewer, {
      props: {
        repoType: "dataset",
        namespace: "open-media-lab",
        name: "hierarchy-crawl-fixtures",
        branch: "main",
        currentPath: "",
        tab: "files",
        ...props,
      },
      global: {
        stubs: {
          ...ElementPlusStubs,
          RouterLink: RouterLinkStub,
          MarkdownViewer: true,
          MetadataHeader: true,
          DetailedMetadataPanel: true,
          ReferencedDatasetsCard: true,
          SidebarRelationshipsCard: true,
          DatasetViewerTab: true,
        },
      },
    });
  }

  it("does not duplicate directory paths when the tree API returns repo-root paths", async () => {
    const wrapper = mountViewer(
      {
        currentPath: "catalog",
      },
      [
        {
          type: "directory",
          path: "catalog/section-01",
          size: 10,
          lastModified: "2026-04-21T13:53:12.000000Z",
        },
      ],
    );

    await flushPromises();

    const row = wrapper
      .findAll('[class*="cursor-pointer"]')
      .find((node) => node.text().includes("section-01"));

    expect(row).toBeTruthy();
    await row.trigger("click");

    expect(mocks.router.push).toHaveBeenCalledWith(
      "/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog/section-01",
    );
  });

  it("does not duplicate file paths when the tree API returns repo-root paths", async () => {
    const wrapper = mountViewer(
      {
        name: "table-scan-fixtures",
        currentPath: "metadata",
      },
      [
        {
          type: "file",
          path: "metadata/features.json",
          size: 42,
          lastModified: "2026-04-21T13:53:39.000000Z",
        },
      ],
    );

    await flushPromises();

    const row = wrapper
      .findAll('[class*="cursor-pointer"]')
      .find((node) => node.text().includes("features.json"));

    expect(row).toBeTruthy();
    await row.trigger("click");

    expect(mocks.router.push).toHaveBeenCalledWith(
      "/datasets/open-media-lab/table-scan-fixtures/blob/main/metadata/features.json",
    );
  });
});
