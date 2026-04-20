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
    expect(pushMock).toHaveBeenCalledWith("/models/mai_lin/lineart-caption-base");
  });

  it("renders captured dataset and space overview entries from the backend", () => {
    const datasetWrapper = mountRepoList(userOverview.datasets, "dataset");
    expect(datasetWrapper.text()).toContain("mai_lin/street-sign-zh-en");
    expect(datasetWrapper.text()).toContain("9");
    expect(datasetWrapper.text()).toContain("2");
    expect(datasetWrapper.get("[data-router-link='true']").attributes("href")).toBe(
      "/datasets/mai_lin/street-sign-zh-en",
    );

    const spaceWrapper = mountRepoList(userOverview.spaces, "space");
    expect(spaceWrapper.text()).toContain("mai_lin/mai_lin");
    expect(spaceWrapper.get("[data-router-link='true']").attributes("href")).toBe(
      "/spaces/mai_lin/mai_lin",
    );
  });
});
