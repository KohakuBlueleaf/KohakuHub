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
});
