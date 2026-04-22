import { flushPromises, mount } from "@vue/test-utils";
import { http, HttpResponse } from "@/testing/msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";
import {
  cloneFixture,
  jsonResponse,
  readJsonBody,
  readNdjsonBody,
  uiApiFixtures,
} from "../helpers/api-fixtures";
import { server } from "../setup/msw-server";

const mocks = vi.hoisted(() => ({
  calculateSHA256: vi.fn(),
  uploadLFSFile: vi.fn(),
  elMessage: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/utils/lfs.js", () => ({
  calculateSHA256: mocks.calculateSHA256,
  uploadLFSFile: mocks.uploadLFSFile,
  formatFileSize: (size) => `${size} B`,
}));

vi.mock("element-plus", () => ({
  ElMessage: mocks.elMessage,
}));

import FileUploader from "@/components/repo/FileUploader.vue";

describe("FileUploader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();

    vi.stubGlobal(
      "FileReader",
      class FakeFileReader {
        onload = null;
        onerror = null;

        readAsDataURL(file) {
          const type = file?.type || "application/octet-stream";
          this.onload?.({
            target: {
              result: `data:${type};base64,aGVsbG8=`,
            },
          });
        }
      },
    );
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

  it("uploads files through fixture-backed API routes and clears the queue", async () => {
    vi.useFakeTimers();
    mocks.calculateSHA256.mockImplementation(async (_file, onProgress) => {
      onProgress?.(0.5);
      onProgress?.(1);
      return "fixture-sha";
    });

    server.use(
      http.post("*/api/models/alice/demo/preupload/main", async ({ request }) => {
        const body = await readJsonBody(request);
        expect(body).toEqual({
          files: [
            {
              path: "notes.md",
              size: 5,
              sha256: "fixture-sha",
            },
          ],
        });

        return jsonResponse(uiApiFixtures.repo.preuploadRegular);
      }),
      http.post("*/api/models/alice/demo/commit/main", async ({ request }) => {
        expect(request.headers.get("content-type")).toContain(
          "application/x-ndjson",
        );

        const body = await readNdjsonBody(request);
        expect(body).toEqual([
          {
            key: "header",
            value: {
              summary: "Upload files via web interface",
              description: "",
            },
          },
          {
            key: "file",
            value: {
              path: "notes.md",
              content: "aGVsbG8=",
              encoding: "base64",
            },
          },
        ]);

        return jsonResponse(uiApiFixtures.repo.commitCreated);
      }),
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

    expect(mocks.calculateSHA256).toHaveBeenCalledTimes(1);
    expect(mocks.uploadLFSFile).not.toHaveBeenCalled();
    expect(wrapper.emitted("upload-success")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1000);
    await flushPromises();

    expect(wrapper.text()).not.toContain("Files to Upload (1)");
  });

  it("surfaces fixture-backed upload failures and supports clearing queued files", async () => {
    mocks.calculateSHA256.mockResolvedValue("fixture-sha");

    server.use(
      http.post("*/api/models/alice/demo/preupload/main", async ({ request }) => {
        const body = await readJsonBody(request);
        const response = cloneFixture(uiApiFixtures.repo.preuploadRegular);
        response.files[0].path = body.files[0].path;
        return jsonResponse(response);
      }),
      http.post("*/api/models/alice/demo/commit/main", () =>
        HttpResponse.json(
          { detail: "Upload failed badly" },
          { status: 500 },
        ),
      ),
    );

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

    expect(mocks.calculateSHA256).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("upload-error")).toHaveLength(1);
    expect(wrapper.text()).toContain("Upload Files");
  });
});
