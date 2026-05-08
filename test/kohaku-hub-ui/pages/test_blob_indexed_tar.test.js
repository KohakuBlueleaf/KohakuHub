// Component test for the standalone blob page's indexed-tar
// detection.
//
// Two contracts being pinned:
//   1. repoType is derived from the FIRST URL segment, not from
//      "/models/" / "/datasets/" / "/spaces/" appearing anywhere in
//      the path. The earlier substring check mis-classified dataset
//      members at paths like `archives/models/bundle.tar` as
//      `model` repos and routed the resolve URL to `/api/models/...`
//      which 404'd.
//   2. When a `.tar` blob has a sibling `<basename>.json` in the same
//      folder, the page renders <TarBrowserPanel> inline in place of
//      the binary fallback. Bare tars stay on the existing fallback.

import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";

const mocks = vi.hoisted(() => ({
  route: {
    path: "/datasets/open-media-lab/showcase/blob/main/archives/models/bundle.tar",
    params: {
      namespace: "open-media-lab",
      name: "showcase",
      branch: "main",
      file: "archives/models/bundle.tar",
    },
    query: {},
  },
  listTreeImpl: vi.fn(),
}));

vi.mock("vue-router/auto", () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

vi.mock("@/utils/api", () => ({
  repoAPI: {
    listTree: (...args) => mocks.listTreeImpl(...args),
    commitFiles: vi.fn(),
  },
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({
    isAuthenticated: false,
    canWriteToNamespace: () => false,
  }),
}));

vi.mock("@/utils/clipboard", () => ({
  copyToClipboard: vi.fn().mockResolvedValue(true),
}));

vi.mock("@/utils/http-errors", () => ({
  classifyError: (err) => ({ kind: "generic", detail: String(err) }),
  classifyResponse: (resp) => ({ kind: "not-found", status: resp?.status }),
  downloadToastFor: (c) => `download-toast:${c?.kind || "?"}`,
  probeUrlAndClassify: vi.fn().mockResolvedValue({ ok: true }),
  ERROR_KIND: { NOT_FOUND: "not-found", UPSTREAM_UNAVAILABLE: "upstream-unavailable" },
}));

// The page's TarBrowserPanel mount is the surface we want to assert.
// Stub it as a presence-detector that surfaces forwarded props as
// data attributes.
vi.mock("@/components/repo/preview/TarBrowserPanel.vue", () => ({
  default: {
    name: "TarBrowserPanel",
    props: ["tarUrl", "indexUrl", "filename", "tarTreeEntry"],
    template:
      '<div data-stub="TarBrowserPanel" :data-tar-url="tarUrl" :data-index-url="indexUrl" :data-filename="filename" :data-has-tree-entry="tarTreeEntry ? \'true\' : \'false\'" />',
  },
}));

// Make the body-fetch path inert so the test doesn't loop on
// retries while the indexed-tar detection runs in parallel.
const fetchMock = vi.fn(async () =>
  new Response(new Uint8Array([0x00, 0x01]), {
    status: 200,
    headers: new Headers({ "Content-Length": "2", "X-Error-Code": "" }),
  }),
);

import BlobPage from "@/pages/[type]s/[namespace]/[name]/blob/[branch]/[...file].vue";

function mountBlob() {
  return mount(BlobPage, {
    global: {
      stubs: {
        ...ElementPlusStubs,
        RouterLink: RouterLinkStub,
        MarkdownViewer: { template: "<div data-stub=MarkdownViewer />" },
        CodeViewer: { template: "<div data-stub=CodeViewer />" },
        ErrorState: { template: "<div data-stub=ErrorState />" },
      },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.fetch = fetchMock;
});

describe("blob page · repoType derivation", () => {
  it("treats `archives/models/bundle.tar` under /datasets/ as a dataset, not a model", async () => {
    mocks.route.path =
      "/datasets/open-media-lab/showcase/blob/main/archives/models/bundle.tar";
    mocks.route.params = {
      namespace: "open-media-lab",
      name: "showcase",
      branch: "main",
      file: "archives/models/bundle.tar",
    };
    // No sibling — the listTree call should still happen but return
    // only the .tar entry, and the page should fall through to the
    // binary fallback. The crucial assertion is the listTree call's
    // first argument (repo type) — it must be "dataset", not "model".
    mocks.listTreeImpl.mockResolvedValue({
      data: [{ type: "file", path: "archives/models/bundle.tar", size: 1 }],
    });
    const wrapper = mountBlob();
    await flushPromises();
    const calls = mocks.listTreeImpl.mock.calls;
    expect(calls.length).toBeGreaterThanOrEqual(1);
    expect(calls[0][0]).toBe("dataset");
  });

  it("treats a /models/ path as a model regardless of the inner file path", async () => {
    mocks.route.path = "/models/aurora/lab/blob/main/archives/some.tar";
    mocks.route.params = {
      namespace: "aurora",
      name: "lab",
      branch: "main",
      file: "archives/some.tar",
    };
    mocks.listTreeImpl.mockResolvedValue({ data: [] });
    const wrapper = mountBlob();
    await flushPromises();
    const calls = mocks.listTreeImpl.mock.calls;
    expect(calls.length).toBeGreaterThanOrEqual(1);
    expect(calls[0][0]).toBe("model");
  });

  it("treats a /spaces/ path as a space", async () => {
    mocks.route.path = "/spaces/team/demo/blob/main/payload.tar";
    mocks.route.params = {
      namespace: "team",
      name: "demo",
      branch: "main",
      file: "payload.tar",
    };
    mocks.listTreeImpl.mockResolvedValue({ data: [] });
    mountBlob();
    await flushPromises();
    expect(mocks.listTreeImpl.mock.calls[0][0]).toBe("space");
  });
});

describe("blob page · indexed-tar inline detection", () => {
  it("renders TarBrowserPanel when the tar has a sibling .json in the same folder", async () => {
    mocks.route.path =
      "/datasets/open-media-lab/showcase/blob/main/archives/models/bundle.tar";
    mocks.route.params = {
      namespace: "open-media-lab",
      name: "showcase",
      branch: "main",
      file: "archives/models/bundle.tar",
    };
    const tarEntry = {
      type: "file",
      path: "archives/models/bundle.tar",
      size: 1024,
      oid: "deadbeef",
    };
    mocks.listTreeImpl.mockResolvedValue({
      data: [
        tarEntry,
        { type: "file", path: "archives/models/bundle.json", size: 256 },
      ],
    });
    const wrapper = mountBlob();
    await flushPromises();
    const panel = wrapper.find('[data-stub="TarBrowserPanel"]');
    expect(panel.exists()).toBe(true);
    // The tarUrl + indexUrl point at the matched pair.
    expect(panel.attributes("data-tar-url")).toContain(
      "archives/models/bundle.tar",
    );
    expect(panel.attributes("data-index-url")).toContain(
      "archives/models/bundle.json",
    );
    expect(panel.attributes("data-filename")).toBe("bundle.tar");
    expect(panel.attributes("data-has-tree-entry")).toBe("true");
  });

  it("falls back to the binary preview when no sibling .json exists", async () => {
    mocks.route.path =
      "/datasets/open-media-lab/showcase/blob/main/lonely.tar";
    mocks.route.params = {
      namespace: "open-media-lab",
      name: "showcase",
      branch: "main",
      file: "lonely.tar",
    };
    mocks.listTreeImpl.mockResolvedValue({
      data: [
        { type: "file", path: "lonely.tar", size: 1024 },
        { type: "file", path: "README.md", size: 5 },
      ],
    });
    const wrapper = mountBlob();
    await flushPromises();
    expect(wrapper.find('[data-stub="TarBrowserPanel"]').exists()).toBe(false);
  });

  it("does not call listTree at all for a non-tar file", async () => {
    mocks.route.path =
      "/datasets/open-media-lab/showcase/blob/main/notes/README.md";
    mocks.route.params = {
      namespace: "open-media-lab",
      name: "showcase",
      branch: "main",
      file: "notes/README.md",
    };
    mountBlob();
    await flushPromises();
    expect(mocks.listTreeImpl).not.toHaveBeenCalled();
  });

  it("survives a listTree failure without crashing the body fetch path", async () => {
    mocks.route.path =
      "/datasets/open-media-lab/showcase/blob/main/archives/x.tar";
    mocks.route.params = {
      namespace: "open-media-lab",
      name: "showcase",
      branch: "main",
      file: "archives/x.tar",
    };
    mocks.listTreeImpl.mockRejectedValue(new Error("network down"));
    const wrapper = mountBlob();
    await flushPromises();
    // Detection failure must not flip isIndexedTar — the panel
    // should NOT mount; the page renders the regular binary
    // fallback instead.
    expect(wrapper.find('[data-stub="TarBrowserPanel"]').exists()).toBe(false);
  });
});
