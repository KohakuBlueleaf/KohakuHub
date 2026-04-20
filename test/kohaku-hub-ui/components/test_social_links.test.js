import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SocialLinks from "@/components/profile/SocialLinks.vue";

describe("SocialLinks", () => {
  it("renders nothing when there are no social links", () => {
    const wrapper = mount(SocialLinks, {
      props: {
        socialMedia: {},
      },
    });

    expect(wrapper.html()).toContain("<!--v-if-->");
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

  it("renders platform names when labels are disabled", () => {
    const wrapper = mount(SocialLinks, {
      props: {
        socialMedia: {
          threads: "owner_threads",
        },
        showLabels: false,
      },
    });

    expect(wrapper.text()).toContain("Threads");
    expect(wrapper.text()).not.toContain("@owner_threads");
  });
});
