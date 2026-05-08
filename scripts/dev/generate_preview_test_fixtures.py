#!/usr/bin/env python3
"""Regenerate tiny real-format fixtures for frontend preview unit tests.

The fixtures are checked into the repo so tests do not require network or
live infrastructure (per AGENTS.md §5.2). This script is the source of
truth: anyone needing to refresh or re-verify the fixtures can run it and
diff the output.

Output:
- ``test/kohaku-hub-ui/fixtures/previews/tiny.safetensors`` — valid
  safetensors file with three small tensors in three dtypes and a
  non-empty ``__metadata__`` block. Produced via ``safetensors.numpy``
  so the wire format is byte-identical to what HuggingFace emits.
- ``test/kohaku-hub-ui/fixtures/previews/tiny.parquet`` — valid parquet
  file with ~100 rows and four columns (string, int64, float32, bool).
  Produced via ``pyarrow.parquet`` so the footer/schema shape matches
  anything the HuggingFace datasets-server would serve for a comparable
  upload.
- ``test/kohaku-hub-ui/fixtures/previews/with_exif_thumb.jpg`` — JPEG
  whose APP1 EXIF segment carries an embedded thumbnail JPEG. Used to
  exercise the front-end's "Range-read 64 KB → extract EXIF thumbnail
  → bypass the full image" path inside the indexed-tar listing.
- ``test/kohaku-hub-ui/fixtures/previews/no_exif_thumb.jpg`` — same
  visual content, EXIF stripped. Pins the negative branch (parser
  returns null → caller falls through to full-decode strategy).
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image
from safetensors.numpy import save as save_safetensors

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "test" / "kohaku-hub-ui" / "fixtures" / "previews"


def build_safetensors() -> bytes:
    rng = np.random.default_rng(seed=0)
    tensors = {
        "encoder.embed.weight": rng.standard_normal((32, 8)).astype(np.float32),
        "encoder.layer0.attn.q_proj.weight": rng.standard_normal((16, 16)).astype(np.float16),
        "encoder.layer0.ln.bias": np.arange(16, dtype=np.int64),
    }
    metadata = {
        "format": "pt",
        "framework": "kohakuhub-fixture",
        "seed": "0",
    }
    return save_safetensors(tensors, metadata=metadata)


def build_parquet() -> bytes:
    row_count = 100
    table = pa.table(
        {
            "id": pa.array([f"row-{i:03d}" for i in range(row_count)], type=pa.string()),
            "score": pa.array(np.arange(row_count, dtype=np.int64)),
            "ratio": pa.array(np.linspace(0.0, 1.0, row_count, dtype=np.float32)),
            "flag": pa.array([i % 2 == 0 for i in range(row_count)], type=pa.bool_()),
        }
    )
    import io

    sink = io.BytesIO()
    pq.write_table(table, sink, compression="snappy")
    return sink.getvalue()


def _solid_image(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", size, color)
    # A diagonal gradient so the thumbnail extracted by the parser is
    # visually distinguishable from the full-resolution image — handy
    # for human inspection of the fixture.
    pixels = img.load()
    w, h = size
    for y in range(h):
        for x in range(w):
            r = (color[0] + x // 4) % 256
            g = (color[1] + y // 4) % 256
            b = (color[2] + (x + y) // 8) % 256
            pixels[x, y] = (r, g, b)
    return img


def _build_exif_with_thumbnail(thumbnail_bytes: bytes) -> bytes:
    """Build a minimal EXIF segment (APP1 payload) carrying a thumbnail
    in IFD1. The parser the front-end uses walks IFD0 → next-IFD pointer
    → IFD1 → tags 0x0201/0x0202; this helper emits exactly that shape."""
    # TIFF header (little-endian)
    tiff = bytearray(b"II*\x00")           # byte order + magic
    tiff += struct.pack("<I", 8)           # IFD0 offset (right after TIFF header)

    # IFD0: one entry (ImageDescription) so the structure is non-empty.
    # Keep IFD0 minimal — what matters is the next-IFD pointer.
    desc = b"kohaku\x00"
    tiff += struct.pack("<H", 1)           # entry count
    # tag 0x010E (ImageDescription), type 2 (ASCII), count, value/offset
    tiff += struct.pack("<HHI", 0x010E, 2, len(desc))
    desc_offset = 8 + 2 + 12 + 4 + 2 + 12 * 4 + 4  # appended after both IFDs + thumb
    tiff += struct.pack("<I", desc_offset)

    # next-IFD pointer = position of IFD1 (right after IFD0 and IFD0's
    # next pointer field — currently at offset 8 + 2 + 12 + 4 = 26)
    ifd1_offset = 8 + 2 + 12 + 4
    tiff += struct.pack("<I", ifd1_offset)

    # IFD1: 4 entries (compression=6 JPEG, JPEGInterchangeFormat,
    # JPEGInterchangeFormatLength, plus a benign one).
    thumb_offset_placeholder_pos = None
    thumb_length_placeholder_pos = None

    ifd1 = bytearray()
    ifd1 += struct.pack("<H", 4)                                       # entry count
    ifd1 += struct.pack("<HHII", 0x0103, 3, 1, 6)                      # Compression = JPEG
    ifd1 += struct.pack("<HHII", 0x0100, 3, 1, 160)                    # ImageWidth (informational)
    # JPEGInterchangeFormat (0x0201) — offset of thumbnail relative to
    # TIFF base. Patched after we know the absolute layout.
    thumb_offset_placeholder_pos = len(tiff) + len(ifd1) + 8
    ifd1 += struct.pack("<HHII", 0x0201, 4, 1, 0)                      # placeholder offset
    # JPEGInterchangeFormatLength (0x0202)
    thumb_length_placeholder_pos = len(tiff) + len(ifd1) + 8
    ifd1 += struct.pack("<HHII", 0x0202, 4, 1, len(thumbnail_bytes))   # thumbnail length
    ifd1 += struct.pack("<I", 0)                                       # next-IFD = none

    tiff += ifd1
    # ASCII description tag value lives here.
    tiff += desc
    # Pad to even alignment before the thumbnail JPEG bytes.
    if len(tiff) % 2:
        tiff += b"\x00"
    thumb_abs = len(tiff)
    struct.pack_into("<I", tiff, thumb_offset_placeholder_pos, thumb_abs)
    tiff += thumbnail_bytes

    # APP1 payload: "Exif\0\0" + TIFF block.
    return b"Exif\x00\x00" + bytes(tiff)


def _wrap_jpeg_with_app1(jpeg_bytes: bytes, app1_payload: bytes) -> bytes:
    """Insert an APP1 (0xFFE1) segment with the supplied EXIF payload
    immediately after the JPEG SOI marker. Strips any pre-existing APP
    segments to keep the layout deterministic."""
    if not jpeg_bytes.startswith(b"\xFF\xD8"):
        raise ValueError("not a JPEG")
    # Drop existing APP segments so our injected one sits right after SOI
    # — keeps the byte layout stable for the parser test.
    pos = 2
    while pos + 4 <= len(jpeg_bytes):
        if jpeg_bytes[pos] != 0xFF:
            break
        marker = jpeg_bytes[pos + 1]
        if 0xE0 <= marker <= 0xEF:
            seg_len = struct.unpack(">H", jpeg_bytes[pos + 2 : pos + 4])[0]
            pos += 2 + seg_len
            continue
        break
    body = jpeg_bytes[pos:]
    seg_len = 2 + len(app1_payload)
    if seg_len > 0xFFFF:
        raise ValueError("APP1 payload too large")
    app1 = b"\xFF\xE1" + struct.pack(">H", seg_len) + app1_payload
    return b"\xFF\xD8" + app1 + body


def build_jpeg_with_exif_thumbnail() -> bytes:
    full = _solid_image((512, 512), (90, 130, 200))
    full_bytes = io.BytesIO()
    full.save(full_bytes, format="JPEG", quality=80)

    thumb = _solid_image((96, 96), (200, 90, 90))
    thumb_bytes = io.BytesIO()
    thumb.save(thumb_bytes, format="JPEG", quality=70)

    app1 = _build_exif_with_thumbnail(thumb_bytes.getvalue())
    return _wrap_jpeg_with_app1(full_bytes.getvalue(), app1)


def build_jpeg_without_exif_thumbnail() -> bytes:
    full = _solid_image((512, 512), (90, 130, 200))
    sink = io.BytesIO()
    # Pillow emits a JFIF APP0 by default, no EXIF — perfect negative
    # fixture for the parser's "no thumbnail found" branch.
    full.save(sink, format="JPEG", quality=80)
    return sink.getvalue()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    safetensors_bytes = build_safetensors()
    (OUT_DIR / "tiny.safetensors").write_bytes(safetensors_bytes)
    print(f"wrote tiny.safetensors ({len(safetensors_bytes)} bytes)")

    parquet_bytes = build_parquet()
    (OUT_DIR / "tiny.parquet").write_bytes(parquet_bytes)
    print(f"wrote tiny.parquet ({len(parquet_bytes)} bytes)")

    jpeg_with = build_jpeg_with_exif_thumbnail()
    (OUT_DIR / "with_exif_thumb.jpg").write_bytes(jpeg_with)
    print(f"wrote with_exif_thumb.jpg ({len(jpeg_with)} bytes)")

    jpeg_without = build_jpeg_without_exif_thumbnail()
    (OUT_DIR / "no_exif_thumb.jpg").write_bytes(jpeg_without)
    print(f"wrote no_exif_thumb.jpg ({len(jpeg_without)} bytes)")


if __name__ == "__main__":
    main()
