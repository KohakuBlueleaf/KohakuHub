import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { server } from "../setup/msw-server";

import { parseSafetensorsMetadata } from "@/utils/safetensors";
import { parseParquetMetadata } from "@/utils/parquet";
import {
  IndexedTarFetchError,
  extractMemberBytes,
  parseTarIndex,
} from "@/utils/indexed-tar";
import { extractThumbnail, _resetThumbnailCache } from "@/utils/tar-thumbnail";

// Regression for the private-repo 404: the four range-read entry points
// (safetensors / parquet / indexed-tar JSON / indexed-tar member, plus
// the tar-thumbnail strategy chain that routes through the last) used to
// hard-code `credentials: "omit"` on their fetch options. Same-origin
// requests with `credentials: "omit"` carry no Cookie header, so the
// SPA's session cookie never reached the backend's /resolve/ handler;
// for a private repo the HF-compat anti-enumeration path then returned
// 404 even though the user was logged in.
//
// The contract these tests pin:
//   * every range-read fetch must use a credentials mode that forwards
//     same-origin cookies (`same-origin` or `include`); never `omit`.
//   * a backend that 404s on cookie-less requests must not break the
//     reader once credentials are forwarded — the request still
//     succeeds and parses end-to-end.

const __dirname = dirname(fileURLToPath(import.meta.url));
const SAFETENSORS_FIXTURE = readFileSync(
  resolve(__dirname, "../fixtures/previews/tiny.safetensors"),
);
const PARQUET_FIXTURE = readFileSync(
  resolve(__dirname, "../fixtures/previews/tiny.parquet"),
);

const SAFETENSORS_URL = "https://hub.test.local/datasets/owner/secret/resolve/main/model.safetensors";
const PARQUET_URL = "https://hub.test.local/datasets/owner/secret/resolve/main/data.parquet";
const TAR_INDEX_URL = "https://hub.test.local/datasets/owner/secret/resolve/main/archive.json";
const TAR_URL = "https://hub.test.local/datasets/owner/secret/resolve/main/archive.tar";

// Picks credentials forwarded by the fetch init. Treat any non-"omit"
// value as a pass — the SPA does not need to commit to a specific
// non-omit token, just to "not omit". The current implementation uses
// "same-origin" but a future hardening to "include" would still pin the
// regression.
function isCookieCarrying(credentials) {
  return credentials !== undefined && credentials !== "omit";
}

// MSW handler factory that mimics the HF-compat anti-enumeration path:
// without a cookie-carrying credentials mode the response is 404 with a
// RepoNotFound shape. With credentials it serves real bytes (Range or
// full body).
function privateRepoHandler(buffer) {
  return async ({ request }) => {
    if (!isCookieCarrying(request.credentials)) {
      return HttpResponse.json(
        { error: "RepoNotFound", detail: "Repository 'owner/secret' not found" },
        {
          status: 404,
          headers: {
            "X-Error-Code": "RepoNotFound",
            "X-Error-Message": "Repository 'owner/secret' not found",
          },
        },
      );
    }
    if (request.method.toUpperCase() === "HEAD") {
      return new HttpResponse(null, {
        status: 200,
        headers: {
          "Content-Length": String(buffer.length),
          "Accept-Ranges": "bytes",
        },
      });
    }
    const range = request.headers.get("range");
    if (!range) {
      return new HttpResponse(buffer, {
        status: 200,
        headers: { "Content-Length": String(buffer.length) },
      });
    }
    const match = /^bytes=(\d+)-(\d+)?$/.exec(range);
    if (!match) return new HttpResponse("Bad Range", { status: 400 });
    const start = Number(match[1]);
    const end = match[2] == null ? buffer.length - 1 : Math.min(Number(match[2]), buffer.length - 1);
    const slice = buffer.subarray(start, end + 1);
    return new HttpResponse(slice, {
      status: 206,
      headers: {
        "Content-Range": `bytes ${start}-${end}/${buffer.length}`,
        "Content-Length": String(slice.length),
        "Accept-Ranges": "bytes",
      },
    });
  };
}

describe("range-read credentials forwarding (private-repo regression)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    _resetThumbnailCache();
  });

  describe("safetensors", () => {
    it("forwards same-origin cookies on the speculative head Range", async () => {
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      server.use(http.get(SAFETENSORS_URL, privateRepoHandler(SAFETENSORS_FIXTURE)));

      const header = await parseSafetensorsMetadata(SAFETENSORS_URL);

      expect(fetchSpy).toHaveBeenCalled();
      for (const call of fetchSpy.mock.calls) {
        const init = call[1] || {};
        expect(init.credentials).toBeDefined();
        expect(init.credentials).not.toBe("omit");
        expect(isCookieCarrying(init.credentials)).toBe(true);
      }
      expect(Object.keys(header.tensors).length).toBeGreaterThan(0);
    });

    it("404s end-to-end when credentials would be omitted", async () => {
      // Pin the bug shape: a backend that hides private repos from
      // cookie-less requests must surface a 404 SafetensorsFetchError.
      // The fix sends credentials, so this path is only reachable when
      // the option is regressed back to "omit" — but the test handler
      // also rejects requests that arrive without same-origin
      // credentials, so the assertion documents the user-visible
      // failure mode the regression caused in production.
      server.use(http.get(SAFETENSORS_URL, privateRepoHandler(SAFETENSORS_FIXTURE)));
      const realFetch = globalThis.fetch;
      const stub = vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
        return realFetch(url, { ...(init || {}), credentials: "omit" });
      });
      const err = await parseSafetensorsMetadata(SAFETENSORS_URL).catch((e) => e);
      expect(err).toBeDefined();
      expect(err.status).toBe(404);
      expect(err.errorCode).toBe("RepoNotFound");
      stub.mockRestore();
    });
  });

  describe("parquet", () => {
    it("forwards same-origin cookies on hyparquet's HEAD + tail Range", async () => {
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      // hyparquet's asyncBufferFromUrl issues HEAD before any Range
      // GET, so register both methods. http.get does not match HEAD
      // in MSW v2.
      const handler = privateRepoHandler(PARQUET_FIXTURE);
      server.use(
        http.head(PARQUET_URL, handler),
        http.get(PARQUET_URL, handler),
      );

      const metadata = await parseParquetMetadata(PARQUET_URL);

      expect(fetchSpy).toHaveBeenCalled();
      for (const call of fetchSpy.mock.calls) {
        const init = call[1] || {};
        // hyparquet sometimes calls fetch with no init at all when
        // delegating through its internal `asyncBufferFromUrl` reader;
        // the requestInit we threaded in is then merged in by hyparquet
        // before the real network call — `init` here is the merged
        // version and must carry the credentials we passed.
        expect(init.credentials).toBeDefined();
        expect(init.credentials).not.toBe("omit");
        expect(isCookieCarrying(init.credentials)).toBe(true);
      }
      expect(metadata.byteLength).toBe(PARQUET_FIXTURE.length);
    });
  });

  describe("indexed-tar", () => {
    it("parseTarIndex forwards same-origin cookies on the sidecar GET", async () => {
      const indexJson = JSON.stringify({
        filesize: 32,
        hash: "",
        hash_lfs: "",
        files: { "a.bin": { offset: 0, size: 4 } },
      });
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      server.use(http.get(TAR_INDEX_URL, privateRepoHandler(Buffer.from(indexJson))));

      const payload = await parseTarIndex(TAR_INDEX_URL);

      expect(fetchSpy).toHaveBeenCalled();
      for (const call of fetchSpy.mock.calls) {
        const init = call[1] || {};
        expect(init.credentials).not.toBe("omit");
        expect(isCookieCarrying(init.credentials)).toBe(true);
      }
      expect(payload.files["a.bin"]).toBeDefined();
    });

    it("parseTarIndex surfaces the 404 when credentials are omitted", async () => {
      const indexJson = JSON.stringify({
        filesize: 32,
        hash: "",
        hash_lfs: "",
        files: { "a.bin": { offset: 0, size: 4 } },
      });
      server.use(http.get(TAR_INDEX_URL, privateRepoHandler(Buffer.from(indexJson))));
      const realFetch = globalThis.fetch;
      const stub = vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
        return realFetch(url, { ...(init || {}), credentials: "omit" });
      });
      const err = await parseTarIndex(TAR_INDEX_URL).catch((e) => e);
      expect(err).toBeInstanceOf(IndexedTarFetchError);
      expect(err.status).toBe(404);
      stub.mockRestore();
    });

    it("extractMemberBytes forwards same-origin cookies on the member Range", async () => {
      const fakeTar = new Uint8Array(64);
      for (let i = 0; i < 64; i++) fakeTar[i] = i;
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      server.use(http.get(TAR_URL, privateRepoHandler(Buffer.from(fakeTar))));

      const bytes = await extractMemberBytes(TAR_URL, { offset: 8, size: 16 });

      expect(fetchSpy).toHaveBeenCalled();
      for (const call of fetchSpy.mock.calls) {
        const init = call[1] || {};
        expect(init.credentials).not.toBe("omit");
        expect(isCookieCarrying(init.credentials)).toBe(true);
      }
      expect(Array.from(bytes.subarray(0, 4))).toEqual([8, 9, 10, 11]);
    });
  });

  describe("tar-thumbnail", () => {
    it("inherits same-origin credentials via extractMemberBytes", async () => {
      // Build a minimal fake "tar" that contains a JPEG with a SOI
      // marker plus enough trailing bytes to exit the EXIF parser
      // cleanly; the strategy chain falls through to the small-image
      // tier which then runs the canvas decoder. We only care that
      // the underlying fetch carried credentials — the canvas path
      // is mocked because jsdom does not decode JPEGs.
      const jpegHead = new Uint8Array([
        0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00,
      ]);
      const tar = new Uint8Array(2048);
      tar.set(jpegHead, 0);
      const member = { offset: 0, size: jpegHead.length, name: "img.jpg", path: "img.jpg" };

      // Stub the canvas-decode pipeline — the test cares about the
      // fetch credentials, not the bitmap result.
      const stub = vi.spyOn(globalThis, "fetch");
      server.use(http.get(TAR_URL, privateRepoHandler(Buffer.from(tar))));

      // Force the chain through a strategy that issues the Range read.
      // Using the EXIF strategy keeps the test cheap; even if EXIF
      // parsing returns null, the head Range was issued — that is
      // what the regression cares about.
      await extractThumbnail({ tarUrl: TAR_URL, member }).catch(() => null);

      expect(stub).toHaveBeenCalled();
      for (const call of stub.mock.calls) {
        const init = call[1] || {};
        expect(init.credentials).not.toBe("omit");
        expect(isCookieCarrying(init.credentials)).toBe(true);
      }
    });
  });
});
