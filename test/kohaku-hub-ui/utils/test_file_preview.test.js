import { describe, expect, it } from "vitest";

import {
  buildResolveUrl,
  canPreviewFile,
  getPreviewKind,
} from "@/utils/file-preview";

describe("file-preview helpers", () => {
  describe("getPreviewKind", () => {
    it("recognizes .safetensors and .parquet by suffix", () => {
      expect(getPreviewKind("model.safetensors")).toBe("safetensors");
      expect(getPreviewKind("weights/shard-01.safetensors")).toBe(
        "safetensors",
      );
      expect(getPreviewKind("data/train-00000-of-00001.parquet")).toBe(
        "parquet",
      );
    });

    it("is case-insensitive", () => {
      expect(getPreviewKind("MODEL.SAFETENSORS")).toBe("safetensors");
      expect(getPreviewKind("TRAIN.Parquet")).toBe("parquet");
    });

    it("returns null for non-preview file types", () => {
      expect(getPreviewKind("README.md")).toBeNull();
      expect(getPreviewKind("config.json")).toBeNull();
      expect(getPreviewKind("model.bin")).toBeNull();
      expect(getPreviewKind("archive.tar.gz")).toBeNull();
    });

    it("returns null for bad inputs", () => {
      expect(getPreviewKind("")).toBeNull();
      expect(getPreviewKind(null)).toBeNull();
      expect(getPreviewKind(undefined)).toBeNull();
      expect(getPreviewKind(42)).toBeNull();
    });
  });

  describe("canPreviewFile", () => {
    it("accepts files with preview-capable extensions", () => {
      expect(canPreviewFile({ type: "file", path: "model.safetensors" })).toBe(
        true,
      );
      expect(canPreviewFile({ type: "file", path: "train.parquet" })).toBe(
        true,
      );
    });

    it("rejects directories even if the name ends with a known suffix", () => {
      expect(
        canPreviewFile({ type: "directory", path: "safetensors" }),
      ).toBe(false);
      expect(
        canPreviewFile({ type: "directory", path: "my.parquet" }),
      ).toBe(false);
    });

    it("rejects files with non-preview extensions", () => {
      expect(canPreviewFile({ type: "file", path: "README.md" })).toBe(false);
    });

    it("rejects bad inputs defensively", () => {
      expect(canPreviewFile(null)).toBe(false);
      expect(canPreviewFile(undefined)).toBe(false);
      expect(canPreviewFile("not-an-object")).toBe(false);
      expect(canPreviewFile({ type: "file" })).toBe(false);
    });
  });

  describe("buildResolveUrl", () => {
    it("builds a /resolve/ URL encoded segment-by-segment", () => {
      const url = buildResolveUrl({
        baseUrl: "http://localhost:5173",
        repoType: "model",
        namespace: "open-media-lab",
        name: "vision-language-assistant-3b",
        branch: "main",
        path: "fixtures/hf-tiny-random-bert.safetensors",
      });
      expect(url).toBe(
        "http://localhost:5173/models/open-media-lab/vision-language-assistant-3b/resolve/main/fixtures/hf-tiny-random-bert.safetensors",
      );
    });

    it("percent-encodes each path segment individually so slashes in the path stay intact", () => {
      const url = buildResolveUrl({
        baseUrl: "http://hub.example.com",
        repoType: "dataset",
        namespace: "user",
        name: "set",
        branch: "main",
        path: "data/weird file name (1).parquet",
      });
      expect(url).toBe(
        "http://hub.example.com/datasets/user/set/resolve/main/data/weird%20file%20name%20(1).parquet",
      );
    });

    it("percent-encodes the branch name so refs/convert/parquet-style branches survive", () => {
      const url = buildResolveUrl({
        baseUrl: "http://hub.example.com",
        repoType: "dataset",
        namespace: "ns",
        name: "ds",
        branch: "refs/convert/parquet",
        path: "a.parquet",
      });
      expect(url).toContain("/resolve/refs%2Fconvert%2Fparquet/a.parquet");
    });

    it("throws when any required field is missing", () => {
      const valid = {
        baseUrl: "http://x",
        repoType: "model",
        namespace: "a",
        name: "b",
        branch: "main",
        path: "c",
      };
      for (const key of Object.keys(valid)) {
        expect(() =>
          buildResolveUrl({ ...valid, [key]: undefined }),
        ).toThrow(/buildResolveUrl/);
      }
    });
  });
});
