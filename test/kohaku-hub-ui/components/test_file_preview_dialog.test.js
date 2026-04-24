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
  },
  setup(props, { slots }) {
    return () => {
      const columnNodes = (slots.default?.() ?? []).filter(
        (node) => node.type?.name === "ElTableColumn",
      );
      return h(
        "table",
        { "data-el-table": "true" },
        (props.data || []).map((row) =>
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
  summarizeSafetensors: vi.fn((header) => ({
    parameters: { F32: 4 },
    total: 4,
    byte_size: 16,
  })),
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
    expect(text).toContain("layer.weight");
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
