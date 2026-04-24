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
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    safetensors_bytes = build_safetensors()
    (OUT_DIR / "tiny.safetensors").write_bytes(safetensors_bytes)
    print(f"wrote tiny.safetensors ({len(safetensors_bytes)} bytes)")

    parquet_bytes = build_parquet()
    (OUT_DIR / "tiny.parquet").write_bytes(parquet_bytes)
    print(f"wrote tiny.parquet ({len(parquet_bytes)} bytes)")


if __name__ == "__main__":
    main()
