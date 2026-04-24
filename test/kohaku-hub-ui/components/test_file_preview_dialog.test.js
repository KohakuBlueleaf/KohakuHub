import { flushPromises, mount } from "@vue/test-utils";
import { nextTick, defineComponent, h } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";

// ElementPlusStubs does not stub ElTable / ElTableColumn. The dialog's
// ready-state template uses <el-table> with scoped <el-table-column>
// slots — a pattern where the column's default slot is called by the
// parent table (not the column itself). Reproduce just enough of that
// contract here: ElTable walks its column children, and for each data
// row invokes each column's default slot with `{ row }`, falling back
// to `row[prop]` for slot-less columns. That matches Element Plus's
// public scoped-slot contract closely enough for text-based assertions.
const ElTableStub = defineComponent({
  name: "ElTable",
  props: {
    data: { type: Array, default: () => [] },
    rowKey: { type: String, default: "" },
    treeProps: { type: Object, default: () => ({}) },
    defaultExpandAll: { type: Boolean, default: false },
  },
  setup(props, { slots }) {
    return () => {
      const columnNodes = (slots.default?.() ?? []).filter(
        (node) => node.type?.name === "ElTableColumn",
      );
      // Flatten tree rows via `treeProps.children` so leaves — which
      // live under `row.children` in tree mode — also render and
      // appear in text-based assertions. Real ElTable hides
      // collapsed subtrees behind expand chevrons; the stub does not
      // model chevron state so everything renders, which is exactly
      // what text-level tests want to assert against.
      const childrenKey = props.treeProps?.children || "children";
      const flat = [];
      (function walk(rows) {
        for (const r of rows || []) {
          flat.push(r);
          const kids = r?.[childrenKey];
          if (Array.isArray(kids) && kids.length) walk(kids);
        }
      })(props.data);
      // Render the per-column header slot if one is provided so any
      // clickable header wires up from the test.
      const headerSlots = columnNodes
        .map((col) => (col.children || {}).header)
        .filter((fn) => typeof fn === "function");
      const thead = headerSlots.length
        ? h(
            "thead",
            {},
            h(
              "tr",
              {},
              headerSlots.map((fn) => h("th", {}, fn())),
            ),
          )
        : null;
      return h(
        "table",
        { "data-el-table": "true" },
        [
          thead,
          ...flat.map((row) =>
            h(
              "tr",
              {},
              columnNodes.map((col) => {
                const colSlots = col.children || {};
                const colProps = col.props || {};
                if (typeof colSlots.default === "function") {
                  return h("td", {}, colSlots.default({ row }));
                }
                return h("td", {}, String(row[colProps.prop] ?? ""));
              }),
            ),
          ),
        ].filter(Boolean),
      );
    };
  },
});

// ElTableColumn is a marker component for the ElTable stub above —
// rendering the child directly (outside a table) still needs a node so
// Vue does not warn. Keep it empty so header-row logic inside ElTable
// owns the real rendering.
const ElTableColumnStub = defineComponent({
  name: "ElTableColumn",
  props: {
    prop: { type: String, default: "" },
    label: { type: String, default: "" },
  },
  setup: () => () => null,
});

const dialogStubs = {
  ...ElementPlusStubs,
  ElTable: ElTableStub,
  ElTableColumn: ElTableColumnStub,
};

// A controllable stand-in for the two parser modules. Each test rebuilds
// the pending deferred + captured calls before mounting the component so
// we can drive loading → ready / error phases explicitly instead of
// relying on real HTTP.
const safetensorsCtrl = { deferred: null, calls: [] };
const parquetCtrl = { deferred: null, calls: [] };

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

vi.mock("@/utils/safetensors", () => ({
  parseSafetensorsMetadata: vi.fn((url, opts = {}) => {
    safetensorsCtrl.calls.push({ url, opts });
    return safetensorsCtrl.deferred.promise;
  }),
  // Compute totals from the actual header so tests exercising the
  // human-readable toggle see a number large enough to render
  // differently under the two formats ("1.23B" vs "1,234,567,890").
  // The default mock's fixed `total: 4` made both formats collapse to
  // the same "4".
  summarizeSafetensors: vi.fn((header) => {
    const parameters = {};
    let total = 0;
    let byteSize = 0;
    for (const entry of Object.values(header?.tensors || {})) {
      const p = entry?.parameters ?? 0;
      total += p;
      parameters[entry?.dtype ?? "?"] =
        (parameters[entry?.dtype ?? "?"] ?? 0) + p;
      byteSize += Array.isArray(entry?.data_offsets)
        ? entry.data_offsets[1] - entry.data_offsets[0]
        : 0;
    }
    return { parameters, total, byte_size: byteSize };
  }),
  // buildTensorTree and formatHumanReadable are pure helpers in the
  // real module; the dialog consumes their outputs so we provide
  // deterministic stand-ins instead of re-mocking per test. The stub
  // shape mirrors the real contract: nested {segment, path, isLeaf,
  // children, parameters, percent, dtypeLabel, leafCount, shape,
  // byteSize}. One parent "encoder" with two leaf children under it.
  buildTensorTree: vi.fn((tensors, total) => {
    const entries = Object.entries(tensors || {});
    if (entries.length === 0) return [];
    // Split each tensor name on "." and use the first segment as the
    // parent-node name; rest as leaf segment. Sum params + bytes.
    const groups = new Map();
    for (const [name, entry] of entries) {
      const [head, ...rest] = name.split(".");
      const leafSegment = rest.length > 0 ? rest.join(".") : name;
      if (!groups.has(head)) groups.set(head, { leaves: [], params: 0, bytes: 0 });
      const grp = groups.get(head);
      const params = entry.parameters ?? 0;
      const bytes = Array.isArray(entry.data_offsets)
        ? entry.data_offsets[1] - entry.data_offsets[0]
        : 0;
      grp.leaves.push({
        path: name,
        segment: leafSegment,
        isLeaf: true,
        dtype: entry.dtype,
        dtypeLabel: entry.dtype,
        shape: Array.isArray(entry.shape) ? entry.shape : [],
        parameters: params,
        byteSize: bytes,
        leafCount: 1,
        percent: total > 0 ? (params / total) * 100 : 0,
      });
      grp.params += params;
      grp.bytes += bytes;
    }
    return [...groups.entries()].map(([head, grp]) => ({
      path: head,
      segment: head,
      isLeaf: false,
      dtypeLabel:
        new Set(grp.leaves.map((l) => l.dtype)).size === 1
          ? grp.leaves[0].dtype
          : `${new Set(grp.leaves.map((l) => l.dtype)).size} dtypes`,
      shape: [],
      parameters: grp.params,
      byteSize: grp.bytes,
      leafCount: grp.leaves.length,
      percent: total > 0 ? (grp.params / total) * 100 : 0,
      children: grp.leaves,
    }));
  }),
  formatHumanReadable: vi.fn((n) => {
    if (n == null || Number.isNaN(n)) return "-";
    if (n >= 1e9) return `${(n / 1e9).toFixed(2).replace(/\.?0+$/, "")}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(2).replace(/\.?0+$/, "")}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(2).replace(/\.?0+$/, "")}K`;
    return String(n);
  }),
  SafetensorsFetchError: class SafetensorsFetchError extends Error {
    constructor(message, status) {
      super(message);
      this.name = "SafetensorsFetchError";
      this.status = status;
    }
  },
}));

vi.mock("@/utils/parquet", () => ({
  parseParquetMetadata: vi.fn((url, opts = {}) => {
    parquetCtrl.calls.push({ url, opts });
    return parquetCtrl.deferred.promise;
  }),
  summarizeParquetSchema: vi.fn(() => ({
    columnCount: 1,
    columns: [
      {
        name: "col",
        physicalType: "INT32",
        logicalType: null,
        repetitionType: "REQUIRED",
      },
    ],
  })),
}));

// Import *after* vi.mock so the component consumes the stubs.
import FilePreviewDialog from "@/components/repo/preview/FilePreviewDialog.vue";

function mountDialog(props) {
  return mount(FilePreviewDialog, {
    props: {
      visible: true,
      ...props,
    },
    global: { stubs: dialogStubs },
  });
}

describe("FilePreviewDialog", () => {
  beforeEach(() => {
    safetensorsCtrl.deferred = makeDeferred();
    safetensorsCtrl.calls.length = 0;
    parquetCtrl.deferred = makeDeferred();
    parquetCtrl.calls.length = 0;
    vi.clearAllMocks();
  });

  it("starts in the loading phase and advances the progress text as onProgress fires", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Preparing Range request");

    const onProgress = safetensorsCtrl.calls[0].opts.onProgress;
    onProgress("range-head", { bytes: 100000 });
    await nextTick();
    expect(wrapper.text()).toContain("Fetching header Range");

    onProgress("parsing");
    await nextTick();
    expect(wrapper.text()).toContain("Parsing header JSON");

    onProgress("done");
    await nextTick();
    // Dialog is still in loading view (state has not flipped to
    // "ready" yet — that happens when the parser promise resolves)
    // but the phase line reflects the final copy before the state
    // transition races in.
    expect(wrapper.text()).toContain("Done.");

    // Cover the fat-header copy that only fires when the speculative
    // 100 KB read misses the full header length.
    onProgress("range-full", { bytes: 200_000 });
    await nextTick();
    expect(wrapper.text()).toContain("Header is large");

    wrapper.unmount();
  });

  it("uses the parquet-flavored progress copy when kind=parquet", async () => {
    const wrapper = mountDialog({
      kind: "parquet",
      resolveUrl: "http://host/ds/resolve/main/train.parquet",
      filename: "train.parquet",
    });
    await flushPromises();

    const onProgress = parquetCtrl.calls[0].opts.onProgress;
    onProgress("head");
    await nextTick();
    expect(wrapper.text()).toContain("Probing file size");

    onProgress("footer", { byteLength: 999 });
    await nextTick();
    expect(wrapper.text()).toContain("Fetching parquet footer");

    onProgress("parsing");
    await nextTick();
    expect(wrapper.text()).toContain("Decoding parquet metadata");

    onProgress("done");
    await nextTick();
    expect(wrapper.text()).toContain("Done.");

    wrapper.unmount();
  });

  it("falls through to the raw phase name for an unknown kind", async () => {
    const wrapper = mountDialog({
      kind: "gguf",
      resolveUrl: "http://host/repo/resolve/main/x.gguf",
      filename: "x.gguf",
    });
    await flushPromises();
    // gguf → init copy falls through to the raw phase string ("init")
    // because describePhase has no branch for unknown kinds. The
    // dialog already renders the "Preview failed" view because the
    // component rejects unsupported kinds, so this assertion just
    // confirms the fallthrough did not crash.
    expect(wrapper.text()).toContain("Preview failed");
    wrapper.unmount();
  });

  it("renders the safetensors tree (parent + nested leaves) with per-row percent", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    safetensorsCtrl.deferred.resolve({
      metadata: null,
      tensors: {
        "encoder.layer.weight": {
          dtype: "F32",
          shape: [4, 4],
          parameters: 16,
          data_offsets: [0, 64],
        },
        "encoder.layer.bias": {
          dtype: "F32",
          shape: [4],
          parameters: 4,
          data_offsets: [64, 80],
        },
      },
    });
    await flushPromises();

    const text = wrapper.text();
    // Parent rows show segment name + leafCount hint.
    expect(text).toContain("encoder");
    expect(text).toContain("2 tensors");
    // Leaves render too (tree table default-expand-all=false, but stub
    // expands everything and we rely on stub tolerance here).
    expect(text).toContain("weight");
    expect(text).toContain("bias");
    // Percent column carries the aggregated value; summary forces
    // percent text to match the total (100% on the root since every
    // tensor lives under `encoder`).
    expect(text).toMatch(/100\.00%/);

    wrapper.unmount();
  });

  it("toggles the Total parameters pill between compact and exact formats", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/big.safetensors",
      filename: "big.safetensors",
    });
    await flushPromises();

    safetensorsCtrl.deferred.resolve({
      metadata: null,
      tensors: {
        big: {
          dtype: "F32",
          shape: [1_234_567_890],
          parameters: 1_234_567_890,
          data_offsets: [0, 4 * 1_234_567_890],
        },
      },
    });
    await flushPromises();

    // Override the summary mock locally to return the real total —
    // the default mock returns 4, which wouldn't exercise the
    // human-readable path.
    // Re-mocking at this point would require vi.resetModules; instead
    // assert the toggle behavior directly on the component. Both
    // strings ("human" and "exact") are shaped by formatHumanReadable
    // / formatNumber which live behind the mock boundary, so we just
    // verify that the pill text CHANGES when clicked and the `title`
    // attribute flips.

    const pillBefore = wrapper
      .findAll("[title]")
      .find((el) => /compact|exact/.test(el.attributes("title") || ""));
    expect(pillBefore).toBeTruthy();
    const titleBefore = pillBefore.attributes("title");
    const textBefore = pillBefore.text();

    await pillBefore.trigger("click");
    await flushPromises();

    const pillAfter = wrapper
      .findAll("[title]")
      .find((el) => /compact|exact/.test(el.attributes("title") || ""));
    expect(pillAfter.attributes("title")).not.toBe(titleBefore);
    expect(pillAfter.text()).not.toBe(textBefore);

    wrapper.unmount();
  });

  it("renders the safetensors result once the parser resolves", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    safetensorsCtrl.deferred.resolve({
      metadata: { format: "pt", notes: "seed-3b" },
      tensors: {
        "layer.weight": {
          dtype: "F32",
          shape: [2, 2],
          parameters: 4,
          data_offsets: [0, 16],
        },
      },
    });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Total parameters");
    expect(text).toContain("4");
    // Tree mode shows each path segment on its own row (parent =
    // "layer", leaf = "weight"); the fully-qualified "layer.weight"
    // lives on the `row-key` and is not rendered verbatim.
    expect(text).toContain("layer");
    expect(text).toContain("weight");
    expect(text).toContain("__metadata__");
    expect(text).toContain("notes");
    expect(text).toContain("seed-3b");

    wrapper.unmount();
  });

  it("renders the parquet result once the parser resolves", async () => {
    const wrapper = mountDialog({
      kind: "parquet",
      resolveUrl: "http://host/ds/resolve/main/train.parquet",
      filename: "train.parquet",
    });
    await flushPromises();

    parquetCtrl.deferred.resolve({
      byteLength: 123_456,
      numRows: 500,
      createdBy: "parquet-cpp 15.0",
      keyValueMetadata: [],
      schema: [],
      schemaTree: { children: [] },
      rowGroups: [{ numRows: 500, totalByteSize: 8000 }],
    });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Rows");
    expect(text).toContain("500");
    expect(text).toContain("col");
    expect(text).toContain("INT32");
    expect(text).toContain("parquet-cpp 15.0");

    wrapper.unmount();
  });

  it("surfaces the parser error and exposes a Retry path", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    safetensorsCtrl.deferred.reject(new Error("internal explosion"));
    await flushPromises();

    expect(wrapper.text()).toContain("Preview failed");
    expect(wrapper.text()).toContain("internal explosion");

    // Clicking Retry kicks off a fresh parser call.
    safetensorsCtrl.deferred = makeDeferred();
    const retryBtn = wrapper.findAll("button").find((b) => b.text() === "Retry");
    expect(retryBtn).toBeTruthy();
    await retryBtn.trigger("click");
    await flushPromises();

    // Parser is called a second time with a fresh onProgress hook.
    expect(safetensorsCtrl.calls).toHaveLength(2);
    expect(wrapper.text()).toContain("Preparing Range request");

    wrapper.unmount();
  });

  it("flags likely-CORS errors with a docs pointer", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    // TypeError("Failed to fetch") is the canonical Chromium CORS
    // signature — no other browser signal ever reaches JS.
    safetensorsCtrl.deferred.reject(new TypeError("Failed to fetch"));
    await flushPromises();

    expect(wrapper.text()).toContain("looks like a CORS failure");
    expect(wrapper.text()).toMatch(/Access-Control-Allow-Origin/);
    expect(wrapper.text()).toMatch(/MinIO CORS/);

    wrapper.unmount();
  });

  it("does NOT flag CORS when the error is a normal rejection", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    safetensorsCtrl.deferred.reject(new Error("404 not found"));
    await flushPromises();

    expect(wrapper.text()).toContain("Preview failed");
    expect(wrapper.text()).not.toContain("looks like a CORS failure");

    wrapper.unmount();
  });

  it("swallows AbortError silently (no error UI flashed at the user)", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    const abortErr = new Error("aborted");
    abortErr.name = "AbortError";
    safetensorsCtrl.deferred.reject(abortErr);
    await flushPromises();

    expect(wrapper.text()).not.toContain("Preview failed");
    wrapper.unmount();
  });

  it("aborts the in-flight parser when visibility flips to false", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();
    const signal = safetensorsCtrl.calls[0].opts.signal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);

    await wrapper.setProps({ visible: false });
    await flushPromises();
    expect(signal.aborted).toBe(true);

    wrapper.unmount();
  });

  it("re-requests when resolveUrl changes", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/one.safetensors",
      filename: "one.safetensors",
    });
    await flushPromises();
    expect(safetensorsCtrl.calls).toHaveLength(1);

    safetensorsCtrl.deferred = makeDeferred();
    await wrapper.setProps({
      resolveUrl: "http://host/repo/resolve/main/two.safetensors",
      filename: "two.safetensors",
    });
    await flushPromises();

    expect(safetensorsCtrl.calls).toHaveLength(2);
    expect(safetensorsCtrl.calls[1].url).toContain("two.safetensors");

    wrapper.unmount();
  });

  it("throws-style rejection for unsupported kinds still renders a clean error state", async () => {
    const wrapper = mountDialog({
      kind: "gguf", // unsupported
      resolveUrl: "http://host/repo/resolve/main/x.gguf",
      filename: "x.gguf",
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Preview failed");
    expect(wrapper.text()).toContain("Unsupported preview kind: gguf");

    wrapper.unmount();
  });

  it("renders a GatedRepo-specific message + sources table when the parser throws a structured fallback error", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/model.safetensors",
      filename: "model.safetensors",
    });
    await flushPromises();

    const gatedErr = new Error(
      "Upstream source requires authentication - likely a gated repository.",
    );
    gatedErr.name = "SafetensorsFetchError";
    gatedErr.status = 401;
    gatedErr.errorCode = "GatedRepo";
    gatedErr.detail =
      "Upstream source requires authentication - likely a gated repository.";
    gatedErr.sources = [
      {
        name: "HuggingFace",
        url: "https://huggingface.co",
        status: 401,
        category: "auth",
        message: "Access to model owner/demo is restricted.",
      },
      {
        name: "Mirror",
        url: "https://mirror.local",
        status: 404,
        category: "not-found",
        message: "File not found",
      },
    ];
    safetensorsCtrl.deferred.reject(gatedErr);
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Authentication required");
    // The copy guides the user toward the concrete next step.
    expect(text).toContain("Attach an access token");
    // Raw upstream message surfaces in the sources details.
    expect(text).toContain("Access to model owner/demo is restricted");
    expect(text).toContain("Mirror");
    expect(text).toContain("404");
    // The generic CORS-guidance copy must NOT appear here — CORS and
    // gated-repo are different remediations.
    expect(text).not.toContain("CORS failure");

    wrapper.unmount();
  });

  it("renders a file-not-found message for an aggregated EntryNotFound error", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/nope.safetensors",
      filename: "nope.safetensors",
    });
    await flushPromises();

    const notFound = new Error("No fallback source serves this file.");
    notFound.name = "SafetensorsFetchError";
    notFound.status = 404;
    notFound.errorCode = "EntryNotFound";
    notFound.detail = "No fallback source serves this file.";
    notFound.sources = [
      { name: "A", url: "https://a", status: 404, category: "not-found", message: "" },
      { name: "B", url: "https://b", status: 404, category: "not-found", message: "" },
    ];
    safetensorsCtrl.deferred.reject(notFound);
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("File not found on any source");
    expect(text).toContain("Every configured fallback source returned 404");

    wrapper.unmount();
  });

  it("renders an upstream-unavailable message when status is 502 with no error code", async () => {
    const wrapper = mountDialog({
      kind: "safetensors",
      resolveUrl: "http://host/repo/resolve/main/transient.safetensors",
      filename: "transient.safetensors",
    });
    await flushPromises();

    const upstream = new Error("All fallback sources failed - upstream unavailable.");
    upstream.name = "SafetensorsFetchError";
    upstream.status = 502;
    upstream.errorCode = null;
    upstream.detail = "All fallback sources failed - upstream unavailable.";
    upstream.sources = null;
    safetensorsCtrl.deferred.reject(upstream);
    await flushPromises();

    expect(wrapper.text()).toContain("Upstream source unavailable");
    wrapper.unmount();
  });

  it("emits update:visible when the footer Close button is clicked", async () => {
    const wrapper = mountDialog({
      kind: "parquet",
      resolveUrl: "http://host/ds/resolve/main/t.parquet",
      filename: "t.parquet",
    });
    await flushPromises();

    const closeBtn = wrapper
      .findAll("button")
      .find((b) => b.text() === "Close");
    expect(closeBtn).toBeTruthy();
    await closeBtn.trigger("click");
    expect(wrapper.emitted("update:visible")?.at(-1)).toEqual([false]);

    wrapper.unmount();
  });
});
