import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { describe, expect, it, vi } from "vitest";

import ErrorState from "@/components/common/ErrorState.vue";
import { defaultCopyFor, ERROR_KIND } from "@/utils/http-errors";
import { ElementPlusStubs } from "../helpers/vue";

// Mount helper that supplies the ElTable stubs already used in the
// dialog test (ErrorState renders an el-table inside the sources
// disclosure; without stubs the real ElTable would complain about
// missing CSS env in jsdom).
const ElTableStub = defineComponent({
  name: "ElTable",
  props: { data: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () => {
      const cols = (slots.default?.() ?? []).filter(
        (n) => n.type?.name === "ElTableColumn",
      );
      return h(
        "table",
        {},
        (props.data || []).map((row) =>
          h(
            "tr",
            {},
            cols.map((col) => {
              const slot = col.children || {};
              const p = col.props || {};
              if (typeof slot.default === "function") {
                return h("td", {}, slot.default({ row }));
              }
              return h("td", {}, String(row[p.prop] ?? ""));
            }),
          ),
        ),
      );
    };
  },
});

const ElTableColumnStub = defineComponent({
  name: "ElTableColumn",
  props: {
    prop: { type: String, default: "" },
    label: { type: String, default: "" },
  },
  setup: () => () => null,
});

const stubs = {
  ...ElementPlusStubs,
  ElTable: ElTableStub,
  ElTableColumn: ElTableColumnStub,
};

function mountWith({ classification, ...props } = {}) {
  return mount(ErrorState, {
    props: { classification: classification ?? { kind: ERROR_KIND.GENERIC }, ...props },
    global: { stubs },
  });
}

describe("ErrorState", () => {
  for (const kind of Object.values(ERROR_KIND)) {
    it(`renders the default copy for kind=${kind}`, () => {
      const wrapper = mountWith({ classification: { kind } });
      const copy = defaultCopyFor(kind);
      expect(wrapper.find('[data-testid="error-title"]').text()).toBe(copy.title);
      expect(wrapper.find('[data-testid="error-hint"]').text()).toBe(copy.hint);
    });
  }

  it("uses titleOverride / hintOverride when the caller supplies them", () => {
    const wrapper = mountWith({
      classification: { kind: ERROR_KIND.GATED },
      titleOverride: "Log in to view this file",
      hintOverride: "Attach a token in Settings → Tokens.",
    });
    expect(wrapper.find('[data-testid="error-title"]').text()).toBe(
      "Log in to view this file",
    );
    expect(wrapper.find('[data-testid="error-hint"]').text()).toBe(
      "Attach a token in Settings → Tokens.",
    );
  });

  it("renders classification.detail beneath the hint when the two differ", () => {
    const wrapper = mountWith({
      classification: {
        kind: ERROR_KIND.UPSTREAM_UNAVAILABLE,
        detail: "connection reset at 127.0.0.1:29001",
      },
    });
    expect(wrapper.find('[data-testid="error-detail"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="error-detail"]').text()).toContain(
      "connection reset",
    );
  });

  it("does NOT render the detail row when detail equals the hint (no tautology)", () => {
    const copy = defaultCopyFor(ERROR_KIND.GATED);
    const wrapper = mountWith({
      classification: { kind: ERROR_KIND.GATED, detail: copy.hint },
    });
    expect(wrapper.find('[data-testid="error-detail"]').exists()).toBe(false);
  });

  it("wires the retry slot default: renders a Retry button that calls the callback", async () => {
    const retry = vi.fn();
    const wrapper = mountWith({
      classification: { kind: ERROR_KIND.UPSTREAM_UNAVAILABLE },
      retry,
    });
    const btn = wrapper.findAll("button").find((b) => b.text() === "Retry");
    expect(btn).toBeTruthy();
    await btn.trigger("click");
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("does not render a Retry button when no callback is provided", () => {
    const wrapper = mountWith({
      classification: { kind: ERROR_KIND.NOT_FOUND },
    });
    expect(
      wrapper.findAll("button").some((b) => b.text() === "Retry"),
    ).toBe(false);
  });

  it("renders the sources[] disclosure when classification carries per-source attempts", () => {
    const wrapper = mountWith({
      classification: {
        kind: ERROR_KIND.GATED,
        sources: [
          {
            name: "HuggingFace",
            url: "https://hf",
            status: 401,
            category: "auth",
            message: "restricted",
          },
          {
            name: "Mirror",
            url: "https://m",
            status: null,
            category: "timeout",
            message: "slow",
          },
        ],
      },
    });
    const text = wrapper.text();
    expect(text).toContain("Fallback sources tried (2)");
    expect(text).toContain("HuggingFace");
    expect(text).toContain("Mirror");
    expect(text).toContain("401");
    // `null` status surfaces as "-" so the table never renders a
    // bare "null" token.
    expect(text).toMatch(/-/);
  });

  it("falls back on missing source fields when rendering the disclosure table", () => {
    // Every field uses a `??` / `typeof === 'string' ? ... : ''` guard
    // so a partial source object (e.g. backend adding a new field or
    // a future test fixture omitting `url`/`status`) never renders a
    // bare "undefined" or blows up the table.
    const wrapper = mountWith({
      classification: {
        kind: ERROR_KIND.GATED,
        sources: [
          {}, // totally empty
          { name: "With-only-name" },
          { status: 500 }, // status-only
        ],
      },
    });
    const text = wrapper.text();
    expect(text).toContain("Fallback sources tried (3)");
    // Name column falls back to "(unknown)" / renders the explicit
    // name when given, and status column renders "-" when missing.
    expect(text).toContain("(unknown)");
    expect(text).toContain("With-only-name");
    expect(text).toContain("500");
    // Ensure no bare "undefined" snuck through any column.
    expect(text).not.toContain("undefined");
  });

  it("omits the disclosure when sources is null / empty / non-array", () => {
    for (const sources of [null, undefined, [], "oops"]) {
      const wrapper = mountWith({
        classification: { kind: ERROR_KIND.NOT_FOUND, sources },
      });
      expect(wrapper.text()).not.toContain("Fallback sources tried");
    }
  });

  it("accepts a custom actions slot that replaces the default Retry button", () => {
    const wrapper = mount(ErrorState, {
      props: {
        classification: { kind: ERROR_KIND.GATED },
        retry: () => {},
      },
      slots: {
        actions: `<button type="button">Open Settings</button>`,
      },
      global: { stubs },
    });
    // The slot replaces the default content including the Retry button.
    expect(
      wrapper.findAll("button").map((b) => b.text()),
    ).toContain("Open Settings");
    expect(
      wrapper.findAll("button").some((b) => b.text() === "Retry"),
    ).toBe(false);
  });

  it("applies full-page vs inline-panel container sizing", () => {
    const full = mountWith({
      classification: { kind: ERROR_KIND.NOT_FOUND },
      mode: "full-page",
    });
    const inline = mountWith({
      classification: { kind: ERROR_KIND.NOT_FOUND },
      mode: "inline-panel",
    });
    // Full-page uses the py-16 class, inline uses py-8.
    expect(full.html()).toContain("py-16");
    expect(inline.html()).toContain("py-8");
  });

  it("picks the right icon class per kind", () => {
    const cases = [
      [ERROR_KIND.GATED, "i-carbon-locked"],
      [ERROR_KIND.FORBIDDEN, "i-carbon-misuse"],
      [ERROR_KIND.NOT_FOUND, "i-carbon-document-unknown"],
      [ERROR_KIND.UPSTREAM_UNAVAILABLE, "i-carbon-cloud-offline"],
      [ERROR_KIND.CORS, "i-carbon-warning-alt"],
      [ERROR_KIND.GENERIC, "i-carbon-warning-alt"],
    ];
    for (const [kind, expected] of cases) {
      const wrapper = mountWith({ classification: { kind } });
      const icon = wrapper.find('[data-testid="error-icon"]');
      expect(icon.classes()).toContain(expected);
    }
  });
});
