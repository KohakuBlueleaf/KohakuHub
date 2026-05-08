// src/kohaku-hub-ui/src/utils/indexed-tar.js
//
// Pure-client reader for the hfutils.index sidecar format. Mirrors the
// algorithm in hfutils.index.local_fetch (TAR + sibling JSON, member
// extraction by `seek(offset) + read(size)`), but runs entirely in the
// browser via HTTP Range requests against /resolve/.
//
// The sidecar JSON is small enough to fetch in full (member offsets +
// sizes + optional sha256 for each entry). Once parsed, the modal can
// render directory listings without further network round-trips, and a
// member is materialized via a single Range read on the .tar URL.
//
// Wire shape:
//   {
//     "filesize": <int>,
//     "hash":      <git-blob sha1>,
//     "hash_lfs":  <sha256 of the tar bytes>,
//     "files": {
//       "<path inside tar>": { "offset": int, "size": int, "sha256"?: hex }
//     }
//   }
//
// Anything missing or shape-violating raises IndexedTarFormatError so
// the modal can fall back to the shared <ErrorState>.

const SUPPORTED_TOP_LEVEL_KEYS = ["filesize", "hash", "hash_lfs", "files"];

export class IndexedTarFetchError extends Error {
  constructor(message, { status, statusText } = {}) {
    super(message);
    this.name = "IndexedTarFetchError";
    this.status = status;
    this.statusText = statusText;
  }
  static async fromResponse(response, label) {
    let body = "";
    try {
      body = (await response.text()).slice(0, 200);
    } catch {
      // ignore
    }
    return new IndexedTarFetchError(
      `Failed to fetch ${label} (${response.status} ${response.statusText})${
        body ? `: ${body}` : ""
      }`,
      { status: response.status, statusText: response.statusText },
    );
  }
}

export class IndexedTarFormatError extends Error {
  constructor(message) {
    super(message);
    this.name = "IndexedTarFormatError";
  }
}

/**
 * Fetch and parse the sidecar JSON.
 *
 * @param {string} url - /resolve/ URL of the .json sidecar.
 * @param {object} [options]
 * @param {(phase: string, detail?: object) => void} [options.onProgress]
 *   Phases:
 *     "fetch"   issuing GET on the sidecar
 *     "parsing" running JSON.parse + shape validation
 *     "done"    parsed + tree built
 * @param {AbortSignal} [options.signal]
 * @returns {Promise<{
 *   filesize: number,
 *   hash: string,
 *   hash_lfs: string,
 *   files: Record<string, {offset:number, size:number, sha256?:string}>,
 * }>}
 */
export async function parseTarIndex(url, options = {}) {
  const { onProgress = () => {}, signal } = options;

  onProgress("fetch");
  // `same-origin` forwards the SPA session cookie on the /resolve/ hop
  // — private repos return 404 without it — and the browser drops
  // cookies on the cross-origin redirect to the presigned object URL,
  // so the S3/MinIO `Access-Control-Allow-Origin: *` response stays
  // compatible with credentialed CORS.
  const response = await fetch(url, {
    signal,
    mode: "cors",
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw await IndexedTarFetchError.fromResponse(response, "tar index JSON");
  }

  const text = await response.text();

  onProgress("parsing");
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new IndexedTarFormatError(
      `Tar index JSON is not valid JSON: ${err.message}`,
    );
  }

  validateShape(parsed);

  onProgress("done");
  return parsed;
}

function validateShape(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new IndexedTarFormatError(
      "Tar index payload must be a JSON object",
    );
  }
  if (
    !payload.files ||
    typeof payload.files !== "object" ||
    Array.isArray(payload.files)
  ) {
    throw new IndexedTarFormatError(
      "Tar index payload is missing the 'files' map",
    );
  }
  for (const [path, info] of Object.entries(payload.files)) {
    if (
      !info ||
      typeof info !== "object" ||
      typeof info.offset !== "number" ||
      typeof info.size !== "number" ||
      info.offset < 0 ||
      info.size < 0
    ) {
      throw new IndexedTarFormatError(
        `Tar index entry for ${JSON.stringify(path)} is missing offset/size`,
      );
    }
  }
  // Optional but expected fields — coerce to strings so downstream code
  // can rely on them being defined.
  for (const key of SUPPORTED_TOP_LEVEL_KEYS) {
    if (key === "files") continue;
    if (payload[key] == null) payload[key] = "";
  }
}

/**
 * Build a navigable directory tree from the flat path -> info map.
 *
 * @param {Record<string, {offset:number,size:number,sha256?:string}>} files
 * @returns {{
 *   type: 'dir',
 *   name: string,
 *   path: string,
 *   size: number,
 *   fileCount: number,
 *   children: Map<string, TarTreeNode>,
 * }}
 */
export function buildTreeFromIndex(files) {
  const root = {
    type: "dir",
    name: "",
    path: "",
    size: 0,
    fileCount: 0,
    children: new Map(),
  };

  const sortedPaths = Object.keys(files).sort();
  for (const rawPath of sortedPaths) {
    const info = files[rawPath];
    const segments = normalizeSegments(rawPath);
    if (segments.length === 0) continue;

    let cursor = root;
    let collision = false;
    for (let i = 0; i < segments.length - 1; i++) {
      const seg = segments[i];
      let next = cursor.children.get(seg);
      if (!next) {
        next = {
          type: "dir",
          name: seg,
          path: segments.slice(0, i + 1).join("/"),
          size: 0,
          fileCount: 0,
          children: new Map(),
        };
        cursor.children.set(seg, next);
      } else if (next.type !== "dir") {
        // The path passes through a leaf — e.g. tar contains both
        // `a/b.txt` and `a/b.txt/extra`. Drop the extra entry to
        // keep the listing deterministic; first insertion wins.
        collision = true;
        break;
      }
      cursor = next;
    }
    if (collision) continue;

    const leafName = segments[segments.length - 1];
    if (cursor.children.has(leafName)) {
      // Two members landing on the same path is rare but possible if
      // the tar contains both 'a' and 'a/b'. Skip the second entry —
      // listing semantics keep the first deterministic insertion.
      continue;
    }

    const fileNode = {
      type: "file",
      name: leafName,
      path: segments.join("/"),
      size: info.size,
      offset: info.offset,
      sha256: info.sha256 || null,
    };
    cursor.children.set(leafName, fileNode);

    let walker = root;
    for (let i = 0; i < segments.length; i++) {
      walker.size += info.size;
      walker.fileCount += 1;
      walker =
        i < segments.length - 1 ? walker.children.get(segments[i]) : walker;
    }
  }

  return root;
}

function normalizeSegments(path) {
  if (typeof path !== "string") return [];
  return path
    .split("/")
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && s !== "." && s !== "..");
}

/**
 * Resolve a directory by walking the tree along the segments, then return
 * its children split + sorted: folders first, then files, both
 * alphabetically by name. Used by the modal's listing pane.
 *
 * @param {ReturnType<typeof buildTreeFromIndex>} root
 * @param {string[]} segments - already-normalized path segments
 * @returns {{folders: TarTreeNode[], files: TarTreeNode[], node: TarTreeNode|null}}
 */
export function listDirectory(root, segments) {
  let cursor = root;
  for (const seg of segments) {
    if (!cursor || cursor.type !== "dir") {
      return { folders: [], files: [], node: null };
    }
    cursor = cursor.children.get(seg);
  }
  if (!cursor || cursor.type !== "dir") {
    return { folders: [], files: [], node: null };
  }
  const folders = [];
  const files = [];
  for (const child of cursor.children.values()) {
    if (child.type === "dir") folders.push(child);
    else files.push(child);
  }
  folders.sort((a, b) => a.name.localeCompare(b.name));
  files.sort((a, b) => a.name.localeCompare(b.name));
  return { folders, files, node: cursor };
}

/**
 * Build the Range header value for a member.
 *
 * Empty members (size === 0) cannot use a Range header at all — the
 * "bytes=O-(O-1)" form is invalid. Caller must short-circuit and return
 * a zero-byte payload directly.
 *
 * @returns {string} e.g. "bytes=1024-2047"
 */
export function buildMemberRangeHeader(info) {
  if (info.size === 0) {
    throw new Error(
      "Cannot build Range header for a zero-byte member (caller should short-circuit)",
    );
  }
  return `bytes=${info.offset}-${info.offset + info.size - 1}`;
}

/**
 * Extract a single member from the tar via a Range read on the .tar URL.
 * Returns a Uint8Array sized exactly `info.size`.
 *
 * @param {string} tarUrl - /resolve/ URL of the .tar
 * @param {{offset:number, size:number, sha256?:string}} info
 * @param {object} [options]
 * @param {AbortSignal} [options.signal]
 * @returns {Promise<Uint8Array>}
 */
export async function extractMemberBytes(tarUrl, info, options = {}) {
  const { signal } = options;
  // Defensive: a wrapper object that drops .offset (e.g. UI state
  // copied for display) used to fall through to a Range header with
  // "undefined" in it; MinIO ignored the header and returned the full
  // tar. Surface the misuse as a clean TypeError instead of a silent
  // size mismatch the user has no way to debug.
  if (
    !info ||
    typeof info.offset !== "number" ||
    !Number.isFinite(info.offset) ||
    info.offset < 0
  ) {
    throw new TypeError(
      `extractMemberBytes: info.offset must be a non-negative number, got ${
        info ? JSON.stringify(info.offset) : "no info"
      }`,
    );
  }
  if (
    typeof info.size !== "number" ||
    !Number.isFinite(info.size) ||
    info.size < 0
  ) {
    throw new TypeError(
      `extractMemberBytes: info.size must be a non-negative number, got ${JSON.stringify(info.size)}`,
    );
  }
  if (info.size === 0) return new Uint8Array(0);

  // Same credentials story as parseTarIndex above: send the session
  // cookie on the same-origin /resolve/ hop, drop it on the cross-origin
  // redirect to the presigned tar URL.
  const response = await fetch(tarUrl, {
    headers: { Range: buildMemberRangeHeader(info) },
    signal,
    mode: "cors",
    credentials: "same-origin",
  });
  if (response.status !== 200 && response.status !== 206) {
    throw await IndexedTarFetchError.fromResponse(response, "tar member");
  }
  const buf = new Uint8Array(await response.arrayBuffer());
  if (buf.length !== info.size) {
    throw new IndexedTarFetchError(
      `Tar member range read returned ${buf.length} bytes, expected ${info.size}`,
    );
  }
  return buf;
}

/**
 * Compare a hfutils-index hash payload against the tree-API entry for the
 * .tar file. Returns one of:
 *   { kind: 'match' }                      both sha256 present and equal
 *   { kind: 'mismatch', expected, actual } both present, not equal
 *   { kind: 'unknown' }                    no usable hash on either side
 *   { kind: 'partial' }                    we have one side but not both
 *
 * The tree API surfaces sha256 as `oid` (or `lfs.oid` for LFS-stored
 * files); see api/repo/routers/tree.py:_make_tree_item. The index
 * always carries `hash_lfs` (sha256 of the tar bytes) per
 * hfutils.index.make.tar_get_index_info, so sha256 is the only side
 * we can verify reliably without re-hashing the .tar in the browser.
 */
export function compareTarHash(indexPayload, tarTreeEntry) {
  const indexSha = (indexPayload && indexPayload.hash_lfs) || "";
  const indexBlobSha = (indexPayload && indexPayload.hash) || "";
  const treeSha =
    (tarTreeEntry && tarTreeEntry.lfs && tarTreeEntry.lfs.oid) ||
    (tarTreeEntry && tarTreeEntry.oid) ||
    "";

  if (!indexSha && !indexBlobSha) return { kind: "unknown" };
  if (!treeSha) return { kind: "partial" };

  // Tree-side oid is sha256-shaped (64 hex chars). If it does not look
  // like sha256 we fall back to comparing it against the git-blob sha1
  // directly so a hand-crafted index (or an extremely small inline file
  // whose oid is the git sha1) does not trigger a false-positive
  // warning.
  const looksLikeSha256 = /^[0-9a-f]{64}$/i.test(treeSha);
  if (looksLikeSha256 && indexSha) {
    return indexSha.toLowerCase() === treeSha.toLowerCase()
      ? { kind: "match" }
      : { kind: "mismatch", expected: indexSha, actual: treeSha };
  }
  if (!looksLikeSha256 && indexBlobSha) {
    return indexBlobSha.toLowerCase() === treeSha.toLowerCase()
      ? { kind: "match" }
      : { kind: "mismatch", expected: indexBlobSha, actual: treeSha };
  }
  return { kind: "partial" };
}

/**
 * Best-effort MIME type derived from a path extension. Mirrors the
 * extension list used by the standalone blob page so an inline preview
 * inside the tar uses the same renderer as the file would standalone.
 */
export function guessMimeType(path) {
  if (typeof path !== "string") return "application/octet-stream";
  const lower = path.toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot < 0 ? "" : lower.slice(dot + 1);
  const map = {
    txt: "text/plain",
    log: "text/plain",
    csv: "text/csv",
    tsv: "text/tab-separated-values",
    json: "application/json",
    xml: "application/xml",
    yaml: "text/yaml",
    yml: "text/yaml",
    toml: "text/plain",
    md: "text/markdown",
    markdown: "text/markdown",
    html: "text/html",
    htm: "text/html",
    css: "text/css",
    js: "text/javascript",
    ts: "text/typescript",
    py: "text/x-python",
    pdf: "application/pdf",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    png: "image/png",
    gif: "image/gif",
    webp: "image/webp",
    svg: "image/svg+xml",
    bmp: "image/bmp",
    ico: "image/x-icon",
    mp4: "video/mp4",
    webm: "video/webm",
    mov: "video/quicktime",
    avi: "video/x-msvideo",
    mp3: "audio/mpeg",
    wav: "audio/wav",
    ogg: "audio/ogg",
    flac: "audio/flac",
    m4a: "audio/mp4",
    aac: "audio/aac",
    safetensors: "application/octet-stream",
    parquet: "application/octet-stream",
    tar: "application/x-tar",
  };
  return map[ext] || "application/octet-stream";
}

/**
 * High-level renderer category for a member, mirroring the buckets used
 * on the standalone blob page so the dialog can route to the same
 * renderer.
 *
 *   image | video | audio | pdf | markdown | text | safetensors | parquet | binary
 */
export function classifyMember(path) {
  if (typeof path !== "string") return "binary";
  const lower = path.toLowerCase();
  const baseName = lower.split("/").pop() || "";
  const dot = baseName.lastIndexOf(".");
  const ext = dot < 0 ? "" : baseName.slice(dot + 1);

  // README and LICENSE files are commonly checked in without an
  // extension (Unix style). The standalone blob page renders them
  // as plain text; classify them the same way so the in-archive
  // listing shows a meaningful icon and the member view picks the
  // text renderer instead of bouncing to the "binary — use download"
  // dead-end.
  if (dot < 0) {
    if (baseName === "readme") return "markdown";
    if (
      [
        "license",
        "licence",
        "copying",
        "authors",
        "contributors",
        "changelog",
        "notice",
        "dockerfile",
        "makefile",
      ].includes(baseName)
    ) {
      return "text";
    }
  }

  if (["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"].includes(ext))
    return "image";
  if (["mp4", "webm", "ogg", "mov", "avi"].includes(ext)) return "video";
  if (["mp3", "wav", "flac", "m4a", "aac"].includes(ext)) return "audio";
  if (ext === "pdf") return "pdf";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "safetensors") return "safetensors";
  if (ext === "parquet") return "parquet";
  // The standalone blob page allows a wide list of textual extensions —
  // mirror it to stay consistent. See pages/.../blob/.../[...file].vue
  // `isTextFile` for the source of truth.
  if (
    [
      "txt",
      "log",
      "csv",
      "tsv",
      "js",
      "ts",
      "jsx",
      "tsx",
      "vue",
      "py",
      "java",
      "cpp",
      "c",
      "h",
      "hpp",
      "cs",
      "go",
      "rs",
      "rb",
      "php",
      "swift",
      "kt",
      "scala",
      "r",
      "jl",
      "html",
      "htm",
      "css",
      "scss",
      "sass",
      "less",
      "json",
      "xml",
      "yaml",
      "yml",
      "toml",
      "ini",
      "cfg",
      "conf",
      "sh",
      "bash",
      "zsh",
      "fish",
      "ps1",
      "bat",
      "cmd",
      "sql",
      "graphql",
      "proto",
      "dockerfile",
      "makefile",
      "gitignore",
      "env",
      "editorconfig",
    ].includes(ext)
  ) {
    return "text";
  }
  return "binary";
}

/**
 * Trigger a browser-native download whose filename matches the in-tar
 * basename. Uses an object URL because the .tar /resolve/ endpoint has
 * no Content-Disposition for sub-ranges; a manufactured anchor with a
 * `download` attribute is the only reliable way to lock the filename.
 */
export function downloadBytesAs(bytes, filename, mime) {
  const blob = new Blob([bytes], { type: mime || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // 60 s gives Chromium time to start the download before the URL is
  // revoked. revokeObjectURL inside the click handler synchronously
  // cancels the download in some Chromium versions.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/**
 * Sibling-aware predicate: a `.tar` row in the file list should only
 * light the indexed-tar icon when the same listing already contains a
 * `<basename>.json` sibling. Bare tars (no sidecar) stay untreated.
 *
 * `siblings` is an iterable of file-tree entries from the same listing.
 * The predicate is path-shape-only; it does not validate the JSON shape
 * (which the modal does on click via parseTarIndex).
 */
export function hasIndexSibling(tarPath, siblings) {
  if (typeof tarPath !== "string" || !tarPath.toLowerCase().endsWith(".tar"))
    return false;
  const dot = tarPath.lastIndexOf(".");
  if (dot < 0) return false;
  const base = tarPath.slice(0, dot);
  const wanted = `${base}.json`;
  if (!siblings || typeof siblings[Symbol.iterator] !== "function")
    return false;
  for (const entry of siblings) {
    if (!entry || entry.type === "directory") continue;
    if (typeof entry.path !== "string") continue;
    if (entry.path === wanted) return true;
  }
  return false;
}

/**
 * Compute the expected `.json` sidecar path for a given `.tar` path.
 * Returns null when `tarPath` is not a `.tar` file (case-insensitive),
 * so callers can early-out without re-running the suffix check.
 */
export function tarSidecarPath(tarPath) {
  if (typeof tarPath !== "string" || !tarPath.toLowerCase().endsWith(".tar"))
    return null;
  const dot = tarPath.lastIndexOf(".");
  if (dot < 0) return null;
  return `${tarPath.slice(0, dot)}.json`;
}

/**
 * Async sibling check that first looks at the loaded listing
 * (`hasIndexSibling`) and, on a miss, asks `probe(jsonPath)` to
 * confirm existence over the network. Returns true only when one of
 * the two paths agrees the sidecar is present.
 *
 * The repo file list is now paginated, so a `.tar` and its `.json`
 * sibling can land on different pages — without the probe the icon
 * would silently not light up on the second page. Caller is expected
 * to memoize positive results so a back-and-forth between pages does
 * not re-issue the HEAD.
 *
 * `probe` must return a Promise<boolean>. Throws or rejects from the
 * probe degrade to "no sidecar" (false) so a transient network error
 * never blocks the row from rendering.
 *
 * @param {string} tarPath
 * @param {Iterable|null} siblings
 * @param {(jsonPath: string) => Promise<boolean>} probe
 * @returns {Promise<boolean>}
 */
export async function hasIndexSiblingWithProbe(tarPath, siblings, probe) {
  if (hasIndexSibling(tarPath, siblings)) return true;
  if (typeof probe !== "function") return false;
  const jsonPath = tarSidecarPath(tarPath);
  if (!jsonPath) return false;
  try {
    return Boolean(await probe(jsonPath));
  } catch {
    return false;
  }
}
