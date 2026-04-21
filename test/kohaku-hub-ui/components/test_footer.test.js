import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TheFooter from "@/components/layout/TheFooter.vue";

describe("TheFooter", () => {
  it("renders primary navigation and community links", () => {
    const wrapper = mount(TheFooter);
    const hrefs = wrapper.findAll("a").map((link) => link.attributes("href"));

    expect(wrapper.text()).toContain("Self-hosted HuggingFace Hub alternative");
    expect(hrefs).toEqual(
      expect.arrayContaining([
        "/docs",
        "/about",
        "/get-started",
        "/self-hosted",
        "/terms",
        "/privacy",
        "https://github.com/KohakuBlueleaf/KohakuHub",
        "https://discord.gg/xWYrkyvJ2s",
      ]),
    );
  });
});
