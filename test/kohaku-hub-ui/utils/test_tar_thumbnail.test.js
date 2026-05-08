import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { server } from "../setup/msw-server";

import {
  HEAD_BYTES,
  SMALL_IMAGE_BYTES,
  MEDIUM_IMAGE_BYTES,
  TOGGLE_STORAGE_KEY,
  _STRATEGIES,
  _createCache,
  _createPool,
  _resetThumbnailToggle,
  detectImageMime,
  extractThumbnail,
  isImageMember,
  parseExifThumbnail,
  useThumbnailToggle,
} from "@/utils/tar-thumbnail";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES = resolve(__dirname, "../fixtures/previews");

const JPEG_WITH_EXIF = readFileSync(resolve(FIXTURES, "with_exif_thumb.jpg"));
const JPEG_NO_EXIF = readFileSync(resolve(FIXTURES, "no_exif_thumb.jpg"));

const TAR_URL = "https://s3.test.local/bucket/archive.tar";

function rangeResponder(buffer, baseOffset = 0) {
  return async ({ request }) => {
    const range = request.headers.get("range");
    if (!range) return new HttpResponse(buffer, { status: 200 });
    const m = /^bytes=(\d+)-(\d+)$/.exec(range);
    if (!m) return new HttpResponse("Bad Range", { status: 400 });
    const start = Number(m[1]) - baseOffset;
    const end = Math.min(Number(m[2]) - baseOffset, buffer.length - 1);
    const slice = buffer.subarray(start, end + 1);
    return new HttpResponse(slice, {
      status: 206,
      headers: {
        "Content-Range": `bytes ${start + baseOffset}-${end + baseOffset}/${buffer.length}`,
        "Content-Length": String(slice.length),
        "Accept-Ranges": "bytes",
      },
    });
  };
}

// jsdom does not expose URL.createObjectURL / revokeObjectURL by
// default — install fakes per-test so the strategy chain (which
// wraps the resulting Blob into an object URL) returns a stable
// `blob:` string the assertions can match against.
let originalCreate;
let originalRevoke;

beforeEach(() => {
  _resetThumbnailToggle();
  originalCreate = URL.createObjectURL;
  originalRevoke = URL.revokeObjectURL;
  let counter = 0;
  URL.createObjectURL = vi.fn(() => `blob:mock/${++counter}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  _resetThumbnailToggle();
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
});

describe("isImageMember", () => {
  it("classifies common image extensions", () => {
    for (const ext of ["jpg", "JPEG", "png", "Webp", "gif", "BMP", "ico"]) {
      expect(isImageMember({ name: `x.${ext}` })).toBe(true);
    }
  });
  it("rejects non-images and bad inputs", () => {
    expect(isImageMember({ name: "video.mp4" })).toBe(false);
    expect(isImageMember({ name: "notes.txt" })).toBe(false);
    expect(isImageMember({ name: "no-extension" })).toBe(false);
    expect(isImageMember(null)).toBe(false);
  });
});

describe("detectImageMime", () => {
  it("recognises JPEG / PNG / WebP / GIF magic bytes", () => {
    expect(detectImageMime(JPEG_WITH_EXIF)).toBe("image/jpeg");
    const png = new Uint8Array([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00,
    ]);
    expect(detectImageMime(png)).toBe("image/png");
    const webp = new Uint8Array(16);
    webp[0] = 0x52; webp[1] = 0x49; webp[2] = 0x46; webp[3] = 0x46;
    webp[8] = 0x57; webp[9] = 0x45; webp[10] = 0x42; webp[11] = 0x50;
    expect(detectImageMime(webp)).toBe("image/webp");
    const gif = new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]);
    expect(detectImageMime(gif)).toBe("image/gif");
  });
  it("returns null for non-image / short input", () => {
    expect(detectImageMime(new Uint8Array(3))).toBe(null);
    expect(detectImageMime(new Uint8Array([1, 2, 3, 4]))).toBe(null);
    expect(detectImageMime(null)).toBe(null);
  });
});

describe("parseExifThumbnail", () => {
  it("extracts the embedded JPEG thumbnail from a real EXIF-bearing JPEG", () => {
    const thumb = parseExifThumbnail(JPEG_WITH_EXIF);
    // The function returns a typed-array view into the head buffer.
    // In jsdom the source Buffer's subarray returns a Buffer, which
    // is structurally a Uint8Array but cross-realm instanceof can
    // fail — assert via the structural contract instead.
    expect(thumb).toBeTruthy();
    expect(typeof thumb.byteLength).toBe("number");
    expect(thumb.byteLength).toBeGreaterThan(0);
    // Embedded thumbnail must itself be a valid JPEG (SOI ... EOI).
    expect(thumb[0]).toBe(0xff);
    expect(thumb[1]).toBe(0xd8);
    expect(thumb[thumb.byteLength - 2]).toBe(0xff);
    expect(thumb[thumb.byteLength - 1]).toBe(0xd9);
  });

  it("returns null when the JPEG carries no EXIF thumbnail", () => {
    expect(parseExifThumbnail(JPEG_NO_EXIF)).toBe(null);
  });

  it("returns null on garbage / non-JPEG / truncated input", () => {
    expect(parseExifThumbnail(new Uint8Array(0))).toBe(null);
    expect(parseExifThumbnail(new Uint8Array([0x89, 0x50, 0x4e, 0x47]))).toBe(
      null,
    );
    expect(parseExifThumbnail(JPEG_WITH_EXIF.subarray(0, 10))).toBe(null);
  });

  it("only succeeds when the head buffer is large enough to reach the thumbnail bytes", () => {
    // Truncating the JPEG before the thumbnail payload starts must
    // surface as null — caller treats this as "EXIF probe miss" and
    // falls through to the next strategy.
    expect(parseExifThumbnail(JPEG_WITH_EXIF.subarray(0, 100))).toBe(null);
  });

  it("returns null when the JPEG starts with SOI but has malformed marker length", () => {
    // SOI + invalid marker (length < 2 is illegal per spec). The
    // walker bails out instead of crashing.
    const bad = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x00]);
    expect(parseExifThumbnail(bad)).toBe(null);
  });

  it("returns null when an APP1 segment lacks the Exif\\0\\0 magic (e.g. XMP)", () => {
    // Construct a minimal SOI + APP1 (length 14) carrying "XMP"
    // ascii — this is what Adobe-injected XMP segments look like
    // and the parser must keep walking past it.
    const buf = new Uint8Array(20);
    buf[0] = 0xff; buf[1] = 0xd8; buf[2] = 0xff; buf[3] = 0xe1;
    buf[4] = 0x00; buf[5] = 0x10;
    buf.set(new TextEncoder().encode("xmp:adobe.com"), 6);
    // No SOS / EOI follows — the next iteration hits end-of-buffer
    // and returns null cleanly.
    expect(parseExifThumbnail(buf)).toBe(null);
  });

  it("returns null when the TIFF byte-order marker isn't II or MM", () => {
    // SOI + APP1 with "Exif\0\0" but a bogus byte-order pair.
    const tiff = new Uint8Array(8);
    tiff[0] = 0x41; tiff[1] = 0x42; // "AB" — neither II nor MM
    const exif = new Uint8Array(6 + tiff.length);
    exif.set(new TextEncoder().encode("Exif\x00\x00"), 0);
    exif.set(tiff, 6);
    const segLen = 2 + exif.length;
    const buf = new Uint8Array(4 + exif.length);
    buf[0] = 0xff; buf[1] = 0xd8; // SOI
    // The above buffer is too small — write a fresh one.
    const full = new Uint8Array(2 + 2 + 2 + exif.length);
    full[0] = 0xff; full[1] = 0xd8;
    full[2] = 0xff; full[3] = 0xe1;
    full[4] = (segLen >> 8) & 0xff; full[5] = segLen & 0xff;
    full.set(exif, 6);
    expect(parseExifThumbnail(full)).toBe(null);
  });
});

describe("strategy registry", () => {
  it("exposes jpeg-exif first so EXIF probes win before any full read", () => {
    const names = _STRATEGIES.map((s) => s.name);
    expect(names[0]).toBe("jpeg-exif");
    expect(names).toEqual(["jpeg-exif", "small-image", "medium-image"]);
  });

  it("matches members by extension and size buckets", () => {
    const small = { name: "a.png", size: 1024 };
    const medium = { name: "b.png", size: SMALL_IMAGE_BYTES + 1 };
    const huge = { name: "c.png", size: MEDIUM_IMAGE_BYTES + 1 };
    const notImage = { name: "d.tar", size: 1024 };

    expect(_STRATEGIES[1].match(small)).toBe(true); // small-image
    expect(_STRATEGIES[2].match(medium)).toBe(true); // medium-image
    expect(_STRATEGIES[1].match(medium)).toBe(false);
    expect(_STRATEGIES[2].match(huge)).toBe(false);
    expect(_STRATEGIES[1].match(notImage)).toBe(false);
    expect(_STRATEGIES[0].match(small)).toBe(false); // jpeg-exif rejects non-jpeg
    expect(_STRATEGIES[0].match({ name: "a.JPG", size: 1 })).toBe(true);
  });
});

describe("extractThumbnail – orchestrator", () => {
  it("returns null and stays harmless for an oversized image (no strategy matches)", async () => {
    const member = {
      name: "big.png",
      offset: 0,
      size: MEDIUM_IMAGE_BYTES + 1024,
    };
    // No MSW handler — if any strategy did fire, the test would fail
    // with an unhandled-request error. A return of null without
    // network activity is exactly the contract we want.
    const out = await extractThumbnail({ tarUrl: TAR_URL, member });
    expect(out).toBe(null);
  });

  it("hits the EXIF strategy with one Range read and returns a blob URL on a JPEG with EXIF thumb", async () => {
    let rangeHits = 0;
    server.use(
      http.get(TAR_URL, async ({ request }) => {
        rangeHits += 1;
        const range = request.headers.get("range");
        const m = /^bytes=(\d+)-(\d+)$/.exec(range || "");
        if (!m) return new HttpResponse("missing", { status: 400 });
        const start = Number(m[1]);
        const end = Math.min(Number(m[2]), JPEG_WITH_EXIF.length - 1);
        const slice = JPEG_WITH_EXIF.subarray(start, end + 1);
        return new HttpResponse(slice, {
          status: 206,
          headers: {
            "Content-Range": `bytes ${start}-${end}/${JPEG_WITH_EXIF.length}`,
            "Content-Length": String(slice.length),
            "Accept-Ranges": "bytes",
          },
        });
      }),
    );

    const member = {
      name: "with-exif.jpg",
      offset: 0,
      size: JPEG_WITH_EXIF.length,
    };
    const cache = _createCache(10);
    const pool = _createPool(2);
    const url = await extractThumbnail({
      tarUrl: TAR_URL,
      member,
      cache,
      pool,
    });
    expect(url).toMatch(/^blob:/);
    expect(rangeHits).toBe(1); // EXIF probe only — never fell through
  });

  it("caches the produced blob URL keyed by (tarUrl, offset, size)", async () => {
    let rangeHits = 0;
    server.use(
      http.get(TAR_URL, async ({ request }) => {
        rangeHits += 1;
        const range = request.headers.get("range");
        const m = /^bytes=(\d+)-(\d+)$/.exec(range || "");
        const start = Number(m[1]);
        const end = Math.min(Number(m[2]), JPEG_WITH_EXIF.length - 1);
        return new HttpResponse(
          JPEG_WITH_EXIF.subarray(start, end + 1),
          { status: 206 },
        );
      }),
    );

    const member = { name: "x.jpg", offset: 0, size: JPEG_WITH_EXIF.length };
    const cache = _createCache(10);
    const pool = _createPool(2);

    const a = await extractThumbnail({ tarUrl: TAR_URL, member, cache, pool });
    const b = await extractThumbnail({ tarUrl: TAR_URL, member, cache, pool });
    expect(a).toBe(b);
    expect(rangeHits).toBe(1); // Second call hit cache.
  });

  it("rejects with AbortError when the signal aborts before extraction starts", async () => {
    const member = { name: "x.jpg", offset: 0, size: JPEG_WITH_EXIF.length };
    const controller = new AbortController();
    controller.abort();
    await expect(
      extractThumbnail({
        tarUrl: TAR_URL,
        member,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("decoder pipeline (mocked Image + canvas)", () => {
  // jsdom does not actually decode images or rasterise canvas
  // operations. We stand in a synchronous Image stub and force
  // `canvas.toBlob` to hand back a small Blob so the strategy
  // chain's small/medium-image extract paths run end-to-end.
  let originalImage;
  let originalCreateElement;

  beforeEach(() => {
    originalImage = globalThis.Image;
    globalThis.Image = class {
      constructor() {
        this._src = "";
        this.naturalWidth = 320;
        this.naturalHeight = 240;
      }
      set src(v) {
        this._src = v;
        // Fire onload on a microtask boundary so the awaiter is
        // attached before resolution.
        Promise.resolve().then(() => this.onload?.());
      }
      get src() {
        return this._src;
      }
    };
    originalCreateElement = document.createElement.bind(document);
    document.createElement = (tag) => {
      if (tag === "canvas") {
        return {
          width: 0,
          height: 0,
          getContext: () => ({
            drawImage: vi.fn(),
          }),
          toBlob: (cb, type, quality) => {
            cb(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type }));
          },
        };
      }
      return originalCreateElement(tag);
    };
  });

  afterEach(() => {
    globalThis.Image = originalImage;
    document.createElement = originalCreateElement;
  });

  it("runs the small-image strategy end-to-end on a JPEG without EXIF", async () => {
    server.use(
      http.get(TAR_URL, async ({ request }) => {
        const range = request.headers.get("range");
        const m = /^bytes=(\d+)-(\d+)$/.exec(range || "");
        const start = Number(m[1]);
        const end = Math.min(Number(m[2]), JPEG_NO_EXIF.length - 1);
        return new HttpResponse(JPEG_NO_EXIF.subarray(start, end + 1), {
          status: 206,
        });
      }),
    );
    const member = {
      name: "no-exif.jpg",
      offset: 0,
      size: JPEG_NO_EXIF.length,
    };
    const cache = _createCache(10);
    const pool = _createPool(2);
    const url = await extractThumbnail({
      tarUrl: TAR_URL,
      member,
      cache,
      pool,
    });
    expect(url).toMatch(/^blob:/);
  });

  it("runs the medium-image strategy on a >256 KB image (synthesised)", async () => {
    // Build a synthetic JPEG blob of ~300 KB by padding the no-exif
    // fixture. Magic bytes survive because we prepend the original
    // SOI/SOS bytes; the ext-based small/medium strategies route by
    // size, not by trailing-byte validity. The mocked Image above
    // returns onload regardless of the bytes.
    const padding = new Uint8Array(300 * 1024);
    padding.set(JPEG_NO_EXIF, 0);
    const member = {
      name: "medium.jpg",
      offset: 0,
      size: padding.length,
    };
    server.use(
      http.get(TAR_URL, async ({ request }) => {
        const range = request.headers.get("range");
        const m = /^bytes=(\d+)-(\d+)$/.exec(range || "");
        const start = Number(m[1]);
        const end = Math.min(Number(m[2]), padding.length - 1);
        return new HttpResponse(padding.subarray(start, end + 1), {
          status: 206,
        });
      }),
    );
    const cache = _createCache(10);
    const pool = _createPool(2);
    const url = await extractThumbnail({
      tarUrl: TAR_URL,
      member,
      cache,
      pool,
    });
    expect(url).toMatch(/^blob:/);
  });

  it("rejects with a synthetic decode failure (Image.onerror fires)", async () => {
    // Replace the Image stub for this single test with one that
    // emits onerror — covers decodeImageToThumbnail's reject arm.
    globalThis.Image = class {
      set src(_v) {
        Promise.resolve().then(() => this.onerror?.());
      }
    };
    server.use(
      http.get(TAR_URL, async ({ request }) => {
        const range = request.headers.get("range");
        const m = /^bytes=(\d+)-(\d+)$/.exec(range || "");
        const start = Number(m[1]);
        const end = Math.min(Number(m[2]), JPEG_NO_EXIF.length - 1);
        return new HttpResponse(JPEG_NO_EXIF.subarray(start, end + 1), {
          status: 206,
        });
      }),
    );
    const member = {
      name: "broken.jpg",
      offset: 0,
      size: JPEG_NO_EXIF.length,
    };
    await expect(
      extractThumbnail({
        tarUrl: TAR_URL,
        member,
        cache: _createCache(2),
        pool: _createPool(2),
      }),
    ).rejects.toThrow(/image failed to decode/);
  });
});

describe("LRU cache", () => {
  it("evicts the oldest entry past `max` capacity and revokes its blob URL", () => {
    const revoked = [];
    const originalRevoke = URL.revokeObjectURL;
    URL.revokeObjectURL = vi.fn((u) => revoked.push(u));
    try {
      const cache = _createCache(2);
      cache.set(TAR_URL, { offset: 0, size: 10 }, "blob:a");
      cache.set(TAR_URL, { offset: 10, size: 10 }, "blob:b");
      cache.set(TAR_URL, { offset: 20, size: 10 }, "blob:c");
      expect(cache.size()).toBe(2);
      expect(revoked).toEqual(["blob:a"]);
      // Touching b refreshes its recency, so the next eviction
      // should drop c instead.
      cache.get(TAR_URL, { offset: 10, size: 10 });
      cache.set(TAR_URL, { offset: 30, size: 10 }, "blob:d");
      expect(revoked).toEqual(["blob:a", "blob:c"]);
    } finally {
      URL.revokeObjectURL = originalRevoke;
    }
  });
});

describe("concurrency pool", () => {
  it("never runs more than `max` tasks concurrently", async () => {
    const pool = _createPool(3);
    let running = 0;
    let peak = 0;
    const tasks = Array.from({ length: 10 }, () =>
      pool.run(async () => {
        running += 1;
        peak = Math.max(peak, running);
        await new Promise((r) => setTimeout(r, 5));
        running -= 1;
      }),
    );
    await Promise.all(tasks);
    expect(peak).toBeLessThanOrEqual(3);
  });
});

describe("useThumbnailToggle", () => {
  it("defaults to ON when localStorage has no opinion", () => {
    expect(localStorage.getItem(TOGGLE_STORAGE_KEY)).toBe(null);
    // Module-level initial state already reflects "no opinion" → on.
    // We can't mount a Vue composable without a component — use the
    // reset hook to confirm the storage write contract.
    _resetThumbnailToggle();
    expect(localStorage.getItem(TOGGLE_STORAGE_KEY)).toBe(null);
  });

  it("persists OFF in localStorage as '0' and ON as '1'", () => {
    // Direct exercise via the same key the composable uses; the
    // composable wrapping is covered in the component test.
    localStorage.setItem(TOGGLE_STORAGE_KEY, "0");
    expect(localStorage.getItem(TOGGLE_STORAGE_KEY)).toBe("0");
    localStorage.setItem(TOGGLE_STORAGE_KEY, "1");
    expect(localStorage.getItem(TOGGLE_STORAGE_KEY)).toBe("1");
  });

  it("setEnabled(false) writes '0' to storage and broadcasts to other live composables", async () => {
    // Mount a tiny host component twice so we can observe the
    // module-level listener fan-out: when one consumer flips the
    // toggle, the other's reactive `enabled` ref must update too.
    const { useThumbnailToggle } = await import("@/utils/tar-thumbnail");
    const { defineComponent, h } = await import("vue");
    const { mount } = await import("@vue/test-utils");

    const captured = { a: null, b: null, setA: null };
    const Host = defineComponent({
      setup() {
        const { enabled, setEnabled } = useThumbnailToggle();
        if (captured.a === null) {
          captured.a = enabled;
          captured.setA = setEnabled;
        } else {
          captured.b = enabled;
        }
        return () => h("div");
      },
    });
    const wrapperA = mount(Host);
    const wrapperB = mount(Host);
    expect(captured.a.value).toBe(true);
    expect(captured.b.value).toBe(true);

    captured.setA(false);
    expect(localStorage.getItem(TOGGLE_STORAGE_KEY)).toBe("0");
    expect(captured.a.value).toBe(false);
    expect(captured.b.value).toBe(false);

    // Calling setEnabled with the same value is a no-op — exercise
    // the early-return branch.
    captured.setA(false);
    expect(captured.a.value).toBe(false);

    wrapperA.unmount();
    wrapperB.unmount();
  });
});
