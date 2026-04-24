// src/kohaku-hub-ui/src/utils/parquet.js
//
// Pure-client parquet footer reader. Wraps hyparquet so the preview modal
// gets a stable interface shape (same as safetensors.js) and so the
// progress callback fires at the right granularity for the spinner text.
//
// Design notes:
//   - hyparquet's asyncBufferFromUrl does HEAD + tail Range reads on its
//     own. It accepts `requestInit` so we can pin the cross-origin CORS
//     contract (mode "cors", credentials "omit") for presigned S3/MinIO
//     URLs that only allow anonymous access.
//   - The default initial tail fetch is 512 KB, which easily covers the
//     footer-only cases measured in issue #27 (≤ 264 KB). We leave the
//     default alone; hyparquet will issue a second Range if the footer
//     turns out to be fatter.
//   - BigInts are passed through `toJson` so Vue templates do not need
//     to know about BigInt rendering. Row counts are preserved as
//     strings when they exceed Number.MAX_SAFE_INTEGER (extremely rare
//     for HF datasets but we do not want to silently lose precision).

import {
  asyncBufferFromUrl,
  parquetMetadataAsync,
  parquetSchema,
  toJson,
} from "hyparquet";

/**
 * Parse a parquet file's footer via hyparquet.
 *
 * @param {string} url - Absolute or same-origin `/resolve/...` URL.
 * @param {object} [options]
 * @param {(phase: string, detail?: object) => void} [options.onProgress]
 *     Phases:
 *       "head"      HEAD request for Content-Length
 *       "footer"    tail Range read (~512 KB)
 *       "parsing"   thrift decoding
 *       "done"      metadata ready
 * @param {AbortSignal} [options.signal]
 * @returns {Promise<{
 *   byteLength: number,
 *   numRows: (number|string),
 *   createdBy: (string|null),
 *   keyValueMetadata: {key: string, value: string}[],
 *   schema: object[],
 *   schemaTree: object,
 *   rowGroups: {numRows: (number|string), totalByteSize: (number|string)}[],
 * }>}
 */
export async function parseParquetMetadata(url, options = {}) {
  const { onProgress = () => {}, signal } = options;

  const requestInit = {
    mode: "cors",
    credentials: "omit",
    ...(signal ? { signal } : {}),
  };

  onProgress("head");
  const buffer = await asyncBufferFromUrl({ url, requestInit });

  onProgress("footer", { byteLength: buffer.byteLength });
  const raw = await parquetMetadataAsync(buffer);

  onProgress("parsing");
  const schemaTree = parquetSchema(raw);
  const keyValueMetadata = (raw.key_value_metadata ?? []).map((kv) => ({
    key: String(kv.key ?? ""),
    value: kv.value == null ? null : String(kv.value),
  }));
  const rowGroups = (raw.row_groups ?? []).map((rg) => ({
    numRows: normalizeCount(rg.num_rows),
    totalByteSize: normalizeCount(rg.total_byte_size),
  }));

  onProgress("done");
  return {
    byteLength: buffer.byteLength,
    numRows: normalizeCount(raw.num_rows),
    createdBy: raw.created_by ?? null,
    keyValueMetadata,
    schema: toJson(raw.schema ?? []),
    schemaTree: toJson(schemaTree),
    rowGroups,
  };
}

/**
 * Summarize schema for the preview header pill: column count and the
 * top-level physical column names in stable order. Nested fields under a
 * struct are not unrolled — only the direct children of the root record
 * are listed, matching what HF's dataset-viewer /info shows as
 * "features".
 */
export function summarizeParquetSchema(metadata) {
  const root = metadata.schemaTree;
  const columns = Array.isArray(root?.children)
    ? root.children.map((child) => ({
        name: child.element?.name ?? "",
        logicalType: child.element?.logical_type?.type ?? null,
        physicalType: child.element?.type ?? null,
        repetitionType: child.element?.repetition_type ?? null,
      }))
    : [];
  return { columnCount: columns.length, columns };
}

// Exported for direct unit-testing — the hyparquet API never hands us a
// plain number path in practice (thrift decoding always yields BigInt),
// so a direct test is cleaner than trying to synthesize a fake buffer
// that happens to encode a small-enough int as Number.
export function normalizeCount(value) {
  if (value == null) return 0;
  if (typeof value === "bigint") {
    return value <= BigInt(Number.MAX_SAFE_INTEGER)
      ? Number(value)
      : value.toString();
  }
  return value;
}
