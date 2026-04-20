import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";

import LanguageCard from "@/components/repo/metadata/LanguageCard.vue";
import LicenseCard from "@/components/repo/metadata/LicenseCard.vue";

describe("metadata cards", () => {
  it("renders language tags using friendly names", () => {
    const wrapper = mount(LanguageCard, {
      props: {
        languages: ["en", "zh"],
      },
      global: {
        stubs: ElementPlusStubs,
      },
    });

    const tags = wrapper.findAll("[data-el-tag='true']");
    expect(tags).toHaveLength(2);
    expect(wrapper.text()).toContain("English");
    expect(wrapper.text()).toContain("Chinese");
  });

  it("handles invalid language collections gracefully", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = mount(LanguageCard, {
      props: {
        languages: "en",
      },
      global: {
        stubs: ElementPlusStubs,
      },
    });

    expect(wrapper.text()).toContain("Language");
    expect(wrapper.findAll("[data-el-tag='true']")).toHaveLength(0);
    warnSpy.mockRestore();
  });

  it("renders license name and fallback link logic", () => {
    const wrapper = mount(LicenseCard, {
      props: {
        metadata: {
          license: "mit",
        },
      },
    });

    expect(wrapper.text()).toContain("MIT License");
    expect(wrapper.get("a").attributes("href")).toBe(
      "https://opensource.org/licenses/MIT",
    );

    const customWrapper = mount(LicenseCard, {
      props: {
        metadata: {
          license_name: "Custom License",
          license_link: "https://example.com/license",
        },
      },
    });

    expect(customWrapper.text()).toContain("Custom License");
    expect(customWrapper.get("a").attributes("href")).toBe(
      "https://example.com/license",
    );

    const unknownWrapper = mount(LicenseCard, {
      props: {
        metadata: {},
      },
    });

    expect(unknownWrapper.text()).toContain("Unknown");
    expect(unknownWrapper.find("a").exists()).toBe(false);
  });
});
