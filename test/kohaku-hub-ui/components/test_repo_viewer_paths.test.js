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
    listTreeAll: vi.fn(),
    getPathsInfo: vi.fn(),
    listCommits: vi.fn(),
  },
  likesApi: {
    checkLiked: vi.fn(),
    like: vi.fn(),
    unlike: vi.fn(),
  },
}));

vi.mock("vue-router/auto", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/utils/api", () => ({
  repoAPI: mocks.repoApi,
  likesAPI: mocks.likesApi,
}));

import RepoViewer from "@/components/repo/RepoViewer.vue";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("RepoViewer path handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    vi.spyOn(console, "error").mockImplementation(() => {});

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

  function mountViewer(props) {
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

  it("loads repo-root tree entries, merges expanded path info, and links commits", async () => {
    mocks.repoApi.listTreeAll.mockResolvedValue([
      {
        type: "directory",
        path: "catalog/section-01",
        size: 0,
        lastModified: "2026-04-21T13:53:12.000000Z",
      },
    ]);
    mocks.repoApi.getPathsInfo.mockResolvedValue({
      data: [
        {
          type: "directory",
          path: "catalog/section-01",
          size: 10,
          lastCommit: {
            id: "commit-1",
            title: "Add section summary",
            date: "2026-04-21T13:53:12.000000Z",
          },
        },
      ],
    });

    const wrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    expect(mocks.repoApi.listTreeAll).toHaveBeenCalledWith(
      "dataset",
      "open-media-lab",
      "hierarchy-crawl-fixtures",
      "main",
      "/catalog",
      { recursive: false },
    );
    expect(mocks.repoApi.getPathsInfo).toHaveBeenCalledWith(
      "dataset",
      "open-media-lab",
      "hierarchy-crawl-fixtures",
      "main",
      ["catalog/section-01"],
      true,
    );

    const row = wrapper
      .findAll('[class*="cursor-pointer"]')
      .find((node) => node.text().includes("section-01"));
    expect(row).toBeTruthy();
    expect(wrapper.text()).toContain("Add section summary");

    const commitLink = wrapper
      .findAll('a[data-router-link="true"]')
      .find(
        (node) =>
          node.attributes("href") ===
          "/datasets/open-media-lab/hierarchy-crawl-fixtures/commit/commit-1",
      );
    expect(commitLink).toBeTruthy();

    await row.trigger("click");

    expect(mocks.router.push).toHaveBeenCalledWith(
      "/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog/section-01",
    );
  });

  it("sorts directories before files and orders same-type paths alphabetically", async () => {
    mocks.repoApi.listTreeAll.mockResolvedValue([
      {
        type: "file",
        path: "catalog/z-last.txt",
        size: 4,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
      {
        type: "directory",
        path: "catalog/b-section",
        size: 0,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
      {
        type: "file",
        path: "catalog/a-first.txt",
        size: 2,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
      {
        type: "directory",
        path: "catalog/a-section",
        size: 0,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
    ]);
    mocks.repoApi.getPathsInfo.mockResolvedValue({ data: [] });

    const wrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    const rowNames = wrapper
      .findAll('[class*="cursor-pointer"] .font-medium.truncate')
      .map((node) => node.text());

    expect(rowNames).toEqual([
      "a-section",
      "b-section",
      "a-first.txt",
      "z-last.txt",
    ]);
  });

  it("keeps repo-root file navigation working when expanded path info fails", async () => {
    mocks.repoApi.listTreeAll.mockResolvedValue([
      {
        type: "file",
        path: "metadata/features.json",
        size: 42,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
    ]);
    mocks.repoApi.getPathsInfo.mockRejectedValue(new Error("expand failed"));

    const wrapper = mountViewer({
      name: "table-scan-fixtures",
      currentPath: "metadata",
    });

    await flushPromises();
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

  it("skips expanded path loading for empty trees and clears the tree when loading fails", async () => {
    mocks.repoApi.listTreeAll
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error("tree failed"));

    const emptyWrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    expect(emptyWrapper.text()).toContain("No files found");
    expect(mocks.repoApi.getPathsInfo).not.toHaveBeenCalled();

    const failedWrapper = mountViewer({ currentPath: "catalog-next" });

    await flushPromises();
    await flushPromises();

    expect(failedWrapper.findAll('[class*="cursor-pointer"]')).toHaveLength(0);
    expect(mocks.repoApi.getPathsInfo).not.toHaveBeenCalled();
  });

  it("ignores stale tree responses after the current path changes", async () => {
    const firstTree = deferred();
    const secondTree = deferred();

    mocks.repoApi.listTreeAll.mockImplementation(
      (type, namespace, name, branch, path) => {
        if (path === "/catalog") {
          return firstTree.promise;
        }
        if (path === "/catalog-next") {
          return secondTree.promise;
        }
        return Promise.resolve([]);
      },
    );
    mocks.repoApi.getPathsInfo.mockResolvedValue({
      data: [{ type: "file", path: "catalog-next/new.txt", size: 1 }],
    });

    const wrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await wrapper.setProps({ currentPath: "catalog-next" });

    secondTree.resolve([
      {
        type: "file",
        path: "catalog-next/new.txt",
        size: 1,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
    ]);
    await flushPromises();
    await flushPromises();

    firstTree.resolve([
      {
        type: "file",
        path: "catalog/old.txt",
        size: 1,
        lastModified: "2026-04-21T13:53:39.000000Z",
      },
    ]);
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("new.txt");
    expect(wrapper.text()).not.toContain("old.txt");
    expect(mocks.repoApi.getPathsInfo).toHaveBeenCalledTimes(1);
    expect(mocks.repoApi.getPathsInfo).toHaveBeenCalledWith(
      "dataset",
      "open-media-lab",
      "hierarchy-crawl-fixtures",
      "main",
      ["catalog-next/new.txt"],
      true,
    );
  });

  it("ignores stale expanded path info responses after a newer request wins", async () => {
    const firstPathsInfo = deferred();

    mocks.repoApi.listTreeAll.mockImplementation(
      (type, namespace, name, branch, path) => {
        if (path === "/catalog") {
          return Promise.resolve([
            {
              type: "file",
              path: "catalog/old.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        }
        if (path === "/catalog-next") {
          return Promise.resolve([
            {
              type: "file",
              path: "catalog-next/new.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        }
        return Promise.resolve([]);
      },
    );
    mocks.repoApi.getPathsInfo.mockImplementation(
      (type, namespace, name, branch, paths) => {
        if (paths[0] === "catalog/old.txt") {
          return firstPathsInfo.promise;
        }
        return Promise.resolve({
          data: [
            {
              type: "file",
              path: "catalog-next/new.txt",
              size: 3,
              lastCommit: {
                id: "commit-2",
                title: "Ship new tree row",
                date: "2026-04-21T13:53:39.000000Z",
              },
            },
          ],
        });
      },
    );

    const wrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    await wrapper.setProps({ currentPath: "catalog-next" });
    await flushPromises();
    await flushPromises();

    firstPathsInfo.resolve({
      data: [
        {
          type: "file",
          path: "catalog/old.txt",
          size: 99,
          lastCommit: {
            id: "commit-1",
            title: "Old tree row",
            date: "2026-04-21T13:53:39.000000Z",
          },
        },
      ],
    });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("new.txt");
    expect(wrapper.text()).toContain("Ship new tree row");
    expect(wrapper.text()).not.toContain("Old tree row");
  });
});
