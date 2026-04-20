import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { server } from "../setup/msw-server";

describe("LFS utilities", () => {
  async function loadModule() {
    vi.resetModules();
    return import("@/utils/lfs");
  }

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("calculates SHA256 incrementally and verifies the result", async () => {
    const { calculateSHA256, verifyFileSHA256 } = await loadModule();

    const chunkSize = 64 * 1024 * 1024;
    const file = {
      name: "large.bin",
      size: chunkSize + 7,
      slice(start) {
        return start === 0 ? new Blob(["first-chunk"]) : new Blob(["tail"]);
      },
    };

    const progress = vi.fn();
    const digest = await calculateSHA256(file, progress);

    expect(digest).toHaveLength(64);
    expect(progress).toHaveBeenCalledTimes(2);
    expect(progress).toHaveBeenLastCalledWith(1);
    await expect(verifyFileSHA256(file, digest)).resolves.toBe(true);
    await expect(verifyFileSHA256(file, "0".repeat(64))).resolves.toBe(false);
  });

  it("uploads a single-part LFS object and formats sizes", async () => {
    server.use(
      http.post("*/alice/demo.git/info/lfs/objects/batch", async ({ request }) => {
        const body = await request.json();
        expect(body.operation).toBe("upload");
        expect(body.objects).toEqual([{ oid: "sha-single", size: 4 }]);

        return HttpResponse.json({
          objects: [
            {
              actions: {
                upload: {
                  href: "https://s3.example/upload",
                  header: {
                    "Content-Type": "application/octet-stream",
                  },
                },
                verify: {
                  href: "https://s3.example/verify",
                },
              },
            },
          ],
        });
      }),
      http.put("https://s3.example/upload", async ({ request }) => {
        expect(request.headers.get("content-type")).toContain(
          "application/octet-stream",
        );
        return new HttpResponse(null, { status: 200 });
      }),
      http.post("https://s3.example/verify", async ({ request }) => {
        const body = await request.json();
        expect(body).toEqual({ oid: "sha-single", size: 4 });
        return HttpResponse.json({ ok: true });
      }),
    );

    const { uploadLFSFile, formatFileSize } = await loadModule();
    const file = new File(["data"], "weights.bin", {
      type: "application/octet-stream",
    });

    await expect(
      uploadLFSFile("alice/demo", file, "sha-single"),
    ).resolves.toEqual({
      oid: "sha-single",
      size: 4,
    });

    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(999)).toBe("999 B");
    expect(formatFileSize(1_500)).toBe("1.5 KB");
    expect(formatFileSize(1_500_000)).toBe("1.5 MB");
    expect(formatFileSize(1_500_000_000)).toBe("1.5 GB");
  });

  it("handles deduplicated objects and batch errors", async () => {
    const { uploadLFSFile } = await loadModule();
    const file = new File(["data"], "weights.bin", {
      type: "application/octet-stream",
    });

    server.use(
      http.post("*/alice/demo.git/info/lfs/objects/batch", () =>
        HttpResponse.json({
          objects: [
            {
              oid: "sha-existing",
              size: 4,
            },
          ],
        }),
      ),
    );

    await expect(
      uploadLFSFile("alice/demo", file, "sha-existing"),
    ).resolves.toEqual({
      oid: "sha-existing",
      size: 4,
    });

    server.use(
      http.post("*/alice/demo.git/info/lfs/objects/batch", () =>
        HttpResponse.json({
          objects: [
            {
              error: {
                message: "permission denied",
              },
            },
          ],
        }),
      ),
    );

    await expect(
      uploadLFSFile("alice/demo", file, "sha-error"),
    ).rejects.toThrow("LFS batch error: permission denied");
  });

  it("uploads multipart LFS objects and completes the upload", async () => {
    server.use(
      http.post("*/alice/demo.git/info/lfs/objects/batch", () =>
        HttpResponse.json({
          objects: [
            {
              actions: {
                upload: {
                  href: "https://s3.example/complete",
                  header: {
                    chunk_size: "3",
                    upload_id: "upload-1",
                    1: "https://s3.example/part-1",
                    2: "https://s3.example/part-2",
                    3: "https://s3.example/part-3",
                  },
                },
                verify: {
                  href: "https://s3.example/verify",
                },
              },
            },
          ],
        }),
      ),
      http.put("https://s3.example/part-1", () =>
        new HttpResponse(null, {
          status: 200,
          headers: { ETag: '"etag-1"' },
        }),
      ),
      http.put("https://s3.example/part-2", () =>
        new HttpResponse(null, {
          status: 200,
          headers: { ETag: '"etag-2"' },
        }),
      ),
      http.put("https://s3.example/part-3", () =>
        new HttpResponse(null, {
          status: 200,
          headers: { ETag: '"etag-3"' },
        }),
      ),
      http.post("https://s3.example/complete", async ({ request }) => {
        const body = await request.json();
        expect(body).toEqual({
          oid: "sha-multipart",
          size: 9,
          parts: [
            { PartNumber: 1, ETag: "etag-1" },
            { PartNumber: 2, ETag: "etag-2" },
            { PartNumber: 3, ETag: "etag-3" },
          ],
        });
        return HttpResponse.json({ ok: true });
      }),
      http.post("https://s3.example/verify", async ({ request }) => {
        const body = await request.json();
        expect(body).toEqual({ oid: "sha-multipart", size: 9 });
        return HttpResponse.json({ ok: true });
      }),
    );

    const { uploadLFSFile } = await loadModule();
    const file = new File(["abcdefghi"], "archive.bin", {
      type: "application/octet-stream",
    });

    await expect(
      uploadLFSFile("alice/demo", file, "sha-multipart"),
    ).resolves.toEqual({
      oid: "sha-multipart",
      size: 9,
    });
  });
});
