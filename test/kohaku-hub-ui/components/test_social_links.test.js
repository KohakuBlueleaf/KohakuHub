import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SocialLinks from "@/components/profile/SocialLinks.vue";

describe("SocialLinks", () => {
  it("renders nothing when social data is missing or empty", () => {
    const wrapper = mount(SocialLinks, {
      props: {
        socialMedia: {},
      },
    });

    expect(wrapper.html()).toContain("<!--v-if-->");

    const nullWrapper = mount(SocialLinks, {
      props: {
        socialMedia: null,
      },
    });

    expect(nullWrapper.html()).toContain("<!--v-if-->");
  });

  it("renders labeled links for supported platforms", () => {
    const wrapper = mount(SocialLinks, {
      props: {
        socialMedia: {
          twitter_x: "owner_x",
          github: "owner-gh",
          huggingface: "owner-hf",
        },
      },
    });

    const links = wrapper.findAll("a");
    expect(links).toHaveLength(3);
    expect(links[0].attributes("href")).toBe("https://twitter.com/owner_x");
    expect(links[1].attributes("href")).toBe("https://github.com/owner-gh");
    expect(links[2].attributes("href")).toBe("https://huggingface.co/owner-hf");
    expect(wrapper.text()).toContain("@owner_x");
    expect(wrapper.text()).toContain("owner-gh");
    expect(wrapper.text()).toContain("owner-hf");
  });

  it.each([
    [
      { threads: "owner_threads" },
      "https://www.threads.net/@owner_threads",
      "Threads",
      "@owner_threads",
    ],
    [
      { github: "owner-gh" },
      "https://github.com/owner-gh",
      "GitHub",
      "owner-gh",
    ],
    [
      { huggingface: "owner-hf" },
      "https://huggingface.co/owner-hf",
      "HuggingFace",
      "owner-hf",
    ],
  ])(
    "renders platform names when labels are disabled for %o",
    (socialMedia, href, label, hiddenText) => {
      const wrapper = mount(SocialLinks, {
        props: {
          socialMedia,
          showLabels: false,
        },
      });

      expect(wrapper.get("a").attributes("href")).toBe(href);
      expect(wrapper.text()).toContain(label);
      expect(wrapper.text()).not.toContain(hiddenText);
    },
  );

  it("evaluates each social platform branch independently", () => {
    const githubWrapper = mount(SocialLinks, {
      props: {
        socialMedia: {
          github: "owner-gh",
        },
      },
    });

    const hfWrapper = mount(SocialLinks, {
      props: {
        socialMedia: {
          huggingface: "owner-hf",
        },
      },
    });

    expect(githubWrapper.text()).toContain("owner-gh");
    expect(hfWrapper.text()).toContain("owner-hf");
  });

  it("renders labeled thread handles when labels are enabled", () => {
    const wrapper = mount(SocialLinks, {
      props: {
        socialMedia: {
          threads: "owner_threads",
        },
      },
    });

    expect(wrapper.text()).toContain("@owner_threads");
  });

  it("renders the Twitter/X fallback label when labels are disabled", () => {
    const wrapper = mount(SocialLinks, {
      props: {
        socialMedia: {
          twitter_x: "owner_x",
        },
        showLabels: false,
      },
    });

    expect(wrapper.text()).toContain("Twitter/X");
    expect(wrapper.text()).not.toContain("@owner_x");
  });
});
