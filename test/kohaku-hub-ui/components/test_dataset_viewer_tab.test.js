import { flushPromises, mount } from "@vue/test-utils";
import { http } from "@/testing/msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";
import { jsonResponse } from "../helpers/api-fixtures";
import { server } from "../setup/msw-server";

const viewerProps = [];

vi.mock("@/components/DatasetViewer/DatasetViewer.vue", () => ({
  default: {
    name: "DatasetViewerStub",
    props: ["fileUrl", "fileName", "maxRows", "sqlQuery", "useSQL"],
    template: "<div data-dataset-viewer-stub=\"true\">viewer</div>",
    watch: {
      fileUrl: {
        immediate: true,
        handler(value) {
          viewerProps.push({
            fileUrl: value,
            fileName: this.fileName,
          });
        },
      },
    },
  },
}));

import DatasetViewerTab from "@/components/repo/DatasetViewerTab.vue";

describe("DatasetViewerTab", () => {
  beforeEach(() => {
    viewerProps.length = 0;
    vi.clearAllMocks();
  });

  function mountViewerTab() {
    return mount(DatasetViewerTab, {
      props: {
        repoType: "dataset",
        namespace: "open-media-lab",
        name: "multimodal-benchmark-suite",
        branch: "main",
        files: [
          {
            type: "directory",
            path: "parquet/",
            size: 0,
          },
        ],
      },
      global: {
        stubs: ElementPlusStubs,
      },
    });
  }

  it("keeps folder tree paths repository-relative when previewing nested parquet files", async () => {
    const requests = {
      head: [],
    };

    server.use(
      http.get(
        "/api/datasets/open-media-lab/multimodal-benchmark-suite/tree/main/parquet/",
        () =>
          jsonResponse([
            {
              type: "file",
              path: "parquet/train-00000-of-00001.parquet",
              size: 123,
            },
          ]),
      ),
      http.head(
        "/datasets/open-media-lab/multimodal-benchmark-suite/resolve/main/:path+",
        ({ params, request }) => {
          requests.head.push({
            path: Array.isArray(params.path)
              ? params.path.join("/")
              : params.path,
            url: request.url,
          });
          return new Response(null, {
            status: 200,
            headers: {
              "Content-Type": "application/octet-stream",
            },
          });
        },
      ),
    );

    const wrapper = mountViewerTab();
    await flushPromises();

    const folderRow = wrapper
      .findAll(".folder-item")
      .find((node) => node.text().includes("parquet"));
    expect(folderRow).toBeTruthy();

    await folderRow.trigger("click");
    await flushPromises();
    await flushPromises();

    const fileRow = wrapper
      .findAll(".file-item")
      .find((node) => node.text().includes("train-00000-of-00001.parquet"));
    expect(fileRow).toBeTruthy();

    await fileRow.trigger("click");
    await flushPromises();
    await flushPromises();

    expect(requests.head).toEqual([
      {
        path: "parquet/train-00000-of-00001.parquet",
        url: "http://localhost:3000/datasets/open-media-lab/multimodal-benchmark-suite/resolve/main/parquet/train-00000-of-00001.parquet",
      },
    ]);
    expect(viewerProps.at(-1)).toEqual({
      fileUrl: "http://localhost:3000/datasets/open-media-lab/multimodal-benchmark-suite/resolve/main/parquet/train-00000-of-00001.parquet",
      fileName: "parquet/train-00000-of-00001.parquet",
    });
  });
});
