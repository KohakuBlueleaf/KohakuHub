import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { server } from "../setup/msw-server";

import {
  IndexedTarFetchError,
  IndexedTarFormatError,
  buildMemberRangeHeader,
  buildTreeFromIndex,
  classifyMember,
  compareTarHash,
  downloadBytesAs,
  extractMemberBytes,
  guessMimeType,
  hasIndexSibling,
  hasIndexSiblingWithProbe,
  listDirectory,
  parseTarIndex,
  tarSidecarPath,
} from "@/utils/indexed-tar";

const INDEX_URL = "https://s3.test.local/bucket/archive.json";
const TAR_URL = "https://s3.test.local/bucket/archive.tar";

const SAMPLE_INDEX = {
  filesize: 4096,
  hash: "0123456789abcdef0123456789abcdef01234567",
  hash_lfs:
    "abcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabca",
  files: {
    "README.md": { offset: 512, size: 64, sha256: "aa" },
    "images/cover.png": { offset: 1024, size: 256, sha256: "bb" },
    "images/photos/forest.jpg": { offset: 1536, size: 512, sha256: "cc" },
    "audio/bell.wav": { offset: 2048, size: 128 },
    "docs/guide.md": { offset: 2304, size: 32 },
  },
};

describe("parseTarIndex", () => {
  it("fetches and validates a well-formed index JSON", async () => {
    server.use(
      http.get(INDEX_URL, () => HttpResponse.json(SAMPLE_INDEX)),
    );
    const phases = [];
    const payload = await parseTarIndex(INDEX_URL, {
      onProgress: (p) => phases.push(p),
    });
    expect(payload.filesize).toBe(4096);
    expect(Object.keys(payload.files)).toHaveLength(5);
    expect(phases).toEqual(["fetch", "parsing", "done"]);
  });

  it("raises IndexedTarFetchError on a non-2xx response", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        new HttpResponse("not found", { status: 404 }),
      ),
    );
    await expect(parseTarIndex(INDEX_URL)).rejects.toBeInstanceOf(
      IndexedTarFetchError,
    );
  });

  it("raises IndexedTarFormatError when the JSON has no files map", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        HttpResponse.json({ filesize: 1, hash: "", hash_lfs: "" }),
      ),
    );
    await expect(parseTarIndex(INDEX_URL)).rejects.toBeInstanceOf(
      IndexedTarFormatError,
    );
  });

  it("raises IndexedTarFormatError when an entry is missing offset", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        HttpResponse.json({
          filesize: 1,
          hash: "",
          hash_lfs: "",
          files: { broken: { size: 10 } },
        }),
      ),
    );
    await expect(parseTarIndex(INDEX_URL)).rejects.toBeInstanceOf(
      IndexedTarFormatError,
    );
  });

  it("raises IndexedTarFormatError when the response body is not JSON", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        new HttpResponse("not json{", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(parseTarIndex(INDEX_URL)).rejects.toBeInstanceOf(
      IndexedTarFormatError,
    );
  });

  it("raises IndexedTarFormatError when the JSON root is an array", async () => {
    server.use(http.get(INDEX_URL, () => HttpResponse.json([1, 2, 3])));
    await expect(parseTarIndex(INDEX_URL)).rejects.toBeInstanceOf(
      IndexedTarFormatError,
    );
  });

  it("raises IndexedTarFormatError when an entry has a negative offset", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        HttpResponse.json({
          filesize: 1,
          hash: "",
          hash_lfs: "",
          files: { neg: { offset: -1, size: 5 } },
        }),
      ),
    );
    await expect(parseTarIndex(INDEX_URL)).rejects.toBeInstanceOf(
      IndexedTarFormatError,
    );
  });

  it("normalises missing top-level hash fields to empty strings", async () => {
    server.use(
      http.get(INDEX_URL, () =>
        HttpResponse.json({
          filesize: 4,
          // hash + hash_lfs deliberately omitted — older sidecars in
          // the wild lack one or both fields.
          files: { "x.txt": { offset: 0, size: 4 } },
        }),
      ),
    );
    const payload = await parseTarIndex(INDEX_URL);
    expect(payload.hash).toBe("");
    expect(payload.hash_lfs).toBe("");
  });

  // Note: AbortSignal forwarding is wired in production but cannot
  // be unit-tested under jsdom + Node 24 — undici's WebIDL guard
  // rejects signals constructed in the test realm before fetch is
  // invoked. The cancel path is exercised end-to-end via the
  // Playwright verification documented on the PR.
});

describe("buildTreeFromIndex + listDirectory", () => {
  it("groups files by directory prefix and aggregates size + count", () => {
    const tree = buildTreeFromIndex(SAMPLE_INDEX.files);
    expect(tree.fileCount).toBe(5);
    expect(tree.size).toBe(64 + 256 + 512 + 128 + 32);

    const root = listDirectory(tree, []);
    expect(root.folders.map((f) => f.name)).toEqual([
      "audio",
      "docs",
      "images",
    ]);
    expect(root.files.map((f) => f.name)).toEqual(["README.md"]);

    const images = listDirectory(tree, ["images"]);
    expect(images.folders.map((f) => f.name)).toEqual(["photos"]);
    expect(images.files.map((f) => f.name)).toEqual(["cover.png"]);

    const photos = listDirectory(tree, ["images", "photos"]);
    expect(photos.files.map((f) => f.name)).toEqual(["forest.jpg"]);
  });

  it("returns empty listings for an unknown segment", () => {
    const tree = buildTreeFromIndex(SAMPLE_INDEX.files);
    expect(listDirectory(tree, ["does-not-exist"]).folders).toEqual([]);
    expect(listDirectory(tree, ["does-not-exist"]).files).toEqual([]);
  });

  it("strips '.' and '..' segments instead of escaping the tree", () => {
    const tree = buildTreeFromIndex({
      "../escape.txt": { offset: 0, size: 4 },
      "./normal.txt": { offset: 4, size: 4 },
    });
    const root = listDirectory(tree, []);
    expect(root.files.map((f) => f.name).sort()).toEqual([
      "escape.txt",
      "normal.txt",
    ]);
  });

  it("returns an empty listing when a path segment hits a file instead of a folder", () => {
    // tar contains both `a/b/c.txt` AND `a` (file). Walking into
    // path ["a", "b"] crosses through a leaf node and must
    // bottom out in an empty listing rather than throw.
    const tree = buildTreeFromIndex({
      "a/b/c.txt": { offset: 0, size: 4 },
    });
    const result = listDirectory(tree, ["a", "b", "c.txt"]);
    expect(result).toEqual({ folders: [], files: [], node: null });
  });

  it("returns an empty listing when the walk continues past a file node", () => {
    // Walk: root → a (dir) → b (dir) → c.txt (file) → "extra".
    // The next-iteration `if (!cursor || cursor.type !== "dir")`
    // fires because cursor is a file node, not a directory.
    const tree = buildTreeFromIndex({
      "a/b/c.txt": { offset: 0, size: 4 },
    });
    const result = listDirectory(tree, ["a", "b", "c.txt", "extra"]);
    expect(result).toEqual({ folders: [], files: [], node: null });
  });

  it("skips a duplicate leaf when two source keys normalise to the same path", () => {
    // `./a/b` and `a/b` are different JSON keys but normalise to
    // the same segments after stripping `.` / `..`. The
    // tree-builder's leaf-collision branch keeps the first
    // insertion and drops the duplicate.
    const tree = buildTreeFromIndex({
      "./a/b.txt": { offset: 0, size: 4, sha256: "1" },
      "a/b.txt": { offset: 4, size: 4, sha256: "2" },
    });
    const sub = listDirectory(tree, ["a"]);
    expect(sub.files.length).toBe(1);
    expect(sub.files[0].sha256).toBe("1");
  });

  it("skips a duplicate path that collides with an existing tree node", () => {
    // hfutils.index files map keys are unique by definition, but a
    // hand-edited or merged sidecar can repeat a path. The
    // tree-builder swallows the second occurrence so listing
    // remains deterministic.
    const tree = buildTreeFromIndex({
      "a/b.txt": { offset: 0, size: 4 },
      "a/b.txt/extra": { offset: 4, size: 4 },
    });
    const sub = listDirectory(tree, ["a"]);
    expect(sub.files.map((f) => f.name)).toEqual(["b.txt"]);
    expect(sub.folders).toEqual([]);
  });
});

describe("buildMemberRangeHeader + extractMemberBytes", () => {
  it("emits the inclusive HTTP byte range hfutils.index uses", () => {
    expect(
      buildMemberRangeHeader({ offset: 100, size: 50 }),
    ).toBe("bytes=100-149");
  });

  it("throws on a zero-byte member so the caller can short-circuit", () => {
    expect(() =>
      buildMemberRangeHeader({ offset: 100, size: 0 }),
    ).toThrow();
  });

  it("rejects a member info that is missing offset or size", async () => {
    // Regression: TarBrowserDialog used to construct a memberView
    // wrapper that mirrored {path, name, size, sha256} but dropped
    // .offset. The download button then re-called extractMemberBytes
    // with offset === undefined; the resulting Range header
    // ("bytes=undefined-...") was silently ignored by MinIO and the
    // server returned the entire tar. The user saw "got 26183680
    // bytes, expected 373519" — the tar's full size, not the member.
    // The contract now is to throw a clear TypeError on shape misuse.
    await expect(
      extractMemberBytes("https://example/tar", { size: 100 }),
    ).rejects.toThrow(/offset/);
    await expect(
      extractMemberBytes("https://example/tar", { offset: 0 }),
    ).rejects.toThrow(/size/);
  });

  it("Range-reads exactly size bytes from the tar URL", async () => {
    const fullTar = new Uint8Array(64);
    for (let i = 0; i < 64; i++) fullTar[i] = i;

    server.use(
      http.get(TAR_URL, async ({ request }) => {
        const range = request.headers.get("range");
        const match = /^bytes=(\d+)-(\d+)$/.exec(range || "");
        if (!match) return new HttpResponse("missing range", { status: 400 });
        const start = Number(match[1]);
        const end = Number(match[2]);
        const slice = fullTar.subarray(start, end + 1);
        return new HttpResponse(slice, {
          status: 206,
          headers: {
            "Content-Range": `bytes ${start}-${end}/${fullTar.length}`,
            "Content-Length": String(slice.length),
            "Accept-Ranges": "bytes",
          },
        });
      }),
    );

    const bytes = await extractMemberBytes(TAR_URL, { offset: 8, size: 16 });
    expect(bytes).toBeInstanceOf(Uint8Array);
    expect(bytes.length).toBe(16);
    expect(Array.from(bytes)).toEqual([
      8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    ]);
  });

  it("returns an empty Uint8Array for size=0 without issuing a fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const bytes = await extractMemberBytes(TAR_URL, { offset: 0, size: 0 });
    expect(bytes.length).toBe(0);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("accepts a 200-status response (Range header ignored by the upstream)", async () => {
    // hfutils.index always uses Range — but if a proxy / cache strips
    // the header and returns the full body with status 200, the
    // expected window can still be sliced from the leading bytes.
    // The contract is "accept 200 too" so a presigned URL behind a
    // server that ignores Range stays usable for inline preview as
    // long as the member happens to start at offset 0.
    const fullTar = new Uint8Array(64);
    for (let i = 0; i < 64; i++) fullTar[i] = i;
    server.use(
      http.get(TAR_URL, () =>
        new HttpResponse(fullTar.subarray(0, 8), { status: 200 }),
      ),
    );
    const bytes = await extractMemberBytes(TAR_URL, { offset: 0, size: 8 });
    expect(Array.from(bytes)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it("rejects a non-2xx response with IndexedTarFetchError", async () => {
    server.use(
      http.get(TAR_URL, () => new HttpResponse("nope", { status: 500 })),
    );
    await expect(
      extractMemberBytes(TAR_URL, { offset: 0, size: 4 }),
    ).rejects.toThrow(IndexedTarFetchError);
  });

  it("rejects when the response is short of the requested size", async () => {
    server.use(
      http.get(TAR_URL, () =>
        new HttpResponse(new Uint8Array([1, 2, 3]), {
          status: 206,
          headers: {
            "Content-Range": "bytes 0-2/3",
            "Content-Length": "3",
          },
        }),
      ),
    );
    await expect(
      extractMemberBytes(TAR_URL, { offset: 0, size: 8 }),
    ).rejects.toThrow(/expected 8/);
  });
});

describe("compareTarHash", () => {
  it("matches when hash_lfs equals the tree-API sha256-shaped oid", () => {
    const oid = "abcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabca";
    expect(
      compareTarHash({ hash_lfs: oid }, { oid, size: 1, type: "file" }),
    ).toEqual({ kind: "match" });
  });

  it("flags a mismatch when sha256 differs", () => {
    const result = compareTarHash(
      { hash_lfs: "a".repeat(64), hash: "" },
      { oid: "b".repeat(64) },
    );
    expect(result.kind).toBe("mismatch");
    expect(result.expected).toBe("a".repeat(64));
    expect(result.actual).toBe("b".repeat(64));
  });

  it("returns 'unknown' when neither hash side has any payload", () => {
    expect(
      compareTarHash({ hash: "", hash_lfs: "" }, { oid: "anything" }),
    ).toEqual({ kind: "unknown" });
  });

  it("returns 'partial' when the index has hashes but the tree entry has none", () => {
    expect(
      compareTarHash({ hash_lfs: "a".repeat(64) }, { oid: "" }),
    ).toEqual({ kind: "partial" });
  });

  it("reads through to lfs.oid when the tar is LFS-stored", () => {
    const sha = "f".repeat(64);
    expect(
      compareTarHash({ hash_lfs: sha }, { oid: "ignored", lfs: { oid: sha } }),
    ).toEqual({ kind: "match" });
  });

  it("falls back to hash (git-blob sha1) when the tree oid is not sha256-shaped", () => {
    // A small inline file whose tree-side oid is the git-blob sha1
    // (40 hex chars) — the index's `hash` field is the matching
    // git-blob sha1, not the sha256 in `hash_lfs`. The comparator
    // recognises the shape and falls back to the right side.
    const blobSha1 = "0123456789abcdef0123456789abcdef01234567"; // 40 hex
    expect(
      compareTarHash(
        { hash: blobSha1, hash_lfs: "f".repeat(64) },
        { oid: blobSha1 },
      ),
    ).toEqual({ kind: "match" });
  });

  it("flags a mismatch on the non-sha256 fallback when git-blob sha1 differs", () => {
    expect(
      compareTarHash(
        { hash: "0".repeat(40), hash_lfs: "" },
        { oid: "1".repeat(40) },
      ).kind,
    ).toBe("mismatch");
  });

  it("returns 'partial' when only one side has a usable hash shape", () => {
    // Index has only sha256, tree only carries a git-blob sha1.
    expect(
      compareTarHash({ hash_lfs: "a".repeat(64) }, { oid: "1".repeat(40) }),
    ).toEqual({ kind: "partial" });
  });

  it("is case-insensitive (HEX strings normalised to lowercase before compare)", () => {
    const sha = "ABCDEFabcdef".repeat(5) + "abcd"; // 64 chars, mixed case
    expect(
      compareTarHash(
        { hash_lfs: sha.toUpperCase() },
        { oid: sha.toLowerCase() },
      ),
    ).toEqual({ kind: "match" });
  });
});

describe("hasIndexSibling", () => {
  it("returns true when the same listing carries a basename.json sibling", () => {
    const siblings = [
      { type: "file", path: "archives/gallery/bundle.tar" },
      { type: "file", path: "archives/gallery/bundle.json" },
      { type: "file", path: "archives/gallery/README.md" },
    ];
    expect(
      hasIndexSibling("archives/gallery/bundle.tar", siblings),
    ).toBe(true);
  });

  it("returns false for a bare .tar with no sidecar", () => {
    const siblings = [
      { type: "file", path: "archives/gallery/bundle.tar" },
      { type: "file", path: "archives/gallery/README.md" },
    ];
    expect(
      hasIndexSibling("archives/gallery/bundle.tar", siblings),
    ).toBe(false);
  });

  it("ignores directories with the same basename", () => {
    const siblings = [
      { type: "file", path: "archives/foo.tar" },
      { type: "directory", path: "archives/foo.json" },
    ];
    expect(hasIndexSibling("archives/foo.tar", siblings)).toBe(false);
  });

  it("returns false for non-tar paths", () => {
    expect(hasIndexSibling("bundle.json", [])).toBe(false);
    expect(hasIndexSibling("bundle.zip", [])).toBe(false);
  });
});

describe("tarSidecarPath", () => {
  it("returns the basename.json variant for a .tar path", () => {
    expect(tarSidecarPath("archives/gallery/bundle.tar")).toBe(
      "archives/gallery/bundle.json",
    );
  });

  it("preserves uppercase basenames but only matches the .tar suffix case-insensitively", () => {
    // The file-list shows the user the same case the repo carries; we
    // build the sidecar by swapping only the trailing ".tar" → ".json".
    expect(tarSidecarPath("Archives/MIXED.TAR")).toBe("Archives/MIXED.json");
  });

  it("returns null for non-tar paths", () => {
    expect(tarSidecarPath("bundle.json")).toBeNull();
    expect(tarSidecarPath("bundle.zip")).toBeNull();
    expect(tarSidecarPath("README")).toBeNull();
    expect(tarSidecarPath("")).toBeNull();
    expect(tarSidecarPath(null)).toBeNull();
  });
});

describe("hasIndexSiblingWithProbe", () => {
  it("short-circuits on a loaded-listing hit without invoking the probe", async () => {
    const probe = vi.fn();
    const siblings = [
      { type: "file", path: "archives/gallery/bundle.tar" },
      { type: "file", path: "archives/gallery/bundle.json" },
    ];
    const result = await hasIndexSiblingWithProbe(
      "archives/gallery/bundle.tar",
      siblings,
      probe,
    );
    expect(result).toBe(true);
    expect(probe).not.toHaveBeenCalled();
  });

  it("falls back to the probe when the loaded listing has no sibling", async () => {
    const probe = vi.fn(async (jsonPath) => {
      expect(jsonPath).toBe("archives/gallery/bundle.json");
      return true;
    });
    const result = await hasIndexSiblingWithProbe(
      "archives/gallery/bundle.tar",
      [],
      probe,
    );
    expect(result).toBe(true);
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("returns false when the probe says the sidecar does not exist", async () => {
    const probe = vi.fn(async () => false);
    const result = await hasIndexSiblingWithProbe(
      "archives/gallery/bundle.tar",
      null,
      probe,
    );
    expect(result).toBe(false);
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("treats a probe rejection as 'no sidecar'", async () => {
    // Pin the soft-fallback contract — a transient HEAD failure must
    // not surface as a user-visible exception when all the SPA wants
    // to know is "should the indexed-tar icon light up?"
    const probe = vi.fn(async () => {
      throw new Error("network down");
    });
    const result = await hasIndexSiblingWithProbe(
      "archives/gallery/bundle.tar",
      null,
      probe,
    );
    expect(result).toBe(false);
  });

  it("never probes for non-tar paths", async () => {
    const probe = vi.fn(async () => true);
    expect(
      await hasIndexSiblingWithProbe("bundle.json", null, probe),
    ).toBe(false);
    expect(probe).not.toHaveBeenCalled();
  });

  it("never probes when the caller did not supply a probe function", async () => {
    expect(
      await hasIndexSiblingWithProbe(
        "archives/gallery/bundle.tar",
        [],
        null,
      ),
    ).toBe(false);
  });
});

describe("classifyMember + guessMimeType", () => {
  it("routes common extensions to the correct preview category", () => {
    expect(classifyMember("a.png")).toBe("image");
    expect(classifyMember("a.MP4")).toBe("video");
    expect(classifyMember("a.flac")).toBe("audio");
    expect(classifyMember("a.pdf")).toBe("pdf");
    expect(classifyMember("a.md")).toBe("markdown");
    expect(classifyMember("a.safetensors")).toBe("safetensors");
    expect(classifyMember("a.parquet")).toBe("parquet");
    expect(classifyMember("a.json")).toBe("text");
    expect(classifyMember("a.bin")).toBe("binary");
  });

  it("recognises extension-less README / LICENSE / Dockerfile by basename", () => {
    // Common Unix-style files were falling through to "binary" so
    // the listing showed the generic gray document icon and the
    // member view bounced to "binary — use Download". Both surfaces
    // now route to the right renderer.
    expect(classifyMember("README")).toBe("markdown");
    expect(classifyMember("docs/README")).toBe("markdown");
    expect(classifyMember("LICENSE")).toBe("text");
    expect(classifyMember("Dockerfile")).toBe("text");
    expect(classifyMember("Makefile")).toBe("text");
    expect(classifyMember("CHANGELOG")).toBe("text");
  });

  it("returns standard MIME types for blob construction", () => {
    expect(guessMimeType("a.png")).toBe("image/png");
    expect(guessMimeType("a.jpg")).toBe("image/jpeg");
    expect(guessMimeType("a.pdf")).toBe("application/pdf");
    expect(guessMimeType("a.unknown")).toBe("application/octet-stream");
  });
});

describe("downloadBytesAs", () => {
  it("constructs an object URL anchor whose download attribute matches the in-tar basename", () => {
    const blobs = [];
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn((blob) => {
      blobs.push(blob);
      return "blob:test/123";
    });
    URL.revokeObjectURL = vi.fn();

    const created = [];
    const originalCreateElement = document.createElement.bind(document);
    const createSpy = vi.spyOn(document, "createElement").mockImplementation(
      (tag) => {
        const el = originalCreateElement(tag);
        if (tag === "a") {
          el.click = vi.fn();
          created.push(el);
        }
        return el;
      },
    );

    try {
      downloadBytesAs(new Uint8Array([1, 2, 3]), "members/photo.png", "image/png");
      expect(created).toHaveLength(1);
      const a = created[0];
      expect(a.download).toBe("members/photo.png");
      expect(a.href).toBe("blob:test/123");
      expect(a.click).toHaveBeenCalled();
      expect(blobs[0].type).toBe("image/png");
    } finally {
      createSpy.mockRestore();
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    }
  });
});
