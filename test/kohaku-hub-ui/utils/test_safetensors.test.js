import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "@/testing/msw";

import { server } from "../setup/msw-server";

// Fixture: real safetensors bytes produced via the Python `safetensors`
// library (scripts/dev/generate_preview_test_fixtures.py). Byte-identical
// to what HuggingFace emits for an equivalent upload.
const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(
  __dirname,
  "../fixtures/previews/tiny.safetensors",
);
const FIXTURE_BYTES = readFileSync(FIXTURE_PATH);
const FIXTURE_URL = "https://s3.test.local/bucket/tiny.safetensors";

function rangeResponder(buffer) {
  return async ({ request }) => {
    const rangeHeader = request.headers.get("range");
    if (!rangeHeader) {
      return new HttpResponse(buffer, { status: 200 });
    }
    const match = /^bytes=(\d+)-(\d+)$/.exec(rangeHeader);
    if (!match) return new HttpResponse("Bad Range", { status: 400 });
    const start = Number(match[1]);
    const end = Math.min(Number(match[2]), buffer.length - 1);
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

describe("safetensors utilities", () => {
  async function loadModule() {
    vi.resetModules();
    return import("@/utils/safetensors");
  }

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("parses a real safetensors header via a single Range read", async () => {
    const { parseSafetensorsMetadata } = await loadModule();
    server.use(http.get(FIXTURE_URL, rangeResponder(FIXTURE_BYTES)));

    const header = await parseSafetensorsMetadata(FIXTURE_URL);

    expect(header.metadata).toEqual({
      format: "pt",
      framework: "kohakuhub-fixture",
      seed: "0",
    });

    // Exact tensor catalog the Python fixture wrote. The Python side is
    // deterministic (numpy seeded), so the shapes and dtypes here are the
    // ground truth.
    expect(Object.keys(header.tensors).sort()).toEqual([
      "encoder.embed.weight",
      "encoder.layer0.attn.q_proj.weight",
      "encoder.layer0.ln.bias",
    ]);
    // safetensors orders tensors alphabetically in the JSON, but the
    // underlying data-layout order (driving data_offsets) is an
    // implementation detail of safetensors itself. Assert on stable
    // derived fields only (dtype, shape, parameter count, offset width).
    const embedWeight = header.tensors["encoder.embed.weight"];
    expect(embedWeight.dtype).toBe("F32");
    expect(embedWeight.shape).toEqual([32, 8]);
    expect(embedWeight.parameters).toBe(256);
    expect(embedWeight.data_offsets[1] - embedWeight.data_offsets[0]).toBe(
      256 * 4, // 256 F32 elements * 4 bytes each
    );
    expect(header.tensors["encoder.layer0.attn.q_proj.weight"].dtype).toBe(
      "F16",
    );
    expect(header.tensors["encoder.layer0.ln.bias"].dtype).toBe("I64");
  });

  it("summarizes dtype buckets, total params, and byte size", async () => {
    const { parseSafetensorsMetadata, summarizeSafetensors } =
      await loadModule();
    server.use(http.get(FIXTURE_URL, rangeResponder(FIXTURE_BYTES)));

    const header = await parseSafetensorsMetadata(FIXTURE_URL);
    const summary = summarizeSafetensors(header);

    // 3 tensors: F32 (32x8=256), F16 (16x16=256), I64 (16)
    expect(summary.parameters).toEqual({ F32: 256, F16: 256, I64: 16 });
    expect(summary.total).toBe(528);
    // F32*4 + F16*2 + I64*8 = 1024 + 512 + 128 = 1664
    expect(summary.byte_size).toBe(1664);
  });

  it("fires the progress callback in order for a short-header read", async () => {
    const { parseSafetensorsMetadata } = await loadModule();
    server.use(http.get(FIXTURE_URL, rangeResponder(FIXTURE_BYTES)));
    const phases = [];

    await parseSafetensorsMetadata(FIXTURE_URL, {
      onProgress: (phase) => phases.push(phase),
    });

    expect(phases).toEqual(["range-head", "parsing", "done"]);
  });

  it("falls back to a second Range read when the header is larger than the speculative read", async () => {
    const { parseSafetensorsMetadata } = await loadModule();

    // Synthesize a safetensors file whose header is > 100000 bytes so the
    // speculative first Range does not capture it — this pins the
    // two-read fallback path that mirrors huggingface_hub's behavior.
    const names = [];
    const payload = {};
    for (let i = 0; i < 6000; i += 1) {
      const name = `tensor.very.long.name.${"x".repeat(12)}.${i}`;
      names.push(name);
      payload[name] = { dtype: "F32", shape: [2], data_offsets: [0, 8] };
    }
    const headerJson = JSON.stringify(payload);
    const headerBytes = new TextEncoder().encode(headerJson);
    expect(headerBytes.length).toBeGreaterThan(100000);

    const fileBytes = new Uint8Array(8 + headerBytes.length);
    new DataView(fileBytes.buffer).setBigUint64(
      0,
      BigInt(headerBytes.length),
      true,
    );
    fileBytes.set(headerBytes, 8);

    const phases = [];
    server.use(http.get(FIXTURE_URL, rangeResponder(fileBytes)));

    const header = await parseSafetensorsMetadata(FIXTURE_URL, {
      onProgress: (phase) => phases.push(phase),
    });

    expect(Object.keys(header.tensors)).toHaveLength(6000);
    expect(phases).toContain("range-full");
    expect(phases[phases.length - 1]).toBe("done");
  });

  it("rejects absurd header lengths instead of issuing a huge Range", async () => {
    const { parseSafetensorsMetadata, SafetensorsFormatError } =
      await loadModule();

    const bogus = new Uint8Array(16);
    // 200 MB header length — larger than SAFETENSORS_MAX_HEADER_LENGTH
    new DataView(bogus.buffer).setBigUint64(0, BigInt(200 * 1024 * 1024), true);
    server.use(http.get(FIXTURE_URL, rangeResponder(bogus)));

    await expect(parseSafetensorsMetadata(FIXTURE_URL)).rejects.toBeInstanceOf(
      SafetensorsFormatError,
    );
  });

  it("raises SafetensorsFetchError on non-206/200 responses", async () => {
    const { parseSafetensorsMetadata, SafetensorsFetchError } =
      await loadModule();
    server.use(
      http.get(FIXTURE_URL, () =>
        HttpResponse.text("forbidden", { status: 403 }),
      ),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFetchError);
    expect(err.status).toBe(403);
  });

  it("captures X-Error-Code + structured sources body from an aggregated fallback error", async () => {
    const { parseSafetensorsMetadata, SafetensorsFetchError } =
      await loadModule();
    // Shape matches src/kohakuhub/api/fallback/utils.py
    // build_aggregate_failure_response — a gated 401 with
    // HF-compatible X-Error-Code=GatedRepo and a sources[] listing.
    const aggregated = {
      error: "GatedRepo",
      detail:
        "Upstream source requires authentication - likely a gated repository.",
      sources: [
        {
          name: "HuggingFace",
          url: "https://huggingface.co",
          status: 401,
          category: "auth",
          message: "Access to model owner/demo is restricted. Please log in.",
        },
      ],
    };
    server.use(
      http.get(FIXTURE_URL, () =>
        HttpResponse.json(aggregated, {
          status: 401,
          headers: {
            "X-Error-Code": "GatedRepo",
            "X-Error-Message":
              "Upstream source requires authentication - likely a gated repository.",
          },
        }),
      ),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFetchError);
    expect(err.status).toBe(401);
    expect(err.errorCode).toBe("GatedRepo");
    expect(err.detail).toContain("authentication");
    expect(err.sources).toHaveLength(1);
    expect(err.sources[0]).toMatchObject({
      name: "HuggingFace",
      status: 401,
      category: "auth",
    });
    expect(err.message).toContain("authentication");
  });

  it("defaults tensor data_offsets to [0, 0] when the header omits them", async () => {
    const { parseSafetensorsMetadata } = await loadModule();

    // A malformed-but-salvageable header: the tensor entry has dtype +
    // shape but no `data_offsets` field. The parser should still produce
    // a row (with an inert [0, 0] range) rather than throwing.
    const headerJson = JSON.stringify({
      "weird.tensor": { dtype: "F32", shape: [4] },
    });
    const headerBytes = new TextEncoder().encode(headerJson);
    const file = new Uint8Array(8 + headerBytes.length);
    new DataView(file.buffer).setBigUint64(0, BigInt(headerBytes.length), true);
    file.set(headerBytes, 8);
    server.use(http.get(FIXTURE_URL, () => new HttpResponse(file, { status: 206 })));

    const header = await parseSafetensorsMetadata(FIXTURE_URL);
    expect(header.tensors["weird.tensor"].data_offsets).toEqual([0, 0]);
  });

  it("fromResponse derives errorCode / sources / detail from body when headers are absent", async () => {
    const { parseSafetensorsMetadata, SafetensorsFetchError } =
      await loadModule();
    // Deliberately omit X-Error-Code + X-Error-Message headers so the
    // parser must read them from the JSON body alone.
    server.use(
      http.get(FIXTURE_URL, () =>
        HttpResponse.json(
          {
            error: "UpstreamFailure",
            detail: "All sources failed",
            sources: [
              { name: "A", status: 500, category: "server", message: "x" },
            ],
          },
          { status: 502 },
        ),
      ),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFetchError);
    expect(err.status).toBe(502);
    expect(err.errorCode).toBe("UpstreamFailure");
    expect(err.detail).toBe("All sources failed");
    expect(err.message).toBe("All sources failed");
    expect(err.sources).toHaveLength(1);
    expect(err.sources[0].name).toBe("A");
  });

  it("fromResponse ignores body.sources when it is not an array", async () => {
    const { parseSafetensorsMetadata, SafetensorsFetchError } =
      await loadModule();
    server.use(
      http.get(FIXTURE_URL, () =>
        HttpResponse.json(
          { error: "BadShape", sources: "not-an-array" },
          { status: 401 },
        ),
      ),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFetchError);
    expect(err.errorCode).toBe("BadShape");
    expect(err.sources).toBeNull();
  });

  it("falls back to a header-derived message when the error body is not JSON", async () => {
    const { parseSafetensorsMetadata, SafetensorsFetchError } =
      await loadModule();
    server.use(
      http.get(FIXTURE_URL, () =>
        HttpResponse.text("plain text body", {
          status: 500,
          headers: {
            "X-Error-Message": "Upstream exploded",
          },
        }),
      ),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFetchError);
    expect(err.status).toBe(500);
    expect(err.errorCode).toBeNull();
    expect(err.sources).toBeNull();
    expect(err.detail).toBe("Upstream exploded");
    expect(err.message).toBe("Upstream exploded");
  });

  it("raises SafetensorsFormatError when the response is shorter than the 8-byte length prefix", async () => {
    const { parseSafetensorsMetadata, SafetensorsFormatError } =
      await loadModule();
    // 4-byte body: no way to read the u64 length prefix.
    server.use(
      http.get(FIXTURE_URL, () => new HttpResponse(new Uint8Array([1, 2, 3, 4]), { status: 206 })),
    );
    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFormatError);
    expect(err.message).toMatch(/Truncated response/i);
  });

  it("raises SafetensorsFormatError when the header JSON is not valid UTF-8", async () => {
    const { parseSafetensorsMetadata, SafetensorsFormatError } =
      await loadModule();
    // Header length = 3, followed by invalid UTF-8 continuation bytes.
    const invalid = new Uint8Array(8 + 3);
    new DataView(invalid.buffer).setBigUint64(0, 3n, true);
    invalid[8] = 0xff;
    invalid[9] = 0xff;
    invalid[10] = 0xff;
    server.use(http.get(FIXTURE_URL, () => new HttpResponse(invalid, { status: 206 })));

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFormatError);
    expect(err.message).toMatch(/not valid UTF-8 JSON/i);
  });

  it("raises SafetensorsFormatError when the header JSON does not parse to an object", async () => {
    const { parseSafetensorsMetadata, SafetensorsFormatError } =
      await loadModule();
    // Valid JSON that parses to `null` — still not an object-shaped header.
    const nullJson = new TextEncoder().encode("null");
    const bytes = new Uint8Array(8 + nullJson.length);
    new DataView(bytes.buffer).setBigUint64(0, BigInt(nullJson.length), true);
    bytes.set(nullJson, 8);
    server.use(http.get(FIXTURE_URL, () => new HttpResponse(bytes, { status: 206 })));

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFormatError);
    expect(err.message).toMatch(/not an object/i);
  });

  it("raises SafetensorsFormatError when the fat-header second read is truncated", async () => {
    const { parseSafetensorsMetadata, SafetensorsFormatError } =
      await loadModule();

    // Build a file whose header length exceeds 100,000 so the parser
    // will issue the second Range — but the mock only returns fewer
    // bytes than requested, exercising the truncation guard.
    const headerJson = JSON.stringify({
      // Pad to >100,000 bytes with a huge unused key.
      padding: "x".repeat(100_050),
    });
    const headerBytes = new TextEncoder().encode(headerJson);
    const fullFile = new Uint8Array(8 + headerBytes.length);
    new DataView(fullFile.buffer).setBigUint64(0, BigInt(headerBytes.length), true);
    fullFile.set(headerBytes, 8);

    let callCount = 0;
    server.use(
      http.get(FIXTURE_URL, ({ request }) => {
        callCount += 1;
        const range = request.headers.get("range");
        if (callCount === 1) {
          // First speculative read — hand back the first 100,001 bytes
          // (the parser will then issue a second read for the full header).
          const slice = fullFile.subarray(0, 100_001);
          return new HttpResponse(slice, {
            status: 206,
            headers: {
              "Content-Range": `bytes 0-100000/${fullFile.length}`,
              "Content-Length": String(slice.length),
            },
          });
        }
        // Second call (fat-header fallback): intentionally short-change
        // the response so we hit the truncation guard.
        expect(range).toMatch(/^bytes=8-/);
        return new HttpResponse(new Uint8Array([1, 2, 3]), { status: 206 });
      }),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFormatError);
    expect(err.message).toMatch(/Truncated header response/i);
    expect(callCount).toBe(2);
  });

  it("raises SafetensorsFetchError when the second Range fetch itself fails", async () => {
    const { parseSafetensorsMetadata, SafetensorsFetchError } =
      await loadModule();
    const headerJson = JSON.stringify({ padding: "x".repeat(100_050) });
    const headerBytes = new TextEncoder().encode(headerJson);
    const fullFile = new Uint8Array(8 + headerBytes.length);
    new DataView(fullFile.buffer).setBigUint64(0, BigInt(headerBytes.length), true);
    fullFile.set(headerBytes, 8);

    let callCount = 0;
    server.use(
      http.get(FIXTURE_URL, () => {
        callCount += 1;
        if (callCount === 1) {
          return new HttpResponse(fullFile.subarray(0, 100_001), {
            status: 206,
            headers: {
              "Content-Range": `bytes 0-100000/${fullFile.length}`,
              "Content-Length": "100001",
            },
          });
        }
        return HttpResponse.text("gone", { status: 410 });
      }),
    );

    const err = await parseSafetensorsMetadata(FIXTURE_URL).catch((e) => e);
    expect(err).toBeInstanceOf(SafetensorsFetchError);
    expect(err.status).toBe(410);
  });

  it("SafetensorsFetchError carries its HTTP status, SafetensorsFormatError has the expected name", async () => {
    const { SafetensorsFetchError, SafetensorsFormatError } =
      await loadModule();
    const fetchErr = new SafetensorsFetchError("boom", 502);
    expect(fetchErr).toBeInstanceOf(Error);
    expect(fetchErr.name).toBe("SafetensorsFetchError");
    expect(fetchErr.status).toBe(502);

    const formatErr = new SafetensorsFormatError("bad bytes");
    expect(formatErr).toBeInstanceOf(Error);
    expect(formatErr.name).toBe("SafetensorsFormatError");
  });

  it("skips __metadata__-less headers and non-object tensor entries without crashing", async () => {
    const { parseSafetensorsMetadata } = await loadModule();

    // Minimal header with one valid tensor entry and one malformed entry
    // that should be silently ignored (the parser is defensive so a
    // weird upload cannot NPE the whole modal).
    const headerJson = JSON.stringify({
      "real.tensor": { dtype: "F32", shape: [2, 2], data_offsets: [0, 16] },
      "junk.tensor": "not-an-object",
    });
    const headerBytes = new TextEncoder().encode(headerJson);
    const file = new Uint8Array(8 + headerBytes.length);
    new DataView(file.buffer).setBigUint64(0, BigInt(headerBytes.length), true);
    file.set(headerBytes, 8);
    server.use(http.get(FIXTURE_URL, () => new HttpResponse(file, { status: 206 })));

    const header = await parseSafetensorsMetadata(FIXTURE_URL);
    expect(header.metadata).toBeNull();
    expect(Object.keys(header.tensors)).toEqual(["real.tensor"]);
    expect(header.tensors["real.tensor"].parameters).toBe(4);
  });

  it("treats missing shape as [] (0 parameters)", async () => {
    const { parseSafetensorsMetadata } = await loadModule();
    const headerJson = JSON.stringify({
      scalar: { dtype: "F32", data_offsets: [0, 4] },
    });
    const headerBytes = new TextEncoder().encode(headerJson);
    const file = new Uint8Array(8 + headerBytes.length);
    new DataView(file.buffer).setBigUint64(0, BigInt(headerBytes.length), true);
    file.set(headerBytes, 8);
    server.use(http.get(FIXTURE_URL, () => new HttpResponse(file, { status: 206 })));

    const header = await parseSafetensorsMetadata(FIXTURE_URL);
    expect(header.tensors.scalar.shape).toEqual([]);
    // reduce over [] with an initial value of 1 gives 1 — that is the
    // documented behaviour: a rank-0 scalar counts as 1 element.
    expect(header.tensors.scalar.parameters).toBe(1);
  });

  it("summarizeSafetensors omits unknown dtype sizes from byte_size total", async () => {
    const { summarizeSafetensors } = await loadModule();
    const summary = summarizeSafetensors({
      tensors: {
        a: { dtype: "F32", shape: [4], parameters: 4, data_offsets: [0, 16] },
        b: {
          dtype: "WEIRD_DTYPE",
          shape: [10],
          parameters: 10,
          data_offsets: [16, 26],
        },
      },
    });
    expect(summary.total).toBe(14);
    // F32 is 4 bytes/elem, the WEIRD_DTYPE size is unknown so it
    // contributes 0 to byte_size. Not a great UX for novel dtypes,
    // but it is better than lying with a made-up size.
    expect(summary.byte_size).toBe(16);
    expect(summary.parameters).toEqual({ F32: 4, WEIRD_DTYPE: 10 });
  });
});
