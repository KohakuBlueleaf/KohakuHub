// src/kohaku-hub-ui/src/utils/safetensors.js
//
// Pure-client safetensors header parser. Mirrors the wire contract of
// huggingface_hub.HfApi.parse_safetensors_file_metadata
// (huggingface_hub/src/huggingface_hub/hf_api.py around 6491-6561):
//
//   1. Speculative Range `bytes=0-100000` — HF's constant, chosen because
//      the header for ~97% of real safetensors fits under 100 KB.
//   2. First 8 bytes are a little-endian u64 header length.
//   3. If the header fits in the first 100 KB, slice it out; otherwise
//      issue a second Range `bytes=8-<headerLen+7>` and parse that.
//
// Returns `{ metadata, tensors }` where:
//   metadata: optional free-form __metadata__ block from the header JSON
//             (dict<str, str> or null)
//   tensors:  { [name]: { dtype, shape, data_offsets, parameters } }
//             `parameters` is derived client-side as product(shape) so the
//             UI can bucket by dtype and sum totals without a second pass.
//
// `fetch()` is used directly (not the axios `api` helper) because:
//   - axios interceptors re-attach auth cookies that would defeat the CORS
//     `*` preflight on presigned S3/MinIO URLs
//   - the browser follows the backend 302 to the presigned URL
//     transparently, preserving the Range request header per RFC 7231 §6.4.3

const SAFETENSORS_FIRST_READ_BYTES = 100000; // HF constant
const SAFETENSORS_MAX_HEADER_LENGTH = 100 * 1024 * 1024; // HF constant

const SAFETENSORS_DTYPE_SIZES = {
  F64: 8,
  F32: 4,
  F16: 2,
  BF16: 2,
  I64: 8,
  I32: 4,
  I16: 2,
  I8: 1,
  U64: 8,
  U32: 4,
  U16: 2,
  U8: 1,
  F8_E4M3: 1,
  F8_E5M2: 1,
  BOOL: 1,
};

/**
 * Parse a safetensors file's header via HTTP Range reads.
 *
 * @param {string} url - Absolute or same-origin `/resolve/...` URL.
 *                       The 302 to the presigned object is followed
 *                       transparently by the browser.
 * @param {object} [options]
 * @param {(phase: string, detail?: object) => void} [options.onProgress]
 *     Called as the parser moves through phases so the preview modal can
 *     narrate progress:
 *       "range-head"      issuing first Range read (100 KB)
 *       "range-full"      header is fat, issuing second Range
 *       "parsing"         JSON.parse of the header block
 *       "done"            parsed payload ready
 * @param {AbortSignal} [options.signal] - forwarded to fetch.
 * @returns {Promise<{ metadata: object|null, tensors: object }>}
 */
export async function parseSafetensorsMetadata(url, options = {}) {
  const { onProgress = () => {}, signal } = options;

  onProgress("range-head", { bytes: SAFETENSORS_FIRST_READ_BYTES });
  const firstResp = await fetch(url, {
    headers: { Range: `bytes=0-${SAFETENSORS_FIRST_READ_BYTES}` },
    signal,
    // `cors` is the default; explicit for clarity. Presigned URLs do not
    // need cookies and sending them would break the Allow-Credentials
    // contract downstream.
    mode: "cors",
    credentials: "omit",
  });
  if (firstResp.status !== 200 && firstResp.status !== 206) {
    throw await SafetensorsFetchError.fromResponse(firstResp);
  }

  const firstBuf = await firstResp.arrayBuffer();
  if (firstBuf.byteLength < 8) {
    throw new SafetensorsFormatError(
      `Truncated response (${firstBuf.byteLength} bytes), expected at least 8 for header length prefix`,
    );
  }

  const headerLen = Number(
    new DataView(firstBuf).getBigUint64(0, /* littleEndian */ true),
  );
  if (!Number.isFinite(headerLen) || headerLen < 0) {
    throw new SafetensorsFormatError(
      `Invalid header length prefix: ${headerLen}`,
    );
  }
  if (headerLen > SAFETENSORS_MAX_HEADER_LENGTH) {
    throw new SafetensorsFormatError(
      `Safetensors header too large: ${headerLen} > ${SAFETENSORS_MAX_HEADER_LENGTH}`,
    );
  }

  let headerBytes;
  if (headerLen + 8 <= firstBuf.byteLength) {
    headerBytes = new Uint8Array(firstBuf, 8, headerLen);
  } else {
    onProgress("range-full", { bytes: headerLen });
    const secondResp = await fetch(url, {
      headers: { Range: `bytes=8-${headerLen + 7}` },
      signal,
      mode: "cors",
      credentials: "omit",
    });
    if (secondResp.status !== 200 && secondResp.status !== 206) {
      throw await SafetensorsFetchError.fromResponse(secondResp);
    }
    const secondBuf = await secondResp.arrayBuffer();
    if (secondBuf.byteLength < headerLen) {
      throw new SafetensorsFormatError(
        `Truncated header response: got ${secondBuf.byteLength}, expected ${headerLen}`,
      );
    }
    headerBytes = new Uint8Array(secondBuf, 0, headerLen);
  }

  onProgress("parsing");
  let raw;
  try {
    raw = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(headerBytes));
  } catch (err) {
    throw new SafetensorsFormatError(
      `Header is not valid UTF-8 JSON: ${err.message}`,
    );
  }
  if (!raw || typeof raw !== "object") {
    throw new SafetensorsFormatError("Header JSON is not an object");
  }

  const metadata = raw.__metadata__ ?? null;
  const tensors = {};
  for (const [name, entry] of Object.entries(raw)) {
    if (name === "__metadata__") continue;
    if (!entry || typeof entry !== "object") continue;
    const shape = Array.isArray(entry.shape) ? entry.shape.map(Number) : [];
    const parameters = shape.reduce((acc, dim) => acc * dim, 1);
    tensors[name] = {
      dtype: String(entry.dtype),
      shape,
      data_offsets: Array.isArray(entry.data_offsets)
        ? entry.data_offsets.map(Number)
        : [0, 0],
      parameters,
    };
  }

  onProgress("done");
  return { metadata, tensors };
}

/**
 * Aggregate dtype buckets + total parameter count from a parsed header.
 * Shape-compatible with HF's `?expand[]=safetensors` response
 * (`{ parameters: {<DTYPE>: <count>}, total: <sum>, byte_size: <sum> }`),
 * except computed client-side instead of precomputed server-side.
 *
 * `byte_size` is our own extension — handy so the modal can show how
 * much disk the shard actually takes; HF does not emit this.
 */
export function summarizeSafetensors(header) {
  const parameters = {};
  let total = 0;
  let byteSize = 0;
  for (const entry of Object.values(header.tensors)) {
    parameters[entry.dtype] = (parameters[entry.dtype] ?? 0) + entry.parameters;
    total += entry.parameters;
    const dtSize = SAFETENSORS_DTYPE_SIZES[entry.dtype];
    if (dtSize) byteSize += entry.parameters * dtSize;
  }
  return { parameters, total, byte_size: byteSize };
}

export class SafetensorsFetchError extends Error {
  constructor(message, status, { errorCode = null, sources = null, detail = null } = {}) {
    super(message);
    this.name = "SafetensorsFetchError";
    this.status = status;
    // huggingface_hub-style classification (populated for fallback
    // aggregate errors). `errorCode` is the `X-Error-Code` header value
    // and is also present as `sources[].error` when the backend
    // returned our structured fallback failure body. Null for ordinary
    // 4xx / 5xx.
    this.errorCode = errorCode;
    this.sources = sources;
    this.detail = detail;
  }

  static async fromResponse(response) {
    // Defensive: tolerate missing body / non-JSON errors. HF upstream
    // sometimes replies with a plain text 401 body, and our aggregate
    // failure body is JSON. Try JSON first, fall back to header/text.
    const status = response.status;
    const errorCodeHeader = response.headers.get("x-error-code") || null;
    const errorMessageHeader = response.headers.get("x-error-message") || null;

    let errorCode = errorCodeHeader;
    let sources = null;
    let detail = errorMessageHeader;

    try {
      const body = await response.clone().json();
      if (body && typeof body === "object") {
        if (!errorCode && typeof body.error === "string") errorCode = body.error;
        if (Array.isArray(body.sources)) sources = body.sources;
        if (!detail && typeof body.detail === "string") detail = body.detail;
      }
    } catch {
      // Not JSON, or empty body — keep header-derived info only.
    }

    const message = detail || `Range read failed: HTTP ${status}`;
    return new SafetensorsFetchError(message, status, {
      errorCode,
      sources,
      detail,
    });
  }
}

export class SafetensorsFormatError extends Error {
  constructor(message) {
    super(message);
    this.name = "SafetensorsFormatError";
  }
}
