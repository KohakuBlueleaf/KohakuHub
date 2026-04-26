// src/kohaku-hub-ui/src/utils/http-errors.js
//
// Shared HTTP error classification.
//
// KohakuHub's backend encodes HF-compatible error classification on
// 4xx / 5xx responses via the `X-Error-Code` header (GatedRepo,
// EntryNotFound, RepoNotFound, RevisionNotFound — see
// `src/kohakuhub/api/repo/utils/hf.py`), plus an `X-Error-Message`
// header carrying a human-readable summary. When the fallback
// aggregate machinery in `src/kohakuhub/api/fallback/utils.py`
// collapses multiple upstream failures into one response, it adds a
// `{error, detail, sources[]}` JSON body carrying per-source probe
// details.
//
// Before this helper existed, only `FilePreviewDialog.vue` knew how
// to read this data — every other error UX in the SPA fell back to
// generic "File Not Found" copy even when the real cause was an
// upstream gate or a transient 5xx. This module centralizes the
// read path so blob pages, edit pages, tree navigation, and the
// Dialog all classify errors the same way.
//
// The classification is intentionally coarse — it maps to the set
// of remediations the UI can offer, not the full HF exception
// hierarchy:
//
//   "gated"                 → 401 + X-Error-Code=GatedRepo
//                             (only — bare 401 without the code means
//                              HF-compat "repo doesn't exist", see below)
//   "forbidden"             → 403
//   "not-found"             → 404 / 410, or bare 401 without GatedRepo
//                             (EntryNotFound, RepoNotFound,
//                              RevisionNotFound, bare 404, or bare 401
//                              — HF returns 401 without a code for
//                              non-existent repos as anti-enumeration,
//                              and `huggingface_hub.utils._http` maps
//                              the same shape to `RepositoryNotFoundError`;
//                              we follow the same heuristic)
//   "upstream-unavailable"  → 5xx / 502 / 503 / 504
//   "cors"                  → browser-level CORS / network failure
//                             (surfaces as TypeError "Failed to fetch")
//   "generic"               → anything else (3xx handled upstream,
//                             unknown 4xx, malformed responses)

export const ERROR_KIND = Object.freeze({
  GATED: "gated",
  FORBIDDEN: "forbidden",
  NOT_FOUND: "not-found",
  UPSTREAM_UNAVAILABLE: "upstream-unavailable",
  CORS: "cors",
  GENERIC: "generic",
});

// Per-kind default remediation copy. UI components should override
// with more specific wording when they have context (e.g. blob page
// can point a gated user at Settings → Tokens).
const DEFAULT_COPY = Object.freeze({
  [ERROR_KIND.GATED]: {
    title: "Authentication required",
    hint: "This repository gates access — attach a Hugging Face token in your account settings, then retry.",
  },
  [ERROR_KIND.FORBIDDEN]: {
    title: "Access denied by upstream",
    hint: "The upstream source refused access. The request was recognised but not authorized.",
  },
  [ERROR_KIND.NOT_FOUND]: {
    title: "Not found",
    hint: "Every configured source returned 404. The repository or file does not exist on any of them.",
  },
  [ERROR_KIND.UPSTREAM_UNAVAILABLE]: {
    title: "Upstream source unavailable",
    hint: "The fallback source(s) timed out or returned a server error. This is usually transient — retry in a few seconds.",
  },
  [ERROR_KIND.CORS]: {
    title: "Browser blocked the request",
    hint: "This looks like a CORS failure on the object-storage host. Preview needs the S3/MinIO backend to advertise Access-Control-Allow-Origin. See docs/development/local-dev.md → 'MinIO CORS'.",
  },
  [ERROR_KIND.GENERIC]: {
    title: "Request failed",
    hint: "Something went wrong. Retry in a moment, and if it persists check the browser console for the raw response.",
  },
});

export function defaultCopyFor(kind) {
  return DEFAULT_COPY[kind] ?? DEFAULT_COPY[ERROR_KIND.GENERIC];
}

/**
 * Pick the right `kind` from a numeric status + optional
 * HF-compatible error code. Used by both `classifyResponse` and
 * `classifyError` so status-code semantics stay in one place.
 */
function classifyStatus(status, errorCode) {
  // GatedRepo on 401 is the canonical HF case. Honour the code when
  // present even if some middleware remapped the status.
  if (errorCode === "GatedRepo") return ERROR_KIND.GATED;
  if (
    errorCode === "EntryNotFound" ||
    errorCode === "RepoNotFound" ||
    errorCode === "RevisionNotFound"
  ) {
    return ERROR_KIND.NOT_FOUND;
  }
  // Bare 401 without X-Error-Code → HF's anti-enumeration response
  // for a non-existent repo. `huggingface_hub.utils._http` raises
  // `RepositoryNotFoundError` for the same shape; we classify as
  // NOT_FOUND so the UI shows "does not exist" instead of asking the
  // user for a token that would not help.
  if (status === 401) return ERROR_KIND.NOT_FOUND;
  if (status === 403) return ERROR_KIND.FORBIDDEN;
  if (status === 404 || status === 410) return ERROR_KIND.NOT_FOUND;
  if (typeof status === "number" && status >= 500 && status < 600) {
    return ERROR_KIND.UPSTREAM_UNAVAILABLE;
  }
  return ERROR_KIND.GENERIC;
}

/**
 * Classify a `fetch` `Response`. Returns a plain object carrying
 * everything the UI needs to render an actionable error state —
 * including the `sources[]` array from the aggregated fallback body
 * when present, for expert users who want to see which upstream
 * source answered what.
 *
 * @param {Response} response
 * @returns {Promise<{
 *   kind: string,
 *   status: (number|null),
 *   errorCode: (string|null),
 *   detail: (string|null),
 *   sources: (Array|null),
 * }>}
 */
export async function classifyResponse(response) {
  if (response == null) {
    return {
      kind: ERROR_KIND.GENERIC,
      status: null,
      errorCode: null,
      detail: null,
      sources: null,
    };
  }

  const status = typeof response.status === "number" ? response.status : null;
  // headers may be a Headers instance, a plain object, or axios's
  // AxiosHeaders wrapper — all support .get() after normalisation.
  const getHeader = (name) => {
    if (!response.headers) return null;
    if (typeof response.headers.get === "function") {
      return response.headers.get(name);
    }
    return response.headers[name] ?? response.headers[name.toLowerCase()] ?? null;
  };

  const errorCodeHeader = getHeader("x-error-code") || getHeader("X-Error-Code");
  const errorMessageHeader =
    getHeader("x-error-message") || getHeader("X-Error-Message");

  let errorCode = errorCodeHeader || null;
  let detail = errorMessageHeader || null;
  let sources = null;

  // Defensive JSON parse. We clone because callers may still want
  // to read the body themselves afterwards.
  try {
    const cloneable =
      typeof response.clone === "function" ? response.clone() : response;
    const body =
      typeof cloneable.json === "function"
        ? await cloneable.json()
        : (response.data ?? null);
    if (body && typeof body === "object") {
      if (!errorCode && typeof body.error === "string") errorCode = body.error;
      if (Array.isArray(body.sources)) sources = body.sources;
      if (!detail && typeof body.detail === "string") detail = body.detail;
    }
  } catch {
    // Not JSON, or already consumed — keep header-derived info only.
  }

  return {
    kind: classifyStatus(status, errorCode),
    status,
    errorCode,
    detail,
    sources,
  };
}

/**
 * Classify a thrown error — AxiosError, fetch `TypeError`,
 * `SafetensorsFetchError`, or a domain exception with `status` on it.
 *
 * Returns the same shape as `classifyResponse` without awaiting, so
 * callers in a `.catch()` don't have to await the classification.
 */
export function classifyError(err) {
  const blank = {
    kind: ERROR_KIND.GENERIC,
    status: null,
    errorCode: null,
    detail: null,
    sources: null,
  };
  if (!err) return blank;

  // AxiosError / anything with `.response` in the fetch-response shape.
  // We intentionally read headers synchronously here; the body /
  // sources that come from `response.data` are already parsed by
  // axios so no async step is needed.
  if (err.response) {
    const response = err.response;
    const status = typeof response.status === "number" ? response.status : null;
    const headers = response.headers || {};
    const getHeader = (name) =>
      typeof headers.get === "function"
        ? headers.get(name)
        : (headers[name] ?? headers[name.toLowerCase()] ?? null);
    let errorCode =
      getHeader("x-error-code") || getHeader("X-Error-Code") || null;
    let detail =
      getHeader("x-error-message") || getHeader("X-Error-Message") || null;
    let sources = null;
    const body = response.data;
    if (body && typeof body === "object") {
      if (!errorCode && typeof body.error === "string") errorCode = body.error;
      if (Array.isArray(body.sources)) sources = body.sources;
      if (!detail && typeof body.detail === "string") detail = body.detail;
    }
    return {
      kind: classifyStatus(status, errorCode),
      status,
      errorCode,
      detail,
      sources,
    };
  }

  // SafetensorsFetchError already went through this pipeline in
  // utils/safetensors.js — honour its attached fields if present.
  if (
    typeof err.status === "number" ||
    typeof err.errorCode === "string" ||
    Array.isArray(err.sources)
  ) {
    return {
      kind: classifyStatus(
        typeof err.status === "number" ? err.status : null,
        typeof err.errorCode === "string" ? err.errorCode : null,
      ),
      status: typeof err.status === "number" ? err.status : null,
      errorCode: typeof err.errorCode === "string" ? err.errorCode : null,
      detail:
        typeof err.detail === "string"
          ? err.detail
          : typeof err.message === "string"
            ? err.message
            : null,
      sources: Array.isArray(err.sources) ? err.sources : null,
    };
  }

  // Browser CORS / network errors reach JS as bare TypeError with
  // "Failed to fetch" (Chromium) or "NetworkError when attempting
  // to fetch resource" (Firefox). No way to distinguish from a DNS
  // failure or a totally offline network, so we flag them as `cors`
  // — the user-facing hint points at the most common cause (MinIO
  // CORS) and a Retry still works for the transient network case.
  const message = (err.message ?? "").toLowerCase();
  if (
    err.name === "TypeError" ||
    message.includes("failed to fetch") ||
    message.includes("networkerror")
  ) {
    return {
      ...blank,
      kind: ERROR_KIND.CORS,
      detail: err.message ?? null,
    };
  }

  // Abort is not user-facing; caller's AbortController triggered it.
  if (err.name === "AbortError") {
    return { ...blank, detail: "aborted" };
  }

  return { ...blank, detail: err.message ?? null };
}

/**
 * Pre-flight a URL and classify the outcome. Intended for the "am I
 * about to hand this URL to a native download / window.open" case —
 * blob-page Download button, anything that would otherwise let the
 * browser render a raw JSON error body in a new tab.
 *
 * Issues a ``GET`` with ``Range: bytes=0-0`` so that the S3/MinIO
 * presigned URL the backend 302s to is still a valid signature
 * (presigned URLs are signed for GET, not HEAD) and only a couple of
 * hundred bytes cross the wire on the happy path. The backend's
 * aggregate failure body is small enough that a full download
 * on the failure path is also cheap.
 *
 * Returns ``{ ok: true, classification: null }`` on 2xx, else
 * ``{ ok: false, classification }`` where ``classification`` is the
 * same shape classifyResponse / classifyError emit.
 *
 * ``fetchImpl`` is injectable for tests; default is the global fetch.
 */
export async function probeUrlAndClassify(url, fetchImpl) {
  const impl = fetchImpl || globalThis.fetch;
  try {
    const response = await impl(url, {
      method: "GET",
      headers: { Range: "bytes=0-0" },
      redirect: "follow",
    });
    if (response.ok) {
      return { ok: true, classification: null };
    }
    return { ok: false, classification: await classifyResponse(response) };
  } catch (err) {
    return { ok: false, classification: classifyError(err) };
  }
}

/**
 * Per-kind toast copy for the blob-page Download button. Kept here
 * next to the classification it's keyed on so the two stay in sync —
 * when a new ``ERROR_KIND`` lands, this map refuses to silently fall
 * through to "generic" without a matching caller update.
 */
export const DOWNLOAD_TOAST_HINTS = Object.freeze({
  [ERROR_KIND.GATED]:
    "This repository is gated. Attach a Hugging Face token in Settings → Tokens, then retry.",
  [ERROR_KIND.FORBIDDEN]: "Upstream source denied access to this file.",
  [ERROR_KIND.NOT_FOUND]: "No source serves this file.",
  [ERROR_KIND.UPSTREAM_UNAVAILABLE]:
    "Upstream source is unavailable right now. Retry shortly.",
  [ERROR_KIND.CORS]:
    "Download blocked by CORS on the storage host. See local-dev docs.",
  [ERROR_KIND.GENERIC]:
    "Download failed. See browser console for the raw response.",
});

export function downloadToastFor(classification) {
  if (!classification) return DOWNLOAD_TOAST_HINTS[ERROR_KIND.GENERIC];
  return (
    DOWNLOAD_TOAST_HINTS[classification.kind] ||
    DOWNLOAD_TOAST_HINTS[ERROR_KIND.GENERIC]
  );
}
