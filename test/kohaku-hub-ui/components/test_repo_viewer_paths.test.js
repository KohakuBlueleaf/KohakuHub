import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { http } from "@/testing/msw";
import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";
import {
  cloneFixture,
  jsonResponse,
  uiApiFixtures,
} from "../helpers/api-fixtures";
import { server } from "../setup/msw-server";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
    back: vi.fn(),
  },
}));

vi.mock("vue-router/auto", () => ({
  useRouter: () => mocks.router,
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
  const requests = {
    pathsInfo: [],
    tree: [],
  };

  function repoInfoFor(name) {
    return {
      ...cloneFixture(uiApiFixtures.repo.info),
      id: `open-media-lab/${name}`,
    };
  }

  function installBaseHandlers() {
    requests.pathsInfo.length = 0;
    requests.tree.length = 0;

    server.use(
      http.get("/api/users/open-media-lab/type", () =>
        jsonResponse({ type: "org" }),
      ),
      http.get("/api/datasets/open-media-lab/:name", ({ params }) =>
        jsonResponse(repoInfoFor(params.name)),
      ),
      http.get("/api/datasets/open-media-lab/:name/commits/main", () =>
        jsonResponse(cloneFixture(uiApiFixtures.repo.commitsHf.page1)),
      ),
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    vi.spyOn(console, "error").mockImplementation(() => {});
    installBaseHandlers();
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

  it("loads repo-root tree entries through the API client, merges expanded path info, and links commits", async () => {
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        ({ request }) => {
          const url = new URL(request.url);
          requests.tree.push({
            name: "hierarchy-crawl-fixtures",
            path: "/catalog",
            params: Object.fromEntries(url.searchParams.entries()),
          });
          return jsonResponse(cloneFixture(uiApiFixtures.repo.tree));
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        async ({ request }) => {
          const form = new URLSearchParams(await request.clone().text());
          requests.pathsInfo.push({
            name: "hierarchy-crawl-fixtures",
            paths: form.getAll("paths"),
            expand: form.get("expand"),
          });
          return jsonResponse(cloneFixture(uiApiFixtures.repo.pathsInfo));
        },
      ),
    );

    const wrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    expect(requests.tree).toEqual([
      {
        name: "hierarchy-crawl-fixtures",
        path: "/catalog",
        params: { recursive: "false" },
      },
    ]);
    expect(requests.pathsInfo).toEqual([
      {
        name: "hierarchy-crawl-fixtures",
        paths: ["catalog/section-01"],
        expand: "true",
      },
    ]);

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
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        () =>
          jsonResponse([
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
          ]),
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

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
    server.use(
      http.get(
        "/api/datasets/open-media-lab/table-scan-fixtures/tree/main/metadata",
        () =>
          jsonResponse([
            {
              type: "file",
              path: "metadata/features.json",
              size: 42,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]),
      ),
      http.post(
        "/api/datasets/open-media-lab/table-scan-fixtures/paths-info/main",
        () => jsonResponse({ detail: "expand failed" }, { status: 500 }),
      ),
    );

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
    let requestCount = 0;
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/:path",
        ({ params }) => {
        requestCount += 1;
        if (requestCount === 1) {
          return jsonResponse([]);
        }
        return jsonResponse({ detail: "tree failed" }, { status: 500 });
        },
      ),
    );

    const emptyWrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    expect(emptyWrapper.text()).toContain("No files found");
    expect(requests.pathsInfo).toEqual([]);

    const failedWrapper = mountViewer({ currentPath: "catalog-next" });

    await flushPromises();
    await flushPromises();

    expect(failedWrapper.findAll('[class*="cursor-pointer"]')).toHaveLength(0);
    expect(requests.pathsInfo).toEqual([]);
  });

  it("ignores stale tree responses after the current path changes", async () => {
    const firstTree = deferred();
    const secondTree = deferred();

    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/:path",
        async ({ params }) => {
        if (params.path === "catalog") {
          const payload = await firstTree.promise;
          return jsonResponse(payload);
        }
        const payload = await secondTree.promise;
        return jsonResponse(payload);
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        async ({ request }) => {
          const form = new URLSearchParams(await request.clone().text());
          requests.pathsInfo.push({
            name: "hierarchy-crawl-fixtures",
            paths: form.getAll("paths"),
            expand: form.get("expand"),
          });
          return jsonResponse([
            { type: "file", path: "catalog-next/new.txt", size: 1 },
          ]);
        },
      ),
    );

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
    expect(requests.pathsInfo).toEqual([
      {
        name: "hierarchy-crawl-fixtures",
        paths: ["catalog-next/new.txt"],
        expand: "true",
      },
    ]);
  });

  it("ignores stale expanded path info responses after a newer request wins", async () => {
    const firstPathsInfo = deferred();

    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/:path",
        ({ params }) => {
        if (params.path === "catalog") {
          return jsonResponse([
            {
              type: "file",
              path: "catalog/old.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        }
        return jsonResponse([
          {
            type: "file",
            path: "catalog-next/new.txt",
            size: 1,
            lastModified: "2026-04-21T13:53:39.000000Z",
          },
        ]);
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        async ({ request }) => {
          const form = new URLSearchParams(await request.clone().text());
          const paths = form.getAll("paths");
          if (paths[0] === "catalog/old.txt") {
            const payload = await firstPathsInfo.promise;
            return jsonResponse(payload);
          }
          return jsonResponse([
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
          ]);
        },
      ),
    );

    const wrapper = mountViewer({ currentPath: "catalog" });

    await flushPromises();
    await flushPromises();

    await wrapper.setProps({ currentPath: "catalog-next" });
    await flushPromises();
    await flushPromises();

    firstPathsInfo.resolve([
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
    ]);
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("new.txt");
    expect(wrapper.text()).toContain("Ship new tree row");
    expect(wrapper.text()).not.toContain("Old tree row");
  });
});
