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

    it("returns null for a .tar file with no listing siblings", () => {
      // Bare .tar files are extremely common; the icon should stay
      // dark unless the same listing carries a sibling .json that
      // looks like an hfutils.index sidecar.
      expect(getPreviewKind("archives/bundle.tar")).toBeNull();
    });

    it("returns 'indexed-tar' when the listing contains a sibling .json", () => {
      const siblings = [
        { type: "file", path: "archives/bundle.tar" },
        { type: "file", path: "archives/bundle.json" },
      ];
      expect(getPreviewKind("archives/bundle.tar", siblings)).toBe(
        "indexed-tar",
      );
    });

    it("does not light up bare .tar even when an unrelated .json exists", () => {
      const siblings = [
        { type: "file", path: "archives/bundle.tar" },
        { type: "file", path: "archives/something-else.json" },
      ];
      expect(getPreviewKind("archives/bundle.tar", siblings)).toBeNull();
    });

    it("returns 'indexed-tar' when confirmedTarPaths carries the path (sidecar fell off this page)", () => {
      // Paginated file list path: the .json sibling lives on a different
      // page so the loaded slice doesn't see it. RepoViewer probes the
      // backend with HEAD and stores the confirmed tar path here.
      const confirmed = new Set(["archives/bundle.tar"]);
      expect(
        getPreviewKind("archives/bundle.tar", [], confirmed),
      ).toBe("indexed-tar");
    });

    it("loaded-listing hit wins even if confirmedTarPaths is missing", () => {
      // The two arguments are ORed — the loaded listing is the cheap
      // path; the confirmed set is the fallback that picks up where
      // pagination drops the sidecar.
      const siblings = [
        { type: "file", path: "archives/bundle.tar" },
        { type: "file", path: "archives/bundle.json" },
      ];
      expect(
        getPreviewKind("archives/bundle.tar", siblings, new Set()),
      ).toBe("indexed-tar");
    });

    it("ignores confirmedTarPaths for non-tar files", () => {
      const confirmed = new Set(["archives/bundle.tar", "config.json"]);
      expect(getPreviewKind("config.json", [], confirmed)).toBeNull();
    });

    it("tolerates a non-Set confirmedTarPaths argument without throwing", () => {
      // Defensive: callers may forget to seed the prop in tests; the
      // helper should treat anything without a `.has` method as empty.
      expect(getPreviewKind("archives/bundle.tar", [], {})).toBeNull();
      expect(getPreviewKind("archives/bundle.tar", [], null)).toBeNull();
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

    it("rejects bare .tar without a sibling .json", () => {
      expect(canPreviewFile({ type: "file", path: "archive.tar" })).toBe(
        false,
      );
    });

    it("accepts .tar with a sibling .json passed via siblings", () => {
      const siblings = [
        { type: "file", path: "archive.tar" },
        { type: "file", path: "archive.json" },
      ];
      expect(
        canPreviewFile({ type: "file", path: "archive.tar" }, siblings),
      ).toBe(true);
    });

    it("accepts .tar when only confirmedTarPaths backs the kind decision", () => {
      // Mirrors the paginated-listing case where the .json sibling is
      // on a different page and the icon was unlocked by a HEAD probe.
      const confirmed = new Set(["archive.tar"]);
      expect(
        canPreviewFile(
          { type: "file", path: "archive.tar" },
          [],
          confirmed,
        ),
      ).toBe(true);
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
