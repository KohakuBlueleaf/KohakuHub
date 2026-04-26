import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";
import { createMemoryHistory, createRouter } from "@/testing/router";

vi.mock("@/components/repo/FileUploader.vue", () => ({
  default: {
    name: "FileUploader",
    emits: ["upload-success", "upload-error"],
    template: `
      <div data-file-uploader="true">
        <button type="button" data-action="success" @click="$emit('upload-success')">
          Upload success
        </button>
        <button
          type="button"
          data-action="error"
          @click="$emit('upload-error', { response: { data: { detail: 'Upload failed badly' } } })"
        >
          Upload error
        </button>
      </div>
    `,
  },
}));

import { useAuthStore } from "@/stores/auth";
import UploadPage from "@/pages/[type]s/[namespace]/[name]/upload/[branch].vue";

describe("upload page", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  async function createTestRouter(initialPath) {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/datasets/:namespace/:name/upload/:branch",
          component: { template: "<div />" },
        },
        {
          path: "/datasets/:namespace/:name",
          component: { template: "<div />" },
        },
        { path: "/:pathMatch(.*)*", component: { template: "<div />" } },
      ],
    });

    await router.push(initialPath);
    await router.isReady();
    return router;
  }

  function mountPage(router) {
    return mount(UploadPage, {
      global: {
        plugins: [router],
        stubs: {
          ...ElementPlusStubs,
          RouterLink: RouterLinkStub,
        },
      },
    });
  }

  it("redirects visitors who are not allowed to upload", async () => {
    const router = await createTestRouter(
      "/datasets/aurora-labs/vision-set/upload/main",
    );
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.user = null;

    const wrapper = mountPage(router);
    await flushPromises();

    expect(wrapper.text()).toContain("Upload Files");
    expect(pushSpy).toHaveBeenCalledWith(
      "/datasets/aurora-labs/vision-set",
    );
  });

  it("handles upload success and back navigation for authorized users", async () => {
    const router = await createTestRouter(
      "/datasets/aurora-labs/vision-set/upload/main",
    );
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.user = { username: "mai_lin" };
    authStore.userOrganizations = [{ name: "aurora-labs" }];

    const wrapper = mountPage(router);
    await flushPromises();

    await wrapper.get('button[data-action="success"]').trigger("click");
    await vi.advanceTimersByTimeAsync(1000);
    expect(pushSpy).toHaveBeenCalledWith(
      "/datasets/aurora-labs/vision-set",
    );

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Back to Repository"))
      .trigger("click");
    expect(pushSpy).toHaveBeenCalledWith(
      "/datasets/aurora-labs/vision-set",
    );
  });
});
