import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { server } from "../setup/msw-server";

// Fixture: real parquet file produced via pyarrow
// (scripts/dev/generate_preview_test_fixtures.py). Byte-identical to
// anything the HuggingFace datasets-server would serve for a comparable
// upload.
const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(__dirname, "../fixtures/previews/tiny.parquet");
const FIXTURE_BYTES = readFileSync(FIXTURE_PATH);
const FIXTURE_URL = "https://s3.test.local/bucket/tiny.parquet";

function respondRange(buffer, { status = 206 } = {}) {
  return async ({ request }) => {
    const method = request.method.toUpperCase();
    if (method === "HEAD") {
      return new HttpResponse(null, {
        status: 200,
        headers: {
          "Content-Length": String(buffer.length),
          "Accept-Ranges": "bytes",
        },
      });
    }
    const rangeHeader = request.headers.get("range");
    if (!rangeHeader) {
      return new HttpResponse(buffer, {
        status: 200,
        headers: { "Content-Length": String(buffer.length) },
      });
    }
    const match = /^bytes=(\d+)-(\d+)?$/.exec(rangeHeader);
    if (!match) return new HttpResponse("Bad Range", { status: 400 });
    const start = Number(match[1]);
    const end =
      match[2] == null
        ? buffer.length - 1
        : Math.min(Number(match[2]), buffer.length - 1);
    const slice = buffer.subarray(start, end + 1);
    return new HttpResponse(slice, {
      status,
      headers: {
        "Content-Range": `bytes ${start}-${end}/${buffer.length}`,
        "Content-Length": String(slice.length),
        "Accept-Ranges": "bytes",
      },
    });
  };
}

describe("parquet utilities", () => {
  async function loadModule() {
    vi.resetModules();
    return import("@/utils/parquet");
  }

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // Regression for the in-archive preview path. hyparquet's
  // asyncBufferFromUrl issues a HEAD against the source for the
  // tail size and Range requests for the footer; both are
  // unreliable on `blob:` URLs and the user saw "Browser blocked
  // the request" when previewing a parquet member from inside an
  // indexed tar. The from-buffer entry point lets the modal hand
  // hyparquet the already-extracted bytes directly.
  describe("parseParquetMetadataFromBuffer", () => {
    it("parses metadata from in-memory bytes without issuing fetches", async () => {
      const { parseParquetMetadataFromBuffer, summarizeParquetSchema } =
        await loadModule();
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      const metadata = await parseParquetMetadataFromBuffer(FIXTURE_BYTES);
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(metadata.byteLength).toBe(FIXTURE_BYTES.length);
      expect(metadata.numRows).toBe(100);
      expect(metadata.rowGroups.length).toBeGreaterThanOrEqual(1);
      const summary = summarizeParquetSchema(metadata);
      expect(summary.columnCount).toBe(4);
    });

    it("rejects bytes that do not end with the PAR1 footer magic", async () => {
      const { parseParquetMetadataFromBuffer } = await loadModule();
      // Random non-parquet bytes — hyparquet checks the trailing 4
      // bytes for "PAR1" and throws on mismatch. Pin the failure
      // mode so a future hyparquet upgrade doesn't silently swallow
      // garbage and surface synthetic-looking metadata.
      const buf = new Uint8Array(64).map((_, i) => i & 0xff);
      await expect(parseParquetMetadataFromBuffer(buf)).rejects.toThrow(
        /PAR1/,
      );
    });

    it("accepts an ArrayBuffer input (not just Uint8Array)", async () => {
      const { parseParquetMetadataFromBuffer } = await loadModule();
      const ab = FIXTURE_BYTES.buffer.slice(
        FIXTURE_BYTES.byteOffset,
        FIXTURE_BYTES.byteOffset + FIXTURE_BYTES.byteLength,
      );
      const metadata = await parseParquetMetadataFromBuffer(ab);
      expect(metadata.numRows).toBe(100);
    });

    it("normalizes input from a different realm (jsdom Buffer / cross-realm ArrayBuffer)", async () => {
      const { parseParquetMetadataFromBuffer } = await loadModule();
      // Node fs.readFileSync returns a Buffer whose underlying
      // ArrayBuffer fails the jsdom realm's `instanceof
      // ArrayBuffer` check. The from-buffer path explicitly copies
      // into a current-realm allocation; this test pins that
      // contract by passing the raw cross-realm ArrayBuffer.
      const realm = FIXTURE_BYTES.buffer;
      const metadata = await parseParquetMetadataFromBuffer(realm.slice(
        FIXTURE_BYTES.byteOffset,
        FIXTURE_BYTES.byteOffset + FIXTURE_BYTES.byteLength,
      ));
      expect(metadata.numRows).toBe(100);
    });
  });

  it("parses footer metadata, row counts, and top-level columns", async () => {
    const { parseParquetMetadata, summarizeParquetSchema } = await loadModule();
    server.use(http.get(FIXTURE_URL, respondRange(FIXTURE_BYTES)));
    server.use(http.head(FIXTURE_URL, respondRange(FIXTURE_BYTES)));

    const metadata = await parseParquetMetadata(FIXTURE_URL);

    expect(metadata.byteLength).toBe(FIXTURE_BYTES.length);
    expect(metadata.numRows).toBe(100);
    expect(metadata.rowGroups.length).toBeGreaterThanOrEqual(1);
    expect(metadata.rowGroups[0].numRows).toBe(100);

    const summary = summarizeParquetSchema(metadata);
    expect(summary.columnCount).toBe(4);
    expect(summary.columns.map((col) => col.name)).toEqual([
      "id",
      "score",
      "ratio",
      "flag",
    ]);
    // Physical types come straight from the parquet footer.
    const physical = Object.fromEntries(
      summary.columns.map((col) => [col.name, col.physicalType]),
    );
    expect(physical.id).toBe("BYTE_ARRAY");
    expect(physical.score).toBe("INT64");
    expect(physical.ratio).toBe("FLOAT");
    expect(physical.flag).toBe("BOOLEAN");
  });

  it("normalizeCount: null/undefined → 0", async () => {
    const { normalizeCount } = await loadModule();
    expect(normalizeCount(null)).toBe(0);
    expect(normalizeCount(undefined)).toBe(0);
  });

  it("normalizeCount: bigint within MAX_SAFE_INTEGER → number", async () => {
    const { normalizeCount } = await loadModule();
    expect(normalizeCount(0n)).toBe(0);
    expect(normalizeCount(500n)).toBe(500);
    expect(normalizeCount(BigInt(Number.MAX_SAFE_INTEGER))).toBe(
      Number.MAX_SAFE_INTEGER,
    );
  });

  it("normalizeCount: bigint beyond MAX_SAFE_INTEGER → string (preserves precision)", async () => {
    const { normalizeCount } = await loadModule();
    const huge = BigInt(Number.MAX_SAFE_INTEGER) + 17n;
    expect(normalizeCount(huge)).toBe(huge.toString());
  });

  it("normalizeCount: plain number passes through unchanged", async () => {
    const { normalizeCount } = await loadModule();
    expect(normalizeCount(42)).toBe(42);
    expect(normalizeCount(0)).toBe(0);
  });

  it("summarizeParquetSchema copes with an empty / shapeless schema tree", async () => {
    const { summarizeParquetSchema } = await loadModule();
    expect(summarizeParquetSchema({ schemaTree: null })).toEqual({
      columnCount: 0,
      columns: [],
    });
    expect(summarizeParquetSchema({ schemaTree: {} })).toEqual({
      columnCount: 0,
      columns: [],
    });
    expect(
      summarizeParquetSchema({ schemaTree: { children: [] } }),
    ).toEqual({ columnCount: 0, columns: [] });
  });

  it("summarizeParquetSchema tolerates children without element metadata", async () => {
    const { summarizeParquetSchema } = await loadModule();
    const summary = summarizeParquetSchema({
      schemaTree: {
        children: [{}, { element: { name: "foo", type: "INT32" } }],
      },
    });
    expect(summary.columnCount).toBe(2);
    expect(summary.columns).toEqual([
      {
        name: "",
        logicalType: null,
        physicalType: null,
        repetitionType: null,
      },
      {
        name: "foo",
        logicalType: null,
        physicalType: "INT32",
        repetitionType: null,
      },
    ]);
  });

  it("fires the progress callback phases in order", async () => {
    const { parseParquetMetadata } = await loadModule();
    server.use(http.get(FIXTURE_URL, respondRange(FIXTURE_BYTES)));
    server.use(http.head(FIXTURE_URL, respondRange(FIXTURE_BYTES)));

    const phases = [];
    await parseParquetMetadata(FIXTURE_URL, {
      onProgress: (phase) => phases.push(phase),
    });

    expect(phases[0]).toBe("head");
    expect(phases).toContain("footer");
    expect(phases).toContain("parsing");
    expect(phases[phases.length - 1]).toBe("done");
  });
});
