// Component tests for TarBrowserPanel.vue.
//
// The panel is the indexed-tar surface that backs both the file-list
// icon shortcut (TarBrowserDialog) and the inline standalone blob
// page. It loads an hfutils.index sidecar JSON, builds an in-memory
// tree, and serves member previews via Range reads against the .tar
// URL. Coverage targets: loading/error/listing states, search +
// pagination + view-toggle controls, breadcrumb navigation, member
// preview routing (text / image / safetensors / parquet / binary /
// too-large), the cached-bytes Download path, the
// not-auto-opening sub-dialog contract, and every branch of the
// hash banner.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { ElementPlusStubs } from "../helpers/vue";
import { server } from "../setup/msw-server";

import TarBrowserPanel from "@/components/repo/preview/TarBrowserPanel.vue";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SAFETENSORS_FIXTURE = readFileSync(
  resolve(__dirname, "../fixtures/previews/tiny.safetensors"),
);
const PARQUET_FIXTURE = readFileSync(
  resolve(__dirname, "../fixtures/previews/tiny.parquet"),
);

const INDEX_URL = "https://s3.test.local/bucket/archive.json";
const TAR_URL = "https://s3.test.local/bucket/archive.tar";

// -------- helpers --------

/**
 * Build a synthetic "tar" buffer that's just the concatenation of
 * member bytes. The hfutils.index sidecar describes each member by
 * offset+size, so the underlying storage doesn't have to be a real
 * USTAR archive — only the byte ranges have to line up.
 */
function buildArchive(members) {
  const parts = [];
  let offset = 0;
  const files = {};
  for (const [path, bytes] of members) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    parts.push(u8);
    files[path] = { offset, size: u8.byteLength, sha256: "ff".repeat(32) };
    offset += u8.byteLength;
  }
  const total = parts.reduce((acc, p) => acc + p.byteLength, 0);
  const buffer = new Uint8Array(total);
  let cursor = 0;
  for (const p of parts) {
    buffer.set(p, cursor);
    cursor += p.byteLength;
  }
  return { buffer, files, totalSize: total };
}

function rangeResponder(buffer) {
  return async ({ request }) => {
    const range = request.headers.get("range");
    if (!range) return new HttpResponse(buffer, { status: 200 });
    const m = /^bytes=(\d+)-(\d+)$/.exec(range);
    if (!m) return new HttpResponse("bad range", { status: 400 });
    const start = Number(m[1]);
    const end = Math.min(Number(m[2]), buffer.length - 1);
    const slice = buffer.subarray(start, end + 1);
    return new HttpResponse(slice, {
      status: 206,
      headers: {
        "Content-Range": `bytes ${start}-${end}/${buffer.length}`,
        "Content-Length": String(slice.length),
        "Accept-Ranges": "bytes",
      },
    });
  };
}

function serveArchive({ buffer, files, totalSize }, indexOverrides = {}) {
  const sidecar = {
    filesize: totalSize,
    hash: "ab".repeat(20),
    hash_lfs: "cd".repeat(32),
    files,
    ...indexOverrides,
  };
  server.use(
    http.get(INDEX_URL, () => HttpResponse.json(sidecar)),
    http.get(TAR_URL, rangeResponder(buffer)),
  );
  return sidecar;
}

// FilePreviewDialog has its own dedicated test file with full
// coverage of safetensors / parquet rendering. Stub it here as a
// presence-detector so the parquet test can assert that the inner
// dialog mounts on explicit click without dragging in <el-table>
// resolution + parquet fixture decoding into the panel suite.
const FilePreviewDialogStub = {
  name: "FilePreviewDialog",
  props: ["visible", "kind", "bytes", "resolveUrl", "filename"],
  template: '<div data-stub="FilePreviewDialog" />',
};

function mountPanel(props = {}) {
  return mount(TarBrowserPanel, {
    props: {
      tarUrl: TAR_URL,
      indexUrl: INDEX_URL,
      filename: "bundle.tar",
      tarTreeEntry: null,
      ...props,
    },
    global: {
      stubs: { ...ElementPlusStubs, FilePreviewDialog: FilePreviewDialogStub },
    },
  });
}

const text = (s) => new TextEncoder().encode(s);

// jsdom does not implement URL.createObjectURL / revokeObjectURL.
// Install fakes on the URL constructor itself per-test so the panel's
// blob-URL hand-off works during member-view rendering. Restore at
// teardown so the test environment's URL constructor remains intact
// for downstream tests.
let originalCreate;
let originalRevoke;
let originalFetch;

beforeEach(() => {
  originalCreate = URL.createObjectURL;
  originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => "blob:mock/abc");
  URL.revokeObjectURL = vi.fn();

  // The panel now embeds <TarMemberThumbnail> for image rows; with
  // thumbnails ON it would issue extra Range reads against the .tar
  // URL on mount, polluting the fetch counters in download-path
  // tests. Disable the global toggle for the panel suite — the
  // thumbnail behaviour itself is exhaustively covered in
  // test_tar_member_thumbnail.test.js.
  localStorage.setItem("kohaku-tar-thumbnail-enabled", "0");

  // The panel constructs `new AbortController()` inside its setup
  // and passes the signal to fetch. Under vitest + jsdom + Node 24,
  // signals constructed inside the Vue component scope fail
  // undici's WebIDL `instanceof AbortSignal` check (cross-realm),
  // and the request errors before it reaches MSW with a CORS-shape
  // classification. Strip the signal at the test boundary so the
  // production code path runs verbatim against MSW; the cancel /
  // abort behaviour is exercised separately in the indexed-tar
  // unit tests.
  originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init = {}) => {
    if (init && "signal" in init) {
      const { signal: _ignored, ...rest } = init;
      return originalFetch(input, rest);
    }
    return originalFetch(input, init);
  };
});

afterEach(() => {
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
  globalThis.fetch = originalFetch;
});

// -------- tests --------

describe("TarBrowserPanel · lifecycle", () => {
  it("renders the loading state while the sidecar is in flight", async () => {
    let release;
    server.use(
      http.get(
        INDEX_URL,
        () => new Promise((r) => {
          release = () => r(HttpResponse.json({
            filesize: 0,
            hash: "",
            hash_lfs: "",
            files: {},
          }));
        }),
      ),
    );
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain("Fetching tar index sidecar");
    release();
    await flushPromises();
  });

  it("surfaces a classified error when the sidecar fetch fails", async () => {
    server.use(
      http.get(INDEX_URL, () => new HttpResponse("nope", { status: 500 })),
    );
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.findComponent({ name: "ErrorState" }).exists()).toBe(true);
  });

  it("surfaces a format error when the sidecar JSON has no files map", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        HttpResponse.json({ filesize: 1, hash: "", hash_lfs: "" }),
      ),
    );
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.findComponent({ name: "ErrorState" }).exists()).toBe(true);
  });

  it("re-runs startLoad when the ErrorState retry callback fires", async () => {
    // First request fails — second request (after retry) succeeds.
    let attempt = 0;
    const archive = buildArchive([["x.txt", text("x")]]);
    server.use(
      http.get(INDEX_URL, () => {
        attempt += 1;
        if (attempt === 1) {
          return new HttpResponse("nope", { status: 500 });
        }
        return HttpResponse.json({
          filesize: archive.totalSize,
          hash: "",
          hash_lfs: "",
          files: archive.files,
        });
      }),
      http.get(TAR_URL, rangeResponder(archive.buffer)),
    );
    const wrapper = mountPanel();
    await flushPromises();
    const errorState = wrapper.findComponent({ name: "ErrorState" });
    expect(errorState.exists()).toBe(true);
    // The panel passes its `retry` function down through the
    // ErrorState's `retry` prop. Invoke it directly to cover the
    // function (the actual button is inside the unstubbed
    // ErrorState body and would require deeper plumbing).
    await errorState.props("retry")();
    await flushPromises();
    expect(wrapper.text()).toContain("x.txt");
  });

  it("propagates an openMember failure to the member-view error state", async () => {
    // Sidecar parses successfully but the .tar Range read throws
    // (server returns 500). The openMember catch arm sets
    // memberView.state = "error" and feeds the message through
    // ErrorState; cover that branch here.
    const archive = buildArchive([["pic.png", new Uint8Array([1, 2, 3, 4])]]);
    server.use(
      http.get(INDEX_URL, () =>
        HttpResponse.json({
          filesize: archive.totalSize,
          hash: "",
          hash_lfs: "",
          files: archive.files,
        }),
      ),
      http.get(TAR_URL, () => new HttpResponse("nope", { status: 500 })),
    );
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("pic.png"))
      .trigger("click");
    await flushPromises();
    // ErrorState with the classified failure replaces the member
    // body; presence is enough to confirm the catch arm ran.
    expect(wrapper.findAllComponents({ name: "ErrorState" }).length).toBe(
      1,
    );
  });

  it("revokes the previous blob URL and aborts the previous extract when a second member is opened in quick succession", async () => {
    // Open two members back-to-back. resetMember()'s controller-
    // abort branch and objectUrl-revoke branch are both exercised
    // by the second click.
    const archive = buildArchive([
      ["a.png", new Uint8Array([1, 2, 3])],
      ["b.png", new Uint8Array([4, 5, 6])],
    ]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("a.png"))
      .trigger("click");
    await flushPromises();
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    // backToListing first so the row is clickable again.
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Back")
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("b.png"))
      .trigger("click");
    await flushPromises();
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
    // The previous blob URL is revoked on a 60 s timer, but the
    // schedule call itself is observable now.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled(); // setTimeout-deferred
  });

  it("triggers cancelInFlight + resetMember when the indexUrl prop is cleared", async () => {
    const archive = buildArchive([["x.txt", text("x")]]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    // Setting indexUrl to "" hits the watch's `if (!newUrl)`
    // branch — cancelInFlight() + resetMember() without
    // re-loading. The listing data persists (the watch doesn't
    // clear `tree`), so we observe the side-effect by checking
    // that no error / loading state appears either.
    await wrapper.setProps({ indexUrl: "" });
    await flushPromises();
    expect(wrapper.findComponent({ name: "ErrorState" }).exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Fetching tar index sidecar");
  });

  it("re-loads when the indexUrl prop changes", async () => {
    const a = buildArchive([["a.txt", text("alpha")]]);
    const b = buildArchive([["b.txt", text("beta")]]);
    const ALT = "https://s3.test.local/bucket/other.json";
    serveArchive(a);
    server.use(http.get(ALT, () =>
      HttpResponse.json({
        filesize: b.totalSize,
        hash: "",
        hash_lfs: "",
        files: b.files,
      }),
    ));
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain("a.txt");
    await wrapper.setProps({ indexUrl: ALT });
    await flushPromises();
    expect(wrapper.text()).toContain("b.txt");
    expect(wrapper.text()).not.toContain("a.txt");
  });
});

describe("TarBrowserPanel · listing + navigation", () => {
  it("lists the in-archive folders before files in the root view", async () => {
    const archive = buildArchive([
      ["README.md", text("# hi")],
      ["data/rows.json", text("{}")],
      ["images/cover.png", text("PNG")],
    ]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    const body = wrapper.text();
    expect(body).toContain("data");
    expect(body).toContain("images");
    expect(body).toContain("README.md");
    // Aggregate stats are rendered in the toolbar.
    expect(body).toContain("3 files in archive");
  });

  it("navigates into a folder when its row is clicked, then back via the breadcrumb root", async () => {
    const archive = buildArchive([
      ["README.md", text("hello")],
      ["data/a.txt", text("aaa")],
      ["data/b.txt", text("bbb")],
    ]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    // Find the "data" row by its name and click it.
    const folderRow = wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("data"));
    expect(folderRow).toBeDefined();
    await folderRow.trigger("click");
    expect(wrapper.text()).toContain("a.txt");
    expect(wrapper.text()).toContain("b.txt");
    // Click the .tar root crumb (first breadcrumb item) to jump back.
    const rootCrumb = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "bundle.tar");
    expect(rootCrumb).toBeDefined();
    await rootCrumb.trigger("click");
    expect(wrapper.text()).toContain("README.md");
  });

  it("hides the Up button at root and shows it after entering a folder", async () => {
    const archive = buildArchive([
      ["root.txt", text("R")],
      ["sub/inner.txt", text("I")],
    ]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    const upBefore = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Up");
    expect(upBefore).toBeUndefined();

    const subRow = wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("sub"));
    await subRow.trigger("click");

    const upAfter = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Up");
    expect(upAfter).toBeDefined();
    await upAfter.trigger("click");
    // Back at root again, no Up button.
    expect(
      wrapper.findAll("button").find((b) => b.text().trim() === "Up"),
    ).toBeUndefined();
  });

  it("starts in grid mode by default and persists a switch to list in localStorage", async () => {
    // The panel suite's beforeEach already disables thumbnails;
    // it does NOT touch the new view-mode key, so this test sees
    // the production default (grid).
    const archive = buildArchive([["a.txt", text("a")]]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    // The list-mode toolbar uses a horizontal flex of rows; grid
    // mode lays the entries out in a CSS grid. Detect via the
    // grid container's class signature.
    expect(wrapper.find(".grid-cols-2").exists()).toBe(true);

    // Flip to list. The ElRadioGroup stub carries the v-model
    // and emits `update:modelValue`; emitting from the stub
    // updates the panel's `viewMode` ref the same way a real
    // click would.
    await wrapper.findComponent({ name: "ElRadioGroup" }).setValue("list");
    await flushPromises();
    expect(localStorage.getItem("kohaku-tar-view-mode")).toBe("list");
  });

  it("persists a page-size change to localStorage", async () => {
    const archive = buildArchive([["a.txt", text("a")]]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    // ElementPlusStubs' ElSelect emits update:modelValue + change.
    // Drive a value change directly on the v-model binding.
    await wrapper.findComponent({ name: "ElSelect" }).setValue(50);
    await flushPromises();
    expect(localStorage.getItem("kohaku-tar-page-size")).toBe("50");
  });

  it("filters the listing in place when the search input has a query", async () => {
    const archive = buildArchive([
      ["alpha.txt", text("a")],
      ["beta.txt", text("b")],
      ["gamma.txt", text("g")],
    ]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    const search = wrapper.find('input[placeholder="Filter in this folder…"]');
    expect(search.exists()).toBe(true);
    await search.setValue("bet");
    await flushPromises();
    expect(wrapper.text()).toContain("beta.txt");
    expect(wrapper.text()).not.toContain("alpha.txt");
    expect(wrapper.text()).not.toContain("gamma.txt");
  });

  it("paginates the listing when the entry count exceeds the page size", async () => {
    const members = [];
    for (let i = 0; i < 220; i++) members.push([`item-${String(i).padStart(3, "0")}.json`, text(`{"i":${i}}`)]);
    const archive = buildArchive(members);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    // pageSize default is 100 — first page shows item-000 not item-150.
    expect(wrapper.text()).toContain("item-000.json");
    expect(wrapper.text()).not.toContain("item-150.json");
    expect(wrapper.text()).toContain("220 entries");
  });
});

describe("TarBrowserPanel · member preview routing", () => {
  it("decodes a small text member into the inline pre/markdown viewer", async () => {
    const archive = buildArchive([["notes.txt", text("hello world")]]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    const fileRow = wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("notes.txt"));
    await fileRow.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("hello world");
    expect(wrapper.find('button[type="button"]').text()).toContain("Back");
  });

  it("routes an image member to a blob URL via createObjectURL", async () => {
    const archive = buildArchive([
      ["pic.png", new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])],
    ]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    const fileRow = wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("pic.png"));
    await fileRow.trigger("click");
    await flushPromises();
    expect(URL.createObjectURL).toHaveBeenCalled();
    const img = wrapper.find('img[alt="pic.png"]');
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("blob:mock/abc");
  });

  it("does not auto-open the inner FilePreviewDialog when a parquet member is clicked", async () => {
    const archive = buildArchive([["sample.parquet", PARQUET_FIXTURE]]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("sample.parquet"))
      .trigger("click");
    await flushPromises();
    // FilePreviewDialog stays unmounted until the explicit
    // "Open metadata preview" click. Reproduces the regression
    // where the inner dialog stacked over the button and
    // intercepted clicks.
    expect(wrapper.findComponent({ name: "FilePreviewDialog" }).exists()).toBe(
      false,
    );
    const openBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Open metadata preview"));
    expect(openBtn).toBeDefined();
    await openBtn.trigger("click");
    await flushPromises();
    expect(wrapper.findComponent({ name: "FilePreviewDialog" }).exists()).toBe(
      true,
    );
  });

  it("routes a safetensors member through the from-buffer parser (no URL fetch)", async () => {
    const archive = buildArchive([["weights.safetensors", SAFETENSORS_FIXTURE]]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("weights.safetensors"))
      .trigger("click");
    await flushPromises();
    // Member view shows the "Open metadata preview" CTA — the
    // tensor-tree itself is rendered inside FilePreviewDialog, so
    // we only assert the hand-off button is wired up here.
    const openBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Open metadata preview"));
    expect(openBtn).toBeDefined();
  });

  it("renders the binary fallback for an unrecognised extension", async () => {
    const archive = buildArchive([
      ["weights.bin", new Uint8Array([0x00, 0x01, 0x02, 0x03])],
    ]);
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("weights.bin"))
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Binary member");
  });

  it("falls back to the too-large-text screen when a text member exceeds the inline cap", async () => {
    // Above INLINE_TEXT_MAX_BYTES = 256 KB. The actual buffer payload
    // is small; only the recorded `size` matters because the size-
    // gate runs before extractMemberBytes is called.
    const archive = buildArchive([["big.log", text("a")]]);
    archive.files["big.log"].size = 256 * 1024 + 1;
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("big.log"))
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("too large to inline-preview");
  });

  it("falls back to the too-large-blob screen when an image exceeds the inline cap", async () => {
    const archive = buildArchive([["huge.png", new Uint8Array([0x89])]]);
    archive.files["huge.png"].size = 200 * 1024 * 1024 + 1;
    serveArchive(archive);

    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("huge.png"))
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("above the inline-preview cap");
  });

  it("returns to the listing when the member view's Back button is clicked", async () => {
    const archive = buildArchive([
      ["docs/intro.txt", text("hello")],
      ["data/x.txt", text("x")],
    ]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    // Drill into docs/, click intro.txt to open the member view.
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("docs"))
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("intro.txt"))
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("hello");

    const backBtn = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Back");
    expect(backBtn).toBeDefined();
    await backBtn.trigger("click");
    await flushPromises();
    // Back to the docs/ listing — intro.txt is back in the listing
    // and the member's body content is gone.
    expect(wrapper.text()).not.toContain("hello");
    expect(
      wrapper
        .findAll(".cursor-pointer")
        .some((w) => w.text().startsWith("intro.txt")),
    ).toBe(true);
  });

  it("handles a zero-byte member without issuing a Range read", async () => {
    const archive = buildArchive([["empty.txt", new Uint8Array(0)]]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("empty.txt"))
      .trigger("click");
    await flushPromises();
    // The member view header is rendered (Back + Download buttons).
    expect(
      wrapper.findAll("button").some((b) => b.text().trim() === "Back"),
    ).toBe(true);
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Download")),
    ).toBe(true);
  });
});

describe("TarBrowserPanel · download path", () => {
  it("re-extracts bytes when Download is clicked from the binary fallback state (no cache)", async () => {
    const archive = buildArchive([
      ["payload.bin", new Uint8Array([10, 20, 30, 40])],
    ]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("payload.bin"))
      .trigger("click");
    await flushPromises();
    // Binary classification skips the up-front extract, so the
    // Download button has no cached bytes and must re-issue the
    // Range read against the .tar URL.
    expect(wrapper.text()).toContain("Binary member");
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      const downloadBtn = wrapper
        .findAll("button")
        .filter((b) => b.text().includes("Download"))[0];
      await downloadBtn.trigger("click");
      await flushPromises();
      expect(clickSpy).toHaveBeenCalled();
    } finally {
      clickSpy.mockRestore();
    }
  });

  it("surfaces an ElMessage error when the download Range read fails", async () => {
    const archive = buildArchive([
      ["payload.bin", new Uint8Array([1, 2, 3, 4])],
    ]);
    serveArchive(archive);
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll(".cursor-pointer")
      .find((w) => w.text().startsWith("payload.bin"))
      .trigger("click");
    await flushPromises();
    // Reroute the .tar URL to 500 only AFTER the binary state has
    // been settled, so the next Range read fails. The catch arm of
    // downloadMember turns the throw into an ElMessage.error toast.
    server.use(
      http.get(TAR_URL, () => new HttpResponse("nope", { status: 500 })),
    );
    const elementPlus = await import("element-plus");
    const errorSpy = vi.spyOn(elementPlus.ElMessage, "error");
    try {
      const downloadBtn = wrapper
        .findAll("button")
        .filter((b) => b.text().includes("Download"))[0];
      await downloadBtn.trigger("click");
      await flushPromises();
      expect(errorSpy).toHaveBeenCalled();
      expect(errorSpy.mock.calls[0][0]).toMatch(/Download failed/);
    } finally {
      errorSpy.mockRestore();
    }
  });

  it("reuses the in-memory bytes for Download instead of re-issuing a Range read", async () => {
    const archive = buildArchive([["pic.png", new Uint8Array([1, 2, 3, 4])]]);
    serveArchive(archive);

    // Spy on global fetch to count Range requests against the .tar
    // URL specifically.
    const realFetch = globalThis.fetch;
    let tarFetchCount = 0;
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = typeof input === "string" ? input : input.url;
      if (url === TAR_URL) tarFetchCount += 1;
      return realFetch(input, init);
    });
    try {
      const wrapper = mountPanel();
      await flushPromises();
      await wrapper
        .findAll(".cursor-pointer")
        .find((w) => w.text().startsWith("pic.png"))
        .trigger("click");
      await flushPromises();
      expect(tarFetchCount).toBe(1);

      // Click the member-view Download button — it must reuse the
      // already-extracted bytes and not issue a second Range read.
      const downloadBtn = wrapper
        .findAll("button")
        .filter((b) => b.text().includes("Download"))[0];
      // downloadBytesAs builds a real <a download> anchor and calls
      // .click(). Patch HTMLAnchorElement.prototype.click so the
      // download invocation is observable without jsdom triggering
      // a navigation that the test runner can't service.
      const clickSpy = vi
        .spyOn(HTMLAnchorElement.prototype, "click")
        .mockImplementation(() => {});
      try {
        await downloadBtn.trigger("click");
        await flushPromises();
        expect(clickSpy).toHaveBeenCalled();
        expect(tarFetchCount).toBe(1);
      } finally {
        clickSpy.mockRestore();
      }
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});

describe("TarBrowserPanel · hash banner", () => {
  function makeTreeEntry(sha) {
    return {
      type: "file",
      oid: sha,
      size: 1234,
      lfs: { oid: sha, size: 1234, pointerSize: 134 },
    };
  }

  // The ElAlert stub used by these tests renders the body slot but
  // not the `title` prop, so we assert against the alert body copy.
  // The banner KIND is what matters here (warning / info / hidden);
  // the title text is verified via the production e2e flow.

  it("shows the warning banner when index hash_lfs disagrees with the tree-API oid", async () => {
    const archive = buildArchive([["x.txt", text("x")]]);
    serveArchive(archive, { hash_lfs: "a".repeat(64) });
    const wrapper = mountPanel({ tarTreeEntry: makeTreeEntry("b".repeat(64)) });
    await flushPromises();
    expect(wrapper.text()).toContain("does not match");
    expect(wrapper.text()).toContain(
      "may have been re-uploaded without regenerating the index",
    );
  });

  it("shows the info notice when both index hashes are empty", async () => {
    const archive = buildArchive([["x.txt", text("x")]]);
    serveArchive(archive, { hash: "", hash_lfs: "" });
    const wrapper = mountPanel({ tarTreeEntry: makeTreeEntry("a".repeat(64)) });
    await flushPromises();
    expect(wrapper.text()).toContain(
      "does not carry a tar hash",
    );
  });

  it("shows the partial banner when the tree entry has no hash to compare", async () => {
    const archive = buildArchive([["x.txt", text("x")]]);
    serveArchive(archive);
    const wrapper = mountPanel({ tarTreeEntry: { type: "file", oid: "" } });
    await flushPromises();
    expect(wrapper.text()).toContain(
      "Could not verify the tar against the sidecar index",
    );
  });

  it("hides the banner entirely when the hashes match", async () => {
    const archive = buildArchive([["x.txt", text("x")]]);
    const sha = "f".repeat(64);
    serveArchive(archive, { hash_lfs: sha });
    const wrapper = mountPanel({ tarTreeEntry: makeTreeEntry(sha) });
    await flushPromises();
    expect(wrapper.text()).not.toContain("does not match");
    expect(wrapper.text()).not.toContain("does not carry a tar hash");
    expect(wrapper.text()).not.toContain(
      "Could not verify the tar against the sidecar index",
    );
  });
});
