// Component tests for TarBrowserDialog.vue.
//
// The dialog is a thin wrapper: <el-dialog> around <TarBrowserPanel>.
// What matters here is the visibility contract — the panel must NOT
// be mounted while the dialog is closed (so it doesn't fire a
// background fetch the user never asked for), it must mount when
// `visible` flips to true, and v-model:visible must round-trip
// through the Close button.

import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { describe, expect, it } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";

import TarBrowserDialog from "@/components/repo/preview/TarBrowserDialog.vue";

// Replace the panel with a presence-detector so we can assert
// mount/unmount transitions without driving real fetch + parsing.
// The stub also exposes the props the dialog forwards, so we can
// assert the wiring is intact.
const TarBrowserPanelStub = defineComponent({
  name: "TarBrowserPanel",
  props: ["tarUrl", "indexUrl", "filename", "tarTreeEntry"],
  setup(props) {
    return () =>
      h("div", {
        "data-stub": "TarBrowserPanel",
        "data-tar-url": props.tarUrl,
        "data-index-url": props.indexUrl,
        "data-filename": props.filename,
        "data-has-tree-entry": props.tarTreeEntry ? "true" : "false",
      });
  },
});

function mountDialog(props = {}) {
  return mount(TarBrowserDialog, {
    props: {
      visible: true,
      tarUrl: "https://x.test/archive.tar",
      indexUrl: "https://x.test/archive.json",
      filename: "archive.tar",
      tarTreeEntry: { type: "file", oid: "abc" },
      ...props,
    },
    global: {
      stubs: { ...ElementPlusStubs, TarBrowserPanel: TarBrowserPanelStub },
    },
  });
}

describe("TarBrowserDialog", () => {
  it("renders the panel only while visible is true", async () => {
    const wrapper = mountDialog({ visible: false });
    expect(wrapper.find('[data-stub="TarBrowserPanel"]').exists()).toBe(false);

    await wrapper.setProps({ visible: true });
    expect(wrapper.find('[data-stub="TarBrowserPanel"]').exists()).toBe(true);

    await wrapper.setProps({ visible: false });
    expect(wrapper.find('[data-stub="TarBrowserPanel"]').exists()).toBe(false);
  });

  it("forwards every backing prop to TarBrowserPanel", () => {
    const wrapper = mountDialog();
    const panel = wrapper.find('[data-stub="TarBrowserPanel"]');
    expect(panel.attributes("data-tar-url")).toBe(
      "https://x.test/archive.tar",
    );
    expect(panel.attributes("data-index-url")).toBe(
      "https://x.test/archive.json",
    );
    expect(panel.attributes("data-filename")).toBe("archive.tar");
    expect(panel.attributes("data-has-tree-entry")).toBe("true");
  });

  it("emits update:visible(false) when the Close button is clicked", async () => {
    const wrapper = mountDialog();
    const closeBtn = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Close");
    expect(closeBtn).toBeDefined();
    await closeBtn.trigger("click");
    const events = wrapper.emitted("update:visible");
    expect(events).toBeTruthy();
    expect(events[events.length - 1]).toEqual([false]);
  });

  it("renders the el-dialog title with the .tar filename in the prefix slot", () => {
    const wrapper = mountDialog({ filename: "archives/models/bundle.tar" });
    // The ElementPlusStubs ElDialog renders the title prop as an h2.
    expect(wrapper.text()).toContain(
      "Indexed tar · archives/models/bundle.tar",
    );
  });
});
