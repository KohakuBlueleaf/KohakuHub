import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";

const mocks = vi.hoisted(() => ({
  repoApi: {
    uploadFiles: vi.fn(),
  },
  elMessage: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/utils/api", () => ({
  repoAPI: mocks.repoApi,
}));

vi.mock("element-plus", () => ({
  ElMessage: mocks.elMessage,
}));

import FileUploader from "@/components/repo/FileUploader.vue";

describe("FileUploader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  function mountUploader() {
    return mount(FileUploader, {
      props: {
        repoType: "model",
        namespace: "alice",
        name: "demo",
        branch: "main",
      },
      global: {
        stubs: ElementPlusStubs,
      },
    });
  }

  async function selectFiles(wrapper, files) {
    const input = wrapper.get('input[type="file"]');
    Object.defineProperty(input.element, "files", {
      value: files,
      configurable: true,
    });
    await input.trigger("change");
  }

  it("collects files, reports progress, uploads successfully, and clears the queue", async () => {
    vi.useFakeTimers();

    mocks.repoApi.uploadFiles.mockImplementation(
      async (_type, _namespace, _name, _branch, payload, callbacks) => {
        expect(payload.message).toBe("Upload files via web interface");
        expect(payload.files).toHaveLength(1);
        callbacks.onHashProgress("notes.md", 0.25);
        callbacks.onHashProgress("notes.md", 1);
        callbacks.onUploadProgress("notes.md", 0.5);
        callbacks.onUploadProgress("notes.md", 1);
        return { data: { commitOid: "abc123" } };
      },
    );

    const wrapper = mountUploader();
    await selectFiles(
      wrapper,
      [new File(["hello"], "notes.md", { type: "text/plain" })],
    );

    expect(wrapper.text()).toContain("Files to Upload (1)");
    expect(wrapper.text()).toContain("notes.md");

    const uploadButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Upload Files"));
    await uploadButton.trigger("click");
    await flushPromises();

    expect(mocks.repoApi.uploadFiles).toHaveBeenCalledWith(
      "model",
      "alice",
      "demo",
      "main",
      expect.objectContaining({
        description: "",
        message: "Upload files via web interface",
      }),
      expect.objectContaining({
        onHashProgress: expect.any(Function),
        onUploadProgress: expect.any(Function),
      }),
    );
    expect(wrapper.emitted("upload-success")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();

    expect(wrapper.text()).not.toContain("Files to Upload (1)");
  });

  it("surfaces upload failures and supports clearing queued files", async () => {
    mocks.repoApi.uploadFiles.mockRejectedValue({
      response: {
        data: {
          detail: "Upload failed badly",
        },
      },
    });

    const wrapper = mountUploader();
    await selectFiles(
      wrapper,
      [new File(["hello"], "notes.md", { type: "text/plain" })],
    );

    const clearButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Clear All"));
    await clearButton.trigger("click");
    expect(wrapper.text()).not.toContain("Files to Upload (1)");

    await selectFiles(
      wrapper,
      [new File(["retry"], "retry.md", { type: "text/plain" })],
    );

    const uploadButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Upload Files"));
    await uploadButton.trigger("click");
    await flushPromises();

    expect(wrapper.emitted("upload-error")).toHaveLength(1);
    expect(wrapper.text()).toContain("Upload Files");
  });
});
