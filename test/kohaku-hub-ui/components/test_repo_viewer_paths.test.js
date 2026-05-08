import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { http, HttpResponse } from "@/testing/msw";
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
        // The file list now requests one page at a time. The page-size
        // pref defaults to 50 (utils/repo-list-pagination.js); paginated
        // listing reduces the wire volume on large directories without
        // changing how the SPA reads back individual entries.
        params: { recursive: "false", limit: "50" },
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

    // Directory rows render as a stretched <RouterLink> overlay so the
    // browser treats them as real anchors (right-click / middle-click /
    // Cmd-click all work). Assert the overlay href matches the tree
    // route for the directory.
    const rowLink = row.find('[data-testid="filelist-row-link"]');
    expect(rowLink.exists()).toBe(true);
    expect(rowLink.attributes("href")).toBe(
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

    // File rows point the stretched RouterLink at the blob route.
    const rowLink = row.find('[data-testid="filelist-row-link"]');
    expect(rowLink.exists()).toBe(true);
    expect(rowLink.attributes("href")).toBe(
      "/datasets/open-media-lab/table-scan-fixtures/blob/main/metadata/features.json",
    );
  });

  it("renders every file-list row as a real <a> anchor so right-click → Open in New Tab works", async () => {
    // Mix of nested subdirectories + files with and without lastCommit
    // to exercise the directory-route vs blob-route branch and confirm
    // the RouterLink overlay is present on every row regardless of
    // entry type or metadata shape.
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        () =>
          jsonResponse([
            {
              type: "directory",
              path: "catalog/sub-dir",
              size: 0,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
            {
              type: "file",
              path: "catalog/weights.safetensors",
              size: 1024,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
            {
              type: "file",
              path: "catalog/notes.md",
              size: 64,
              lastModified: "2026-04-21T13:53:39.000000Z",
              lastCommit: {
                id: "abc123",
                title: "Add notes",
                date: "2026-04-21T13:53:39.000000Z",
              },
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

    const rowLinks = wrapper.findAll('[data-testid="filelist-row-link"]');
    const hrefs = rowLinks.map((n) => n.attributes("href")).sort();
    expect(hrefs).toEqual(
      [
        "/datasets/open-media-lab/hierarchy-crawl-fixtures/blob/main/catalog/notes.md",
        "/datasets/open-media-lab/hierarchy-crawl-fixtures/blob/main/catalog/weights.safetensors",
        "/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog/sub-dir",
      ].sort(),
    );

    // Every overlay is an <a> element — that is what makes the native
    // browser context menu offer "Open link in new tab".
    for (const link of rowLinks) {
      expect(link.element.tagName).toBe("A");
      expect(link.attributes("aria-label")).toMatch(/^Open /);
    }

    // The preview button and commit-row RouterLink must NOT trigger a
    // navigation of the stretched row link — their clicks are
    // swallowed by @click.stop and their z-index keeps them on top.
    const previewBtn = wrapper
      .findAll("button")
      .find((b) => (b.attributes("aria-label") || "").startsWith("Preview"));
    if (previewBtn) {
      await previewBtn.trigger("click");
      // click on the preview button should NOT have navigated via router.push
      expect(mocks.router.push).not.toHaveBeenCalledWith(
        expect.stringMatching(/\/blob\/|\/tree\//),
      );
    }
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

  it("renders an ErrorState panel when the root tree fetch fails (gated fallback)", async () => {
    // Backend replies with the shape build_aggregate_failure_response
    // emits on an all-auth aggregate: 401 + X-Error-Code=GatedRepo +
    // sources[] in the JSON body. RepoViewer should classify via the
    // axios interceptor and render ErrorState where the file list
    // would otherwise sit — NOT silently show an empty grid.
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        () =>
          jsonResponse(
            {
              error: "GatedRepo",
              detail:
                "Upstream source requires authentication - likely a gated repository.",
              sources: [
                {
                  name: "HuggingFace",
                  url: "https://huggingface.co",
                  status: 401,
                  category: "auth",
                  message: "Access restricted",
                },
              ],
            },
            {
              status: 401,
              headers: {
                "X-Error-Code": "GatedRepo",
                "X-Error-Message":
                  "Upstream source requires authentication - likely a gated repository.",
              },
            },
          ),
      ),
    );

    const wrapper = mountViewer();
    await flushPromises();
    await flushPromises();

    // Shared ErrorState's default copy for `gated`.
    expect(wrapper.text()).toContain("Authentication required");
    // Diagnostic disclosure from the sources[] body. The per-row
    // cell content lives inside the ElTable stub's scoped-slot
    // rendering, which isn't exercised at this mount's stubs; the
    // count is enough to prove we plumbed sources through.
    expect(wrapper.text()).toContain("Fallback sources tried (1)");
    // The misleading "No files" / empty-tree copy must NOT be shown
    // once we have a classified error.
    expect(wrapper.text()).not.toContain("No files");

    wrapper.unmount();
  });

  it("initial fetch defaults to limit=50 (no cursor) and renders the Load More footer when the response carries a Link rel=next cursor", async () => {
    // Acceptance criterion from issue #56: the first /tree request
    // never carries a cursor — random-page jumps were dropped along
    // with the numbered pager.
    const observed = [];
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        ({ request }) => {
          const url = new URL(request.url);
          observed.push({
            limit: url.searchParams.get("limit"),
            cursor: url.searchParams.get("cursor"),
          });
          return jsonResponse(
            [
              {
                type: "file",
                path: "catalog/a-first.txt",
                size: 1,
                lastModified: "2026-04-21T13:53:39.000000Z",
              },
            ],
            {
              headers: {
                Link: '<https://hub.test/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog?recursive=false&limit=50&cursor=cursor-2>; rel="next"',
              },
            },
          );
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

    const wrapper = mountViewer({ currentPath: "catalog" });
    await flushPromises();
    await flushPromises();

    // Single round trip on mount — no background walk follows.
    expect(observed).toHaveLength(1);
    expect(observed[0]).toEqual({ limit: "50", cursor: null });
    expect(wrapper.text()).toContain("a-first.txt");
    // Load More button surfaces because the response carried a
    // `Link: rel="next"` cursor.
    expect(wrapper.find('[data-testid="file-list-load-more"]').exists()).toBe(
      true,
    );
    // Count copy carries the "loaded" suffix while more is available.
    expect(wrapper.find('[data-testid="file-list-count"]').text()).toContain(
      "loaded",
    );

    wrapper.unmount();
  });

  it("Load More appends the next cursor's batch — entries union, dropped pager state, hides footer when the listing is exhausted", async () => {
    // Two-batch listing. The first response carries a cursor; the
    // second is the tail (no Link). The Load More click must pass
    // that cursor as `cursor=...` and append the new entries to the
    // already-rendered ones (HuggingFace-style append-on-click).
    const observed = [];
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        ({ request }) => {
          const url = new URL(request.url);
          const cursor = url.searchParams.get("cursor");
          observed.push(cursor);
          if (!cursor) {
            return jsonResponse(
              [
                {
                  type: "file",
                  path: "catalog/a-first.txt",
                  size: 1,
                  lastModified: "2026-04-21T13:53:39.000000Z",
                },
              ],
              {
                headers: {
                  Link: '<https://hub.test/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog?recursive=false&limit=50&cursor=cursor-2>; rel="next"',
                },
              },
            );
          }
          expect(cursor).toBe("cursor-2");
          // Tail batch — no Link, so nextCursor flips to null and the
          // footer should disappear after this response is processed.
          return jsonResponse([
            {
              type: "file",
              path: "catalog/z-last.txt",
              size: 2,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

    const wrapper = mountViewer({ currentPath: "catalog" });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("a-first.txt");
    expect(wrapper.text()).not.toContain("z-last.txt");

    const loadMore = wrapper.find('[data-testid="file-list-load-more"]');
    expect(loadMore.exists()).toBe(true);
    await loadMore.trigger("click");
    await flushPromises();
    await flushPromises();

    // Two requests total: the initial cursor-less load, then the
    // cursor=cursor-2 click. No background walk in between.
    expect(observed).toEqual([null, "cursor-2"]);
    // Both entries are rendered — Load More appended, did not replace.
    expect(wrapper.text()).toContain("a-first.txt");
    expect(wrapper.text()).toContain("z-last.txt");
    // Tail batch arrived → Load More button removed (nothing to fetch).
    expect(wrapper.find('[data-testid="file-list-load-more"]').exists()).toBe(
      false,
    );

    wrapper.unmount();
  });

  it("every Load More click sends limit=50 — the per-batch selector was removed in favor of a single fixed batch", async () => {
    // The earlier numbered-pager UI had a 50/100/200 selector. With
    // Load More the affordance "click to extend" carries the entire
    // knob, so the selector was dropped (it visually collided with
    // the surrounding header chrome). Pin the constant batch size on
    // the wire so a future regression doesn't silently re-introduce
    // a configurable size without the UI to drive it.
    const observed = [];
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        ({ request }) => {
          const url = new URL(request.url);
          observed.push({
            limit: url.searchParams.get("limit"),
            cursor: url.searchParams.get("cursor"),
          });
          if (!url.searchParams.get("cursor")) {
            return jsonResponse(
              [
                {
                  type: "file",
                  path: "catalog/a.txt",
                  size: 1,
                  lastModified: "2026-04-21T13:53:39.000000Z",
                },
              ],
              {
                headers: {
                  Link: '<https://hub.test/api?cursor=cursor-2>; rel="next"',
                },
              },
            );
          }
          return jsonResponse([
            {
              type: "file",
              path: "catalog/m.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

    const wrapper = mountViewer({ currentPath: "catalog" });
    await flushPromises();
    await flushPromises();

    await wrapper.find('[data-testid="file-list-load-more"]').trigger("click");
    await flushPromises();
    await flushPromises();

    expect(observed).toEqual([
      { limit: "50", cursor: null },
      { limit: "50", cursor: "cursor-2" },
    ]);
    // No batch-size widget renders in either state.
    expect(wrapper.find('[data-testid="file-list-page-size"]').exists()).toBe(
      false,
    );

    wrapper.unmount();
  });

  it("Load More failure leaves the existing listing intact — the footer stays so the user can retry on the next click", async () => {
    // The Load-More flow deliberately does NOT route an append
    // failure through `treeErrorClassification` (that's a
    // listing-wide failure indicator). Already-rendered rows must
    // stay; the button must come back out of its loading state so
    // the user can try again.
    let secondCallShouldFail = false;
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main/catalog",
        ({ request }) => {
          const url = new URL(request.url);
          const cursor = url.searchParams.get("cursor");
          if (!cursor) {
            return jsonResponse(
              [
                {
                  type: "file",
                  path: "catalog/a.txt",
                  size: 1,
                  lastModified: "2026-04-21T13:53:39.000000Z",
                },
              ],
              {
                headers: {
                  Link: '<https://hub.test/api?cursor=cursor-2>; rel="next"',
                },
              },
            );
          }
          if (secondCallShouldFail) {
            return jsonResponse(
              { detail: "transient" },
              { status: 503 },
            );
          }
          return jsonResponse([
            {
              type: "file",
              path: "catalog/m.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

    const wrapper = mountViewer({ currentPath: "catalog" });
    await flushPromises();
    await flushPromises();

    secondCallShouldFail = true;
    await wrapper.find('[data-testid="file-list-load-more"]').trigger("click");
    await flushPromises();
    await flushPromises();

    // Initial row stays rendered; no error panel takes over.
    expect(wrapper.text()).toContain("a.txt");
    expect(wrapper.text()).not.toContain("Authentication required");
    // Footer + Load More button stay so retry is possible.
    expect(wrapper.find('[data-testid="file-list-load-more"]').exists()).toBe(
      true,
    );

    // Retry succeeds — appended row joins the listing.
    secondCallShouldFail = false;
    await wrapper.find('[data-testid="file-list-load-more"]').trigger("click");
    await flushPromises();
    await flushPromises();
    expect(wrapper.text()).toContain("a.txt");
    expect(wrapper.text()).toContain("m.txt");

    wrapper.unmount();
  });

  it("name_prefix filter is server-side: the search box drives a new /tree request and renders the prefix-not-found copy when LakeFS returns []", async () => {
    // Issue #54 — the in-memory `filteredFiles` post-filter was
    // replaced by a server-side `name_prefix` query param so paginated
    // listings can actually be searched. The empty-state copy must
    // reflect the prefix semantics ("starts with", case-sensitive)
    // rather than the previous in-memory contains-match wording.
    const treeRequests = [];
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        ({ request }) => {
          const url = new URL(request.url);
          const params = Object.fromEntries(url.searchParams.entries());
          treeRequests.push(params);
          if (params.name_prefix === "zeta") {
            return jsonResponse([]);
          }
          return jsonResponse([
            {
              type: "file",
              path: "alpha.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
            {
              type: "file",
              path: "beta.txt",
              size: 1,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]);
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const wrapper = mountViewer();
    await flushPromises();
    await flushPromises();

    wrapper.vm.fileSearchQuery = "zeta";
    // The watcher debounces 300ms before firing the reload. Advance
    // the timer so the new request goes out, then drain pending
    // microtasks (paths-info chains off the same request id).
    await vi.advanceTimersByTimeAsync(350);
    await flushPromises();
    await flushPromises();
    vi.useRealTimers();

    // The new request must carry the prefix verbatim — case-sensitive
    // wire form, no client-side lowercasing.
    const prefixedReq = treeRequests.find((r) => r.name_prefix === "zeta");
    expect(prefixedReq).toBeTruthy();
    // Cursor must be absent — typing a new prefix resets the cursor
    // stack so previously-discovered cursors (which addressed the
    // unfiltered slice) cannot leak into the filtered listing.
    expect(prefixedReq.cursor).toBeUndefined();
    expect(wrapper.text()).toContain(
      'No files in this directory start with "zeta"',
    );

    wrapper.unmount();
  });

  it("typing into the search box resets the listing — already-loaded entries are dropped and the filtered request starts cursor-less", async () => {
    // Issue #54 invariant carried into the Load-More world: a
    // name_prefix change must drop the already-rendered batch (the
    // first batch was minted against the unfiltered listing) and the
    // subsequent request must NOT carry the cached `nextCursor`,
    // which addressed the unfiltered slice.
    const treeRequests = [];
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        ({ request }) => {
          const url = new URL(request.url);
          const params = Object.fromEntries(url.searchParams.entries());
          treeRequests.push(params);
          if (params.name_prefix) {
            return jsonResponse([]);
          }
          return jsonResponse(
            [
              {
                type: "file",
                path: "alpha.txt",
                size: 1,
                lastModified: "2026-04-21T13:53:39.000000Z",
              },
            ],
            {
              headers: {
                Link: '<https://hub.local/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main?cursor=cursor-page-2&limit=50>; rel="next"',
              },
            },
          );
        },
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
    );

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const wrapper = mountViewer();
    await flushPromises();
    await flushPromises();

    // After the initial unfiltered fetch the SPA holds `cursor-page-2`
    // as nextCursor (Load More target) and one rendered row.
    expect(wrapper.vm.fileListNextCursor).toBe("cursor-page-2");
    expect(wrapper.text()).toContain("alpha.txt");

    wrapper.vm.fileSearchQuery = "alp";
    await vi.advanceTimersByTimeAsync(350);
    await flushPromises();
    await flushPromises();
    vi.useRealTimers();

    // Filtered request fired and DID NOT reuse the cached cursor —
    // typing a new prefix is a full listing reset.
    const filteredRequests = treeRequests.filter(
      (r) => r.name_prefix === "alp",
    );
    expect(filteredRequests.length).toBeGreaterThan(0);
    for (const req of filteredRequests) {
      expect(req.cursor).toBeUndefined();
    }
    // Listing reset: the previously-rendered row is gone (filter
    // returned []), and `nextCursor` flipped back to null because
    // the filtered response had no Link header.
    expect(wrapper.vm.fileTree).toEqual([]);
    expect(wrapper.vm.fileListNextCursor).toBe(null);

    wrapper.unmount();
  });

  it("Card tab finds the README via paths-info when it is not on the current page", async () => {
    // The repo's root listing intentionally omits README.md (simulating
    // a paginated tree where README falls off this page); paths-info
    // must surface it and the Card tab still renders the markdown.
    let pathsInfoCalls = 0;
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        () =>
          jsonResponse([
            {
              type: "file",
              path: "alpha.json",
              size: 5,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]),
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        async ({ request }) => {
          pathsInfoCalls += 1;
          const form = new URLSearchParams(await request.clone().text());
          const paths = form.getAll("paths");
          if (paths.includes("README.md")) {
            return jsonResponse([
              {
                type: "file",
                path: "README.md",
                size: 64,
                lastCommit: {
                  id: "c1",
                  title: "Add readme",
                  date: "2026-04-21T13:53:39.000000Z",
                },
              },
            ]);
          }
          return jsonResponse([]);
        },
      ),
      http.get(
        "/datasets/open-media-lab/hierarchy-crawl-fixtures/resolve/main/README.md",
        () =>
          new HttpResponse(
            "---\ntitle: Recovered\n---\n\n# Off-Page README\n",
            {
              status: 200,
              headers: { "Content-Type": "text/markdown" },
            },
          ),
      ),
    );

    const wrapper = mountViewer({ tab: "card" });
    await flushPromises();
    await flushPromises();
    await flushPromises();

    expect(pathsInfoCalls).toBeGreaterThan(0);
    // MarkdownViewer is stubbed in this test mount, so the rendered
    // markdown body is not in wrapper.text(). We assert the recovery
    // path negatively: the empty placeholder is gone, which only
    // happens when readmeContent was populated by the probe + fetch
    // chain. (The structurally-identical test in
    // FilePreviewDialog.spec covers the actual markdown render.)
    expect(wrapper.text()).not.toContain("No README.md found");

    wrapper.unmount();
  });

  it("README paths-info probe failure degrades gracefully to the empty-readme state", async () => {
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        () => jsonResponse([]),
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () =>
          jsonResponse(
            { detail: "paths-info upstream offline" },
            { status: 502 },
          ),
      ),
    );

    const wrapper = mountViewer({ tab: "card" });
    await flushPromises();
    await flushPromises();
    await flushPromises();

    // Soft-fallback: the probe failure does not throw; the Card tab
    // ends up with no README and renders the regular empty copy.
    expect(wrapper.text()).not.toContain("Authentication required");
    expect(wrapper.text()).not.toContain("undefined");

    wrapper.unmount();
  });

  it("indexed-tar sibling icon: lights up when the .json sibling exists on a different page and stays dark when HEAD says it does not", async () => {
    const headProbes = [];
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        () =>
          jsonResponse([
            {
              type: "file",
              path: "with-sibling.tar",
              size: 100,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
            {
              type: "file",
              path: "no-sibling.tar",
              size: 100,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]),
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
      // HEAD probes mimic the backend: 200 for the existing sidecar,
      // 404 for the bare tar's missing sidecar.
      http.head(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/resolve/main/with-sibling.json",
        () => {
          headProbes.push("with-sibling.json");
          return new HttpResponse(null, { status: 200 });
        },
      ),
      http.head(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/resolve/main/no-sibling.json",
        () => {
          headProbes.push("no-sibling.json");
          return new HttpResponse(null, { status: 404 });
        },
      ),
    );

    const wrapper = mountViewer({ currentPath: "" });
    await flushPromises();
    await flushPromises();
    await flushPromises();

    // Both probes should have fired exactly once (one per .tar row
    // whose sibling is not in the loaded page).
    expect(headProbes.sort()).toEqual([
      "no-sibling.json",
      "with-sibling.json",
    ]);

    // The confirmed tar gets the indexed-tar icon (Carbon's archive),
    // the unconfirmed one stays bare.
    const previewButtons = wrapper.findAll(
      "button[aria-label^='Preview metadata for'], button[aria-label^='Preview metadata for ']",
    );
    // ElButton stubs render as <button>; iterate all of them and
    // collect the ones the icon predicate says are previewable.
    const allButtons = wrapper.findAll("button");
    const tarPreviewButtons = allButtons.filter((b) =>
      (b.attributes("aria-label") || "").startsWith("Preview metadata for"),
    );
    const titles = tarPreviewButtons.map((b) => b.attributes("title") || "");
    // One previewable .tar row only — the confirmed one.
    expect(tarPreviewButtons).toHaveLength(1);
    expect(titles[0]).toContain("Browse indexed tar contents");
    // unused alias kept to silence the linter when the future patch
    // adds a second selector — drop on the next touch.
    void previewButtons;

    wrapper.unmount();
  });

  it("indexed-tar sibling icon short-circuits when the .json is in the loaded page (no HEAD probe issued)", async () => {
    const headSpy = vi.fn(() => new HttpResponse(null, { status: 500 }));
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        () =>
          jsonResponse([
            {
              type: "file",
              path: "bundle.tar",
              size: 100,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
            {
              type: "file",
              path: "bundle.json",
              size: 50,
              lastModified: "2026-04-21T13:53:39.000000Z",
            },
          ]),
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
      http.head(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/resolve/main/bundle.json",
        headSpy,
      ),
    );

    const wrapper = mountViewer({ currentPath: "" });
    await flushPromises();
    await flushPromises();
    await flushPromises();

    // The icon should already be lit from the loaded-listing fast
    // path — and the probe must NOT fire, otherwise the cheap path is
    // doing wasted work on every paginated render.
    expect(headSpy).not.toHaveBeenCalled();
    const previewBtn = wrapper
      .findAll("button")
      .find((b) =>
        (b.attributes("aria-label") || "").startsWith("Preview metadata for bundle.tar"),
      );
    expect(previewBtn).toBeTruthy();
    expect(previewBtn.attributes("title")).toContain(
      "Browse indexed tar contents",
    );

    wrapper.unmount();
  });

  it("renders an ErrorState for a README that fails to resolve, not the 'No README.md found' placeholder", async () => {
    // Tree returns one entry — a README — so loadReadme() will try to
    // fetch it. That fetch returns a classified 401/GatedRepo body;
    // RepoViewer should show the ErrorState in the README slot
    // instead of the misleading "No README.md found" empty copy.
    server.use(
      http.get(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/tree/main",
        () =>
          jsonResponse([
            {
              type: "file",
              path: "README.md",
              size: 120,
              lastCommit: {
                id: "c1",
                title: "Add readme",
                date: "2026-04-21T13:53:39.000000Z",
              },
            },
          ]),
      ),
      http.post(
        "/api/datasets/open-media-lab/hierarchy-crawl-fixtures/paths-info/main",
        () => jsonResponse([]),
      ),
      // The README resolve itself fails with the aggregate-gated body.
      http.get(
        "/datasets/open-media-lab/hierarchy-crawl-fixtures/resolve/main/README.md",
        () =>
          jsonResponse(
            {
              error: "GatedRepo",
              detail:
                "Upstream source requires authentication - likely a gated repository.",
              sources: [
                {
                  name: "HF",
                  url: "https://hf",
                  status: 401,
                  category: "auth",
                  message: "Access restricted",
                },
              ],
            },
            {
              status: 401,
              headers: {
                "X-Error-Code": "GatedRepo",
                "X-Error-Message":
                  "Upstream source requires authentication - likely a gated repository.",
              },
            },
          ),
      ),
    );

    const wrapper = mountViewer({ tab: "card" });
    await flushPromises();
    await flushPromises();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Authentication required");
    expect(text).not.toContain("No README.md found");

    wrapper.unmount();
  });
});
