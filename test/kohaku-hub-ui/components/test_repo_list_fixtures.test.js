import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";
import repoInfo from "../fixtures/repo-info.json";
import repoStats from "../fixtures/repo-stats.json";
import userOverview from "../fixtures/user-overview.json";

import RepoList from "@/components/repo/RepoList.vue";

const pushMock = vi.fn();

vi.mock("vue-router/auto", () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ params: {}, query: {} }),
}));

describe("RepoList captured backend fixtures", () => {
  beforeEach(() => {
    pushMock.mockReset();
  });

  function mountRepoList(repos, type) {
    return mount(RepoList, {
      props: { repos, type },
      global: {
        stubs: {
          ...ElementPlusStubs,
          RouterLink: RouterLinkStub,
        },
      },
    });
  }

  it("renders captured model repository data from the backend", async () => {
    const wrapper = mountRepoList([repoInfo], "model");

    expect(wrapper.text()).toContain("mai_lin/lineart-caption-base");
    expect(wrapper.text()).toContain("by mai_lin");
    expect(wrapper.text()).toContain(String(repoStats.downloads));
    expect(wrapper.text()).toContain(String(repoStats.likes));

    await wrapper.get(".card").trigger("click");
    expect(pushMock).toHaveBeenCalledWith(
      "/models/mai_lin/lineart-caption-base",
    );
  });

  it("renders captured dataset and space overview entries from the backend", () => {
    const datasetWrapper = mountRepoList(userOverview.datasets, "dataset");
    expect(datasetWrapper.text()).toContain("mai_lin/street-sign-zh-en");
    expect(datasetWrapper.text()).toContain("9");
    expect(datasetWrapper.text()).toContain("2");
    expect(
      datasetWrapper.get("[data-router-link='true']").attributes("href"),
    ).toBe("/datasets/mai_lin/street-sign-zh-en");

    const spaceWrapper = mountRepoList(userOverview.spaces, "space");
    expect(spaceWrapper.text()).toContain("mai_lin/mai_lin");
    expect(
      spaceWrapper.get("[data-router-link='true']").attributes("href"),
    ).toBe("/spaces/mai_lin/mai_lin");
  });

  it("renders private repository badges and empty states", () => {
    const privateWrapper = mountRepoList(
      [
        {
          ...repoInfo,
          id: "mai_lin/private-demo",
          private: true,
          lastModified: null,
        },
      ],
      "model",
    );

    expect(privateWrapper.text()).toContain("Private");
    expect(privateWrapper.text()).not.toContain("Updated");

    const emptyWrapper = mountRepoList([], "model");
    expect(emptyWrapper.text()).toContain("No repositories found");
  });

  it("renders updated timestamps and tag previews", () => {
    const wrapper = mountRepoList(
      [
        {
          ...repoInfo,
          id: "mai_lin/tagged-model",
          lastModified: "2026-04-20T08:47:31.616317Z",
          tags: ["vision", "captioning", "english"],
        },
      ],
      "model",
    );

    expect(wrapper.text()).toContain("Updated");
    expect(wrapper.text()).toContain("vision");
    expect(wrapper.text()).toContain("captioning");
    expect(wrapper.text()).toContain("english");
  });

  it("falls back to the model icon mapping for unknown repository types", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = mountRepoList([repoInfo], "unknown");

    expect(wrapper.find(".i-carbon-model").exists()).toBe(true);
    warnSpy.mockRestore();
  });
});
