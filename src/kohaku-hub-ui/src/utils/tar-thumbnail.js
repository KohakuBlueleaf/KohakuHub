// src/kohaku-hub-ui/src/utils/tar-thumbnail.js
//
// Pure-client thumbnail extraction for image members inside an
// hfutils.index TAR. Goal: render a small (~128 px) JPEG preview in
// the listing while reading the smallest possible byte window from
// the .tar — never just blindly Range-read the whole file.
//
// Strategy chain (first non-null wins):
//
//   1. jpeg-exif       — Range-read first 64 KB; parse JPEG markers;
//                        if APP1 carries a thumbnail in IFD1, return
//                        the embedded JPEG bytes (typically 5-15 KB).
//                        Bandwidth: 64 KB regardless of source size.
//
//   2. small-image     — for size ≤ 256 KB. Range-read full bytes,
//                        decode + resize via canvas. Bandwidth: size.
//
//   3. medium-image    — for size ≤ 5 MB. Same as small but with the
//                        head bytes already cached from the EXIF probe
//                        — only the remainder is fetched.
//                        Bandwidth: size (one-time, with cache reuse).
//
// > 5 MB: no strategy matches. Caller must show the placeholder icon.
//
// Failure semantics: every strategy resolves to either a Blob (the
// final small thumbnail) or null (didn't apply / didn't find what it
// needed). Errors thrown from inside the chain bubble up to the
// composable, which converts them to a `fallback` state. The placeholder
// icon then stays in place — never any toast, never any thrown render.
//
// Future formats (video / safetensors-as-thumb / etc.) can be added by
// pushing additional strategies into the registry; the orchestrator
// doesn't care about which side of the registry handled the member.

import { ref, onMounted, onUnmounted } from "vue";
import { extractMemberBytes } from "@/utils/indexed-tar";

// -----------------------------------------------------------------------
// Tunables
// -----------------------------------------------------------------------

export const HEAD_BYTES = 64 * 1024;
export const SMALL_IMAGE_BYTES = 256 * 1024;
export const MEDIUM_IMAGE_BYTES = 5 * 1024 * 1024;
// Output thumbnail bound. Bigger than necessary at 128 px (the
// in-listing images would render too small inside the grid card's
// ~200-px square slot, leaving a halo of empty space). 256 px keeps
// the downscale crisp on the larger grid cards while staying under
// ~25 KB per cached thumbnail.
export const THUMB_MAX_DIM = 256;
export const THUMB_QUALITY = 0.78;
export const THUMB_OUTPUT_MIME = "image/jpeg";
export const POOL_CONCURRENCY = 4;
export const CACHE_MAX_ENTRIES = 100;
export const TOGGLE_STORAGE_KEY = "kohaku-tar-thumbnail-enabled";

// -----------------------------------------------------------------------
// MIME sniffing
// -----------------------------------------------------------------------

const IMAGE_EXTENSIONS = new Set([
  "jpg",
  "jpeg",
  "png",
  "gif",
  "webp",
  "bmp",
  "ico",
]);

export function isImageMember(member) {
  if (!member || typeof member.name !== "string") return false;
  const dot = member.name.lastIndexOf(".");
  if (dot < 0) return false;
  const ext = member.name.slice(dot + 1).toLowerCase();
  return IMAGE_EXTENSIONS.has(ext);
}

export function detectImageMime(bytes) {
  if (!bytes || bytes.byteLength < 4) return null;
  const b = bytes;
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (
    b[0] === 0x89 &&
    b[1] === 0x50 &&
    b[2] === 0x4e &&
    b[3] === 0x47 &&
    b[4] === 0x0d &&
    b[5] === 0x0a &&
    b[6] === 0x1a &&
    b[7] === 0x0a
  ) {
    return "image/png";
  }
  if (
    b.byteLength >= 12 &&
    b[0] === 0x52 &&
    b[1] === 0x49 &&
    b[2] === 0x46 &&
    b[3] === 0x46 &&
    b[8] === 0x57 &&
    b[9] === 0x45 &&
    b[10] === 0x42 &&
    b[11] === 0x50
  ) {
    return "image/webp";
  }
  if (b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x38) {
    return "image/gif";
  }
  return null;
}

// -----------------------------------------------------------------------
// EXIF thumbnail parser
// -----------------------------------------------------------------------

/**
 * Walk JPEG markers in a Uint8Array and return the bytes of the
 * embedded EXIF thumbnail JPEG, or null if nothing is found.
 *
 * Parses only what the chain needs to reach IFD1's
 * JPEGInterchangeFormat / JPEGInterchangeFormatLength tags
 * (0x0201 / 0x0202). All malformed inputs cause a graceful null
 * return; the caller treats null as "this strategy did not apply".
 */
export function parseExifThumbnail(bytes) {
  if (!bytes || bytes.byteLength < 4) return null;
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8) return null; // not JPEG SOI

  // Walk markers until APP1.
  let pos = 2;
  while (pos + 4 <= bytes.byteLength) {
    if (bytes[pos] !== 0xff) return null;
    const marker = bytes[pos + 1];
    if (marker === 0xda || marker === 0xd9) return null; // SOS / EOI: no APP1 from here on
    const segLen = (bytes[pos + 2] << 8) | bytes[pos + 3];
    if (segLen < 2) return null;
    if (pos + 2 + segLen > bytes.byteLength) return null;

    if (marker === 0xe1) {
      // APP1. Confirm "Exif\0\0" magic.
      const start = pos + 4;
      if (
        start + 6 > bytes.byteLength ||
        bytes[start] !== 0x45 ||
        bytes[start + 1] !== 0x78 ||
        bytes[start + 2] !== 0x69 ||
        bytes[start + 3] !== 0x66 ||
        bytes[start + 4] !== 0x00 ||
        bytes[start + 5] !== 0x00
      ) {
        // Some other APP1 (XMP for instance). Continue scanning.
        pos += 2 + segLen;
        continue;
      }
      const tiffStart = start + 6;
      return extractEmbeddedThumbFromTiff(bytes, tiffStart, pos + 2 + segLen);
    }
    pos += 2 + segLen;
  }
  return null;
}

function extractEmbeddedThumbFromTiff(bytes, tiffStart, segEnd) {
  if (tiffStart + 8 > segEnd) return null;
  const bo0 = bytes[tiffStart];
  const bo1 = bytes[tiffStart + 1];
  let little;
  if (bo0 === 0x49 && bo1 === 0x49) little = true; // "II"
  else if (bo0 === 0x4d && bo1 === 0x4d) little = false; // "MM"
  else return null;

  const u16 = (off) => readU16(bytes, off, little);
  const u32 = (off) => readU32(bytes, off, little);

  if (u16(tiffStart + 2) !== 0x002a) return null;
  const ifd0Off = u32(tiffStart + 4);
  if (tiffStart + ifd0Off + 2 > segEnd) return null;
  const ifd0Count = u16(tiffStart + ifd0Off);
  const ifd0End = tiffStart + ifd0Off + 2 + ifd0Count * 12;
  if (ifd0End + 4 > segEnd) return null;
  const ifd1Off = u32(ifd0End);
  if (ifd1Off === 0) return null;
  const ifd1Abs = tiffStart + ifd1Off;
  if (ifd1Abs + 2 > segEnd) return null;
  const ifd1Count = u16(ifd1Abs);
  if (ifd1Abs + 2 + ifd1Count * 12 > segEnd) return null;

  let thumbOff = null;
  let thumbLen = null;
  for (let i = 0; i < ifd1Count; i++) {
    const entry = ifd1Abs + 2 + i * 12;
    const tag = u16(entry);
    const value = u32(entry + 8);
    if (tag === 0x0201) thumbOff = value; // JPEGInterchangeFormat
    else if (tag === 0x0202) thumbLen = value; // JPEGInterchangeFormatLength
  }
  if (thumbOff == null || thumbLen == null || thumbLen <= 0) return null;
  const absStart = tiffStart + thumbOff;
  const absEnd = absStart + thumbLen;
  if (absEnd > segEnd) return null;
  // Sanity-check the embedded payload looks like JPEG so we don't hand
  // a garbage Blob to <img>.
  if (
    bytes[absStart] !== 0xff ||
    bytes[absStart + 1] !== 0xd8 ||
    bytes[absEnd - 2] !== 0xff ||
    bytes[absEnd - 1] !== 0xd9
  ) {
    return null;
  }
  return bytes.subarray(absStart, absEnd);
}

function readU16(bytes, off, little) {
  return little
    ? bytes[off] | (bytes[off + 1] << 8)
    : (bytes[off] << 8) | bytes[off + 1];
}
function readU32(bytes, off, little) {
  return little
    ? (bytes[off] |
        (bytes[off + 1] << 8) |
        (bytes[off + 2] << 16) |
        (bytes[off + 3] << 24)) >>>
        0
    : ((bytes[off] << 24) |
        (bytes[off + 1] << 16) |
        (bytes[off + 2] << 8) |
        bytes[off + 3]) >>>
        0;
}

// -----------------------------------------------------------------------
// Concurrency pool
// -----------------------------------------------------------------------

class TaskPool {
  constructor(maxConcurrent) {
    this.max = maxConcurrent;
    this.running = 0;
    this.waiters = [];
  }

  async run(taskFn) {
    if (this.running >= this.max) {
      await new Promise((resolve) => this.waiters.push(resolve));
    }
    this.running += 1;
    try {
      return await taskFn();
    } finally {
      this.running -= 1;
      const next = this.waiters.shift();
      if (next) next();
    }
  }
}

const defaultPool = new TaskPool(POOL_CONCURRENCY);

// Exported for unit-testing the queue behaviour directly.
export function _createPool(maxConcurrent) {
  return new TaskPool(maxConcurrent);
}

// -----------------------------------------------------------------------
// LRU cache (key → blob URL of the rendered thumbnail)
// -----------------------------------------------------------------------

class ThumbnailCache {
  constructor(max) {
    this.max = max;
    this.map = new Map();
  }

  cacheKey(tarUrl, member) {
    return `${tarUrl} ${member.offset} ${member.size}`;
  }

  get(tarUrl, member) {
    const key = this.cacheKey(tarUrl, member);
    const value = this.map.get(key);
    if (value === undefined) return undefined;
    // LRU: re-insert to bump recency.
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  set(tarUrl, member, blobUrl) {
    const key = this.cacheKey(tarUrl, member);
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, blobUrl);
    while (this.map.size > this.max) {
      const oldestKey = this.map.keys().next().value;
      const oldestUrl = this.map.get(oldestKey);
      this.map.delete(oldestKey);
      // Reclaim object URLs as they leave the cache.
      try {
        URL.revokeObjectURL(oldestUrl);
      } catch {
        // ignore — jsdom raises in some versions
      }
    }
  }

  size() {
    return this.map.size;
  }
}

let defaultCache = new ThumbnailCache(CACHE_MAX_ENTRIES);

export function _createCache(max) {
  return new ThumbnailCache(max);
}

/** Test helper: drop every cached thumbnail blob URL. */
export function _resetThumbnailCache() {
  defaultCache = new ThumbnailCache(CACHE_MAX_ENTRIES);
}

// -----------------------------------------------------------------------
// Extraction context — shared between strategies on a single attempt
// -----------------------------------------------------------------------

class ExtractionContext {
  constructor({ tarUrl, member, signal }) {
    this.tarUrl = tarUrl;
    this.member = member;
    this.signal = signal;
    this._head = null;
    this._full = null;
  }

  /** Range-read the first up-to-`bytes` bytes of the member, cached. */
  async getHead(bytes = HEAD_BYTES) {
    if (this._full) return this._full.subarray(0, Math.min(bytes, this._full.byteLength));
    if (this._head && this._head.byteLength >= Math.min(bytes, this.member.size)) {
      return this._head.subarray(0, Math.min(bytes, this._head.byteLength));
    }
    const headSize = Math.min(bytes, this.member.size);
    if (headSize === 0) return new Uint8Array(0);
    const slice = await extractMemberBytes(
      this.tarUrl,
      { offset: this.member.offset, size: headSize },
      { signal: this.signal },
    );
    this._head = slice;
    return slice;
  }

  /** Range-read the full member bytes, reusing whatever was already cached. */
  async getFull() {
    if (this._full) return this._full;
    if (this.member.size === 0) {
      this._full = new Uint8Array(0);
      return this._full;
    }
    const slice = await extractMemberBytes(
      this.tarUrl,
      { offset: this.member.offset, size: this.member.size },
      { signal: this.signal },
    );
    this._full = slice;
    return slice;
  }
}

// -----------------------------------------------------------------------
// Strategy registry
// -----------------------------------------------------------------------

/** @typedef {{name:string, match:(member)=>boolean, extract:(ctx)=>Promise<Blob|null>}} Strategy */

/**
 * Strategy 1 — JPEG EXIF thumbnail probe. Reads only the head bytes; if
 * an embedded thumbnail is present, returns it as a Blob ready to wrap
 * in an `<img>`. Saves up to 99% of bandwidth on EXIF-bearing JPEGs.
 */
const jpegExifStrategy = {
  name: "jpeg-exif",
  match: (member) => {
    const dot = member.name.lastIndexOf(".");
    const ext = dot < 0 ? "" : member.name.slice(dot + 1).toLowerCase();
    return ext === "jpg" || ext === "jpeg";
  },
  async extract(ctx) {
    const head = await ctx.getHead();
    const mime = detectImageMime(head);
    if (mime !== "image/jpeg") return null;
    const thumbBytes = parseExifThumbnail(head);
    if (!thumbBytes) return null;
    return new Blob([thumbBytes], { type: "image/jpeg" });
  },
};

/**
 * Strategy 2 — full read + canvas resize, gated by SMALL_IMAGE_BYTES.
 * Always tried for any image format under the small cap.
 */
const smallImageStrategy = {
  name: "small-image",
  match: (member) => isImageMember(member) && member.size <= SMALL_IMAGE_BYTES,
  async extract(ctx) {
    const bytes = await ctx.getFull();
    if (bytes.byteLength === 0) return null;
    const mime = detectImageMime(bytes);
    if (!mime) return null;
    return await decodeImageToThumbnail(bytes, mime);
  },
};

/**
 * Strategy 3 — full read + canvas resize, gated by MEDIUM_IMAGE_BYTES.
 * Applies for images between SMALL and MEDIUM. Uses the same body the
 * smaller tier does, but the cost is paid only when the EXIF probe
 * above didn't find anything.
 */
const mediumImageStrategy = {
  name: "medium-image",
  match: (member) =>
    isImageMember(member) &&
    member.size > SMALL_IMAGE_BYTES &&
    member.size <= MEDIUM_IMAGE_BYTES,
  async extract(ctx) {
    const bytes = await ctx.getFull();
    if (bytes.byteLength === 0) return null;
    const mime = detectImageMime(bytes);
    if (!mime) return null;
    return await decodeImageToThumbnail(bytes, mime);
  },
};

const STRATEGIES = [jpegExifStrategy, smallImageStrategy, mediumImageStrategy];

export const _STRATEGIES = STRATEGIES; // for unit-testing the registry

// -----------------------------------------------------------------------
// Decoder — bytes → resized JPEG thumbnail blob
// -----------------------------------------------------------------------

/**
 * Decode an image buffer via the browser, draw it into a canvas at
 * THUMB_MAX_DIM-bounded dimensions, and return a JPEG-encoded Blob of
 * the result. The source object URL is revoked as soon as the canvas
 * has consumed the image so memory only holds the small thumbnail.
 */
export async function decodeImageToThumbnail(bytes, mime) {
  const sourceBlob = new Blob([bytes], { type: mime });
  const sourceUrl = URL.createObjectURL(sourceBlob);
  let img;
  try {
    img = await loadImageElement(sourceUrl);
  } finally {
    // Revoke regardless of success — the bitmap (if any) has been
    // copied into the <img>'s decoded backing store by the time
    // onload fires, so the URL is no longer needed.
    URL.revokeObjectURL(sourceUrl);
  }

  const { naturalWidth: w0, naturalHeight: h0 } = img;
  if (!w0 || !h0) throw new Error("decoded image has zero dimensions");

  const ratio = Math.min(THUMB_MAX_DIM / w0, THUMB_MAX_DIM / h0, 1);
  const w = Math.max(1, Math.round(w0 * ratio));
  const h = Math.max(1, Math.round(h0 * ratio));

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d context unavailable");
  ctx.drawImage(img, 0, 0, w, h);

  return await new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else reject(new Error("canvas.toBlob produced null"));
      },
      THUMB_OUTPUT_MIME,
      THUMB_QUALITY,
    );
  });
}

function loadImageElement(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("image failed to decode"));
    img.src = src;
  });
}

// -----------------------------------------------------------------------
// Orchestrator — runs the strategy chain inside the pool
// -----------------------------------------------------------------------

export async function extractThumbnail({ tarUrl, member, signal, pool, cache }) {
  const c = cache || defaultCache;
  const cached = c.get(tarUrl, member);
  if (cached) return cached;

  const p = pool || defaultPool;
  return await p.run(async () => {
    if (signal?.aborted) {
      const err = new Error("aborted");
      err.name = "AbortError";
      throw err;
    }

    const ctx = new ExtractionContext({ tarUrl, member, signal });
    let resultBlob = null;
    for (const strategy of STRATEGIES) {
      if (signal?.aborted) {
        const err = new Error("aborted");
        err.name = "AbortError";
        throw err;
      }
      if (!strategy.match(member)) continue;
      const out = await strategy.extract(ctx);
      if (out) {
        resultBlob = out;
        break;
      }
    }
    if (!resultBlob) return null;
    const url = URL.createObjectURL(resultBlob);
    c.set(tarUrl, member, url);
    return url;
  });
}

// -----------------------------------------------------------------------
// Toggle composable — global on/off, persisted in localStorage
// -----------------------------------------------------------------------

const toggleListeners = new Set();
let toggleCurrent = readToggleFromStorage();

function readToggleFromStorage() {
  try {
    const raw = localStorage.getItem(TOGGLE_STORAGE_KEY);
    return raw !== "0"; // default ON
  } catch {
    return true;
  }
}

function writeToggleToStorage(value) {
  try {
    localStorage.setItem(TOGGLE_STORAGE_KEY, value ? "1" : "0");
  } catch {
    // storage may be blocked (private mode); the runtime ref still
    // works, just doesn't persist.
  }
}

export function useThumbnailToggle() {
  // Re-read storage at instantiation so a test that primes
  // localStorage *before* mounting picks up the prepared value.
  // Production-side this is also harmless: the value is only read
  // once per composable call.
  toggleCurrent = readToggleFromStorage();
  const enabled = ref(toggleCurrent);

  const listener = (next) => {
    enabled.value = next;
  };
  onMounted(() => toggleListeners.add(listener));
  onUnmounted(() => toggleListeners.delete(listener));

  function setEnabled(next) {
    const v = !!next;
    if (v === toggleCurrent) {
      enabled.value = v;
      return;
    }
    toggleCurrent = v;
    writeToggleToStorage(v);
    for (const l of toggleListeners) l(v);
    enabled.value = v;
  }

  return { enabled, setEnabled };
}

/** Test helper: reset toggle state in module scope + storage. */
export function _resetThumbnailToggle() {
  try {
    localStorage.removeItem(TOGGLE_STORAGE_KEY);
  } catch {
    // ignore
  }
  toggleCurrent = true;
  for (const l of toggleListeners) l(true);
}

// -----------------------------------------------------------------------
// Composable for the row component
// -----------------------------------------------------------------------

/**
 * Vue composable that drives a single image row's thumbnail lifecycle.
 * The caller passes a ref to the row's bounding element and the panel's
 * (tarUrl, member). The composable wires up an IntersectionObserver,
 * kicks off extraction the first time the row becomes visible, aborts
 * if the row scrolls out before extraction completes, and returns
 * reactive `state` + `thumbUrl` refs the template can render against.
 */
export function useTarThumbnail({ tarUrl, member, rootRef }) {
  const state = ref("idle"); // idle | loading | ready | fallback
  const thumbUrl = ref(null);
  let observer = null;
  let controller = null;

  const handleVisible = () => {
    if (state.value !== "idle") return;
    const cached = defaultCache.get(tarUrl, member);
    if (cached) {
      thumbUrl.value = cached;
      state.value = "ready";
      return;
    }
    state.value = "loading";
    controller = new AbortController();
    extractThumbnail({ tarUrl, member, signal: controller.signal })
      .then((url) => {
        if (controller && controller.signal.aborted) return;
        if (url) {
          thumbUrl.value = url;
          state.value = "ready";
        } else {
          state.value = "fallback";
        }
      })
      .catch((err) => {
        if (err?.name === "AbortError") {
          // The row scrolled out before extraction finished. Stay
          // in `loading` so a future intersect re-tries.
          state.value = "idle";
          return;
        }
        // Anything else (network, decode, OOM): silent fallback.
        // eslint-disable-next-line no-console
        console.debug(
          "[tar-thumbnail] extraction failed",
          { path: member?.path, err: err?.message },
        );
        state.value = "fallback";
      });
  };

  const cancelInflight = () => {
    if (controller && !controller.signal.aborted) controller.abort();
    controller = null;
    if (state.value === "loading") state.value = "idle";
  };

  onMounted(() => {
    if (!rootRef.value) return;
    if (typeof IntersectionObserver === "undefined") {
      // Server-rendered or extremely old test runtime — skip lazy
      // gate and load eagerly. Still cheap; just no scroll savings.
      handleVisible();
      return;
    }
    observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) handleVisible();
        else cancelInflight();
      }
    });
    observer.observe(rootRef.value);
  });

  onUnmounted(() => {
    if (observer) observer.disconnect();
    cancelInflight();
  });

  // No prop-retarget watch: the consumer (TarBrowserPanel) keys
  // image rows by `entry.path`, so a member change at the same
  // DOM slot tears the component down and remounts a fresh one
  // anyway. A watch here would just be dead code.

  return { state, thumbUrl };
}
