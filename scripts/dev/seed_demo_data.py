#!/usr/bin/env python3
"""Create deterministic local demo data through KohakuHub's API surface."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import sys
import tarfile
import tempfile
import textwrap
from collections.abc import Callable, Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from hfutils import index as hf_index
from safetensors.numpy import save as save_safetensors

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kohakuhub.config import cfg
from kohakuhub.main import app
from kohakuhub.utils.s3 import init_storage

SEED_VERSION = "local-dev-demo-v2"
DEFAULT_PASSWORD = "KohakuDev123!"
PRIMARY_USERNAME = "mai_lin"
MANIFEST_PATH = ROOT_DIR / "hub-meta" / "dev" / "demo-seed-manifest.json"
INTERNAL_BASE_URL = (
    getattr(cfg.app, "internal_base_url", None)
    or cfg.app.base_url
    or "http://127.0.0.1:48888"
)


class SeedError(RuntimeError):
    """Raised when demo data creation fails."""


@dataclass(frozen=True)
class AccountSeed:
    username: str
    email: str
    full_name: str
    bio: str
    website: str
    social_media: dict[str, str]
    avatar_bg: str
    avatar_accent: str


@dataclass(frozen=True)
class OrganizationSeed:
    name: str
    description: str
    bio: str
    website: str
    social_media: dict[str, str]
    avatar_bg: str
    avatar_accent: str
    members: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CommitSeed:
    summary: str
    description: str
    files: tuple["SeedFile", ...]


@dataclass(frozen=True)
class FileSeed:
    path: str
    content: bytes | Callable[[], bytes]


@dataclass(frozen=True)
class RepoSeed:
    actor: str
    repo_type: str
    namespace: str
    name: str
    private: bool
    commits: tuple[CommitSeed, ...]
    branch: str | None = None
    tag: str | None = None
    download_path: str | None = None
    download_sessions: int = 0


SeedFile = tuple[str, bytes] | FileSeed


@dataclass(frozen=True)
class RemoteAsset:
    cache_name: str
    url: str
    sha256: str
    source_url: str


SEED_ASSET_CACHE_DIR = ROOT_DIR / "hub-meta" / "cache" / "seed-assets"


ACCOUNTS: tuple[AccountSeed, ...] = (
    AccountSeed(
        username="mai_lin",
        email="mai.lin@kohakuhub.dev",
        full_name="Mai Lin",
        bio=(
            "Product-minded ML engineer focused on reproducible dataset QA, "
            "small-model packaging, and local debugging workflows."
        ),
        website="https://kohakuhub.local/mai-lin",
        social_media={
            "github": "mai-lin-labs",
            "huggingface": "mai-lin-labs",
            "twitter_x": "mai_lin_ops",
        },
        avatar_bg="#183153",
        avatar_accent="#f59e0b",
    ),
    AccountSeed(
        username="leo_park",
        email="leo.park@kohakuhub.dev",
        full_name="Leo Park",
        bio=(
            "Frontend-heavy engineer who keeps repo demos honest with browser "
            "smoke tests and hand-curated example data."
        ),
        website="https://kohakuhub.local/leo-park",
        social_media={
            "github": "leo-park-dev",
            "threads": "leo.park.dev",
        },
        avatar_bg="#0f766e",
        avatar_accent="#f8fafc",
    ),
    AccountSeed(
        username="sara_chen",
        email="sara.chen@kohakuhub.dev",
        full_name="Sara Chen",
        bio=(
            "Annotation lead for invoice, receipt, and layout-heavy datasets. "
            "Prefers clean schemas over magical post-processing."
        ),
        website="https://kohakuhub.local/sara-chen",
        social_media={
            "github": "sara-chen-data",
            "huggingface": "sara-chen-data",
        },
        avatar_bg="#7c2d12",
        avatar_accent="#fde68a",
    ),
    AccountSeed(
        username="noah_kim",
        email="noah.kim@kohakuhub.dev",
        full_name="Noah Kim",
        bio=(
            "Ships compact vision models for harbor monitoring, segmentation, "
            "and camera-side smoke testing."
        ),
        website="https://kohakuhub.local/noah-kim",
        social_media={
            "github": "noah-kim-vision",
            "twitter_x": "noahkimvision",
        },
        avatar_bg="#1d4ed8",
        avatar_accent="#dbeafe",
    ),
    AccountSeed(
        username="ivy_ops",
        email="ivy.ops@kohakuhub.dev",
        full_name="Ivy Ops",
        bio=(
            "Release and infra support. Uses stable, boring fixtures so bug "
            "reports stay reproducible."
        ),
        website="https://kohakuhub.local/ivy-ops",
        social_media={
            "github": "ivy-ops",
        },
        avatar_bg="#3f3f46",
        avatar_accent="#f4f4f5",
    ),
)

ORGANIZATIONS: tuple[OrganizationSeed, ...] = (
    OrganizationSeed(
        name="aurora-labs",
        description=(
            "Applied document intelligence team building OCR-friendly models, "
            "datasets, and lightweight internal tooling."
        ),
        bio=(
            "Aurora Labs curates multilingual OCR assets for receipts, forms, "
            "and customer-service automation."
        ),
        website="https://aurora-labs.kohakuhub.local",
        social_media={
            "github": "aurora-labs",
            "huggingface": "aurora-labs",
        },
        avatar_bg="#312e81",
        avatar_accent="#e0e7ff",
        members=(
            ("mai_lin", "super-admin"),
            ("leo_park", "admin"),
            ("sara_chen", "member"),
            ("ivy_ops", "visitor"),
        ),
    ),
    OrganizationSeed(
        name="harbor-vision",
        description=(
            "Small computer-vision team for coastal monitoring, dock safety, "
            "and camera-ready deployment checks."
        ),
        bio=(
            "Harbor Vision maintains compact segmentation and inspection models "
            "for edge-friendly marine operations."
        ),
        website="https://harbor-vision.kohakuhub.local",
        social_media={
            "github": "harbor-vision",
            "twitter_x": "harborvision",
        },
        avatar_bg="#0f766e",
        avatar_accent="#ccfbf1",
        members=(
            ("mai_lin", "super-admin"),
            ("noah_kim", "super-admin"),
            ("leo_park", "visitor"),
        ),
    ),
)


def build_scale_accounts() -> tuple[AccountSeed, ...]:
    specs = (
        (
            "mila_zhou",
            "Mila Zhou",
            "Dataset release engineer focused on parquet validation, shard manifests, and large org operations.",
            "mila-zhou-data",
            "#4c1d95",
            "#ede9fe",
        ),
        (
            "ethan_reed",
            "Ethan Reed",
            "Model packaging owner who keeps tokenizer assets, shard indexes, and release notes tidy.",
            "ethan-reed-models",
            "#0f766e",
            "#ccfbf1",
        ),
        (
            "olivia_hart",
            "Olivia Hart",
            "Benchmarks multimodal search pipelines and curates reproducible evaluation bundles.",
            "olivia-hart-ai",
            "#9a3412",
            "#ffedd5",
        ),
        (
            "liam_north",
            "Liam North",
            "Owns local demo QA for file-tree pagination, deep directory browsing, and download flows.",
            "liam-north-labs",
            "#1d4ed8",
            "#dbeafe",
        ),
        (
            "zoe_park",
            "Zoe Park",
            "Keeps audio, image, and video fixtures aligned with product demos and ingestion checks.",
            "zoe-park-media",
            "#065f46",
            "#d1fae5",
        ),
        (
            "owen_davis",
            "Owen Davis",
            "Maintains synthetic but structurally realistic model exports for offline smoke testing.",
            "owen-davis-ml",
            "#7c2d12",
            "#fed7aa",
        ),
        (
            "mia_cross",
            "Mia Cross",
            "Curates metadata-heavy datasets with stable labels and repeatable schema previews.",
            "mia-cross-data",
            "#be123c",
            "#ffe4e6",
        ),
        (
            "lucas_tan",
            "Lucas Tan",
            "Documents retrieval pipelines, indexed archives, and annotation workflows for the team.",
            "lucas-tan-docs",
            "#1e3a8a",
            "#dbeafe",
        ),
        (
            "ava_scott",
            "Ava Scott",
            "Runs browser-first QA against large org listings, search results, and activity views.",
            "ava-scott-qa",
            "#854d0e",
            "#fef3c7",
        ),
        (
            "jackson_liu",
            "Jackson Liu",
            "Tracks media indexing pipelines and long-tail file format regressions.",
            "jackson-liu-index",
            "#155e75",
            "#cffafe",
        ),
        (
            "grace_hill",
            "Grace Hill",
            "Handles org membership operations and permissions reviews for shared demo spaces.",
            "grace-hill-ops",
            "#6d28d9",
            "#ede9fe",
        ),
        (
            "henry_wu",
            "Henry Wu",
            "Maintains multilingual dataset snapshots and local release validation checklists.",
            "henry-wu-data",
            "#92400e",
            "#fef3c7",
        ),
    )

    return tuple(
        AccountSeed(
            username=username,
            email=f"{username.replace('_', '.')}@kohakuhub.dev",
            full_name=full_name,
            bio=bio,
            website=f"https://kohakuhub.local/{username.replace('_', '-')}",
            social_media={
                "github": github_handle,
                "huggingface": github_handle,
            },
            avatar_bg=avatar_bg,
            avatar_accent=avatar_accent,
        )
        for username, full_name, bio, github_handle, avatar_bg, avatar_accent in specs
    )


SCALE_ACCOUNTS = build_scale_accounts()
ACCOUNTS = ACCOUNTS + SCALE_ACCOUNTS


OPEN_MEDIA_MEMBERS: tuple[tuple[str, str], ...] = (
    ("mai_lin", "super-admin"),
    ("leo_park", "admin"),
    ("sara_chen", "admin"),
    ("ivy_ops", "admin"),
    ("noah_kim", "member"),
    ("mila_zhou", "admin"),
    ("ethan_reed", "member"),
    ("olivia_hart", "member"),
    ("liam_north", "member"),
    ("zoe_park", "member"),
    ("owen_davis", "member"),
    ("mia_cross", "member"),
    ("lucas_tan", "member"),
    ("ava_scott", "visitor"),
    ("jackson_liu", "member"),
    ("grace_hill", "visitor"),
    ("henry_wu", "member"),
)

ORGANIZATIONS = ORGANIZATIONS + (
    OrganizationSeed(
        name="open-media-lab",
        description=(
            "Shared local-dev org packed with multimodal fixtures, large repo lists, "
            "and high-member-count collaboration scenarios."
        ),
        bio=(
            "Open Media Lab maintains reproducible multimodal assets for UI browsing, "
            "download tracking, metadata QA, and repository management demos."
        ),
        website="https://open-media-lab.kohakuhub.local",
        social_media={
            "github": "open-media-lab",
            "huggingface": "open-media-lab",
        },
        avatar_bg="#0f172a",
        avatar_accent="#bae6fd",
        members=OPEN_MEDIA_MEMBERS,
    ),
)


SAFEBOORU_IMAGE_ASSETS: tuple[RemoteAsset, ...] = (
    RemoteAsset(
        cache_name="safebooru-canal-reflections.png",
        url="https://cdn.donmai.us/original/79/a6/79a6c565714b36c5689131085d70a8a2.png",
        sha256="4b0b07d9f6d2658346525326567f4db7aebeae8b2ade4facb0f56f9972bdb669",
        source_url="https://safebooru.donmai.us/posts/11208212",
    ),
    RemoteAsset(
        cache_name="safebooru-mountain-church.jpg",
        url="https://cdn.donmai.us/original/dc/d4/dcd4a809e6efc402363720a6714bc4f7.jpg",
        sha256="a688df893449c757d979ff877aa1a3f006de649686ed0f5b101e807808e1dbc7",
        source_url="https://safebooru.donmai.us/posts/11207803",
    ),
    RemoteAsset(
        cache_name="safebooru-sand-plain.jpg",
        url="https://cdn.donmai.us/original/e8/20/e8201ebfcf9802fd5b74f126ae501406.jpg",
        sha256="14420b7849ab8922914d2ccc5d32abbf25ae26642ea50dfbb15096a8d9e85503",
        source_url="https://safebooru.donmai.us/posts/11207788",
    ),
    RemoteAsset(
        cache_name="safebooru-fence-field.jpg",
        url="https://cdn.donmai.us/original/5d/28/5d2833c4731c2b8631eefe5f89cd2541.jpg",
        sha256="e7eec10df1393ee661da300612b84cc4b0f8052d54aae4244cddaaaeb50a3d79",
        source_url="https://safebooru.donmai.us/posts/11207775",
    ),
    RemoteAsset(
        cache_name="safebooru-forest-lake.jpg",
        url="https://cdn.donmai.us/original/08/33/08330cb79116cd7dd1000f702b28c4f3.jpg",
        sha256="565520f058666a04953a1cbc8db67b2687fde240bb26b29d9b1008f562d78aa6",
        source_url="https://safebooru.donmai.us/posts/11207641",
    ),
    RemoteAsset(
        cache_name="safebooru-fantasy-castle.jpg",
        url="https://cdn.donmai.us/original/31/45/3145abe70177f3d01150a8fa9aa692dc.jpg",
        sha256="1d52643e22021364650176ff5c47e70ee101020f3329f9cd1f44b9aad739737a",
        source_url="https://safebooru.donmai.us/posts/11207593",
    ),
    RemoteAsset(
        cache_name="safebooru-phainon-cyrene.jpg",
        url=(
            "https://cdn.donmai.us/original/29/82/"
            "__phainon_and_cyrene_honkai_and_1_more_drawn_by_whyte_srsn__"
            "298282d12b00b563a09bebb65cc11116.jpg"
        ),
        sha256="8c8e04d47dea6ba020c6f0ec96932aaf760101b1cd358ba6eb829aa908f52b2f",
        source_url="https://safebooru.donmai.us/posts/9740876",
    ),
    RemoteAsset(
        cache_name="safebooru-sunflower-field.png",
        url=(
            "https://cdn.donmai.us/original/65/dd/"
            "__shirakami_fubuki_hololive_drawn_by_hyde_tabakko__"
            "65ddfa390ca539e6f9ed9658d65c77c4.png"
        ),
        sha256="c6a157e11758d8b1584502f772f1300c2a0b9e00ba7d9d883fd6b24b247181c0",
        source_url="https://safebooru.donmai.us/posts/9779697",
    ),
    RemoteAsset(
        cache_name="safebooru-grass-wonder.jpg",
        url=(
            "https://cdn.donmai.us/original/f9/5f/"
            "__grass_wonder_umamusume_and_1_more_drawn_by_fuuseppu__"
            "f95f1c3cdc9e69d9f2de613dc8117df2.jpg"
        ),
        sha256="35d08757090287d2fa465cc7ab959829b3df03c18e254580fc6ecbb8dc1cb118",
        source_url="https://safebooru.donmai.us/posts/9658576",
    ),
    RemoteAsset(
        cache_name="safebooru-paper-boat.jpg",
        url=(
            "https://cdn.donmai.us/original/f2/66/"
            "__sameko_saba_indie_virtual_youtuber_drawn_by_sky_above_me__"
            "f2664dc9d6a90473cf49234a3f30bea1.jpg"
        ),
        sha256="ae20506f36504895708fe1c85979c1dede228571044457bd5e91daaa1415ce7e",
        source_url="https://safebooru.donmai.us/posts/9599213",
    ),
)

REMOTE_MEDIA_ASSETS: dict[str, RemoteAsset] = {
    asset.cache_name: asset
    for asset in (
        *SAFEBOORU_IMAGE_ASSETS,
        RemoteAsset(
            cache_name="voices-speech.wav",
            url=(
                "https://download.pytorch.org/torchaudio/tutorial-assets/"
                "Lab41-SRI-VOiCES-src-sp0307-ch127535-sg0042.wav"
            ),
            sha256="c65fcd726d6b08c82c1e5dc7558f863cd8d483e3ed2f4a7bcf271dc1865ada14",
            source_url=(
                "https://download.pytorch.org/torchaudio/tutorial-assets/"
                "Lab41-SRI-VOiCES-src-sp0307-ch127535-sg0042.wav"
            ),
        ),
        RemoteAsset(
            cache_name="steam-train-whistle.wav",
            url=(
                "https://download.pytorch.org/torchaudio/tutorial-assets/"
                "steam-train-whistle-daniel_simon.wav"
            ),
            sha256="762b6783be7f20aa8be03812eeb33184bb5b1497db7422607a70b5d441fc45e9",
            source_url=(
                "https://download.pytorch.org/torchaudio/tutorial-assets/"
                "steam-train-whistle-daniel_simon.wav"
            ),
        ),
        RemoteAsset(
            cache_name="opencv-vtest.avi",
            url="https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/vtest.avi",
            sha256="45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf",
            source_url="https://github.com/opencv/opencv/blob/4.x/samples/data/vtest.avi",
        ),
    )
}


def text_bytes(body: str) -> bytes:
    return (textwrap.dedent(body).strip() + "\n").encode("utf-8")


def json_bytes(payload: dict | list) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def csv_bytes(rows: Iterable[Iterable[str]]) -> bytes:
    lines = [",".join(row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def jsonl_bytes(rows: Iterable[dict]) -> bytes:
    return ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode(
        "utf-8"
    )


def profile_space_files(title: str, summary: str, accent: str) -> tuple[tuple[str, bytes], ...]:
    return (
        (
            "README.md",
            text_bytes(
                f"""
                ---
                title: {title}
                emoji: "\u2605"
                colorFrom: indigo
                colorTo: amber
                sdk: gradio
                sdk_version: "4.44.0"
                ---

                # {title}

                {summary}

                This space exists so local profile pages render with realistic content
                instead of an empty placeholder repository.
                """
            ),
        ),
        (
            "app.py",
            text_bytes(
                f"""
                import gradio as gr

                demo = gr.Interface(
                    fn=lambda text: "{title}: " + text.strip(),
                    inputs=gr.Textbox(label="Prompt"),
                    outputs=gr.Textbox(label="Response"),
                    title="{title}",
                    description="{summary}",
                    theme=gr.themes.Soft(primary_hue="{accent}"),
                )

                if __name__ == "__main__":
                    demo.launch()
                """
            ),
        ),
        ("requirements.txt", text_bytes("gradio>=4.44.0")),
    )


def seed_file(path: str, content: bytes | Callable[[], bytes]) -> FileSeed:
    return FileSeed(path=path, content=content)


def materialize_seed_file(file_entry: SeedFile) -> tuple[str, bytes]:
    if isinstance(file_entry, FileSeed):
        content = file_entry.content() if callable(file_entry.content) else file_entry.content
        return file_entry.path, content
    return file_entry


_ASSET_BYTES_CACHE: dict[str, bytes] = {}


def patterned_bytes(label: str, size_bytes: int, *, header: bytes = b"") -> bytes:
    if size_bytes <= len(header):
        return header[:size_bytes]

    pattern = bytearray()
    counter = 0
    while len(pattern) < 4096:
        pattern.extend(hashlib.sha256(f"{label}:{counter}".encode("utf-8")).digest())
        counter += 1

    body_size = size_bytes - len(header)
    repeated = (bytes(pattern) * math.ceil(body_size / len(pattern)))[:body_size]
    return header + repeated


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_remote_asset(asset: RemoteAsset) -> bytes:
    cached = _ASSET_BYTES_CACHE.get(asset.cache_name)
    if cached is not None:
        return cached

    cache_path = SEED_ASSET_CACHE_DIR / asset.cache_name
    if cache_path.is_file():
        data = cache_path.read_bytes()
        if sha256_hex(data) == asset.sha256:
            _ASSET_BYTES_CACHE[asset.cache_name] = data
            return data

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        asset.url,
        timeout=180,
        headers={"User-Agent": "KohakuHubLocalSeed/1.0"},
    )
    response.raise_for_status()
    data = response.content
    actual_sha256 = sha256_hex(data)
    if actual_sha256 != asset.sha256:
        raise SeedError(
            f"Remote asset hash mismatch for {asset.cache_name}: "
            f"expected {asset.sha256}, got {actual_sha256}"
        )

    tmp_path = cache_path.with_suffix(f"{cache_path.suffix}.part")
    tmp_path.write_bytes(data)
    tmp_path.replace(cache_path)
    _ASSET_BYTES_CACHE[asset.cache_name] = data
    return data


def remote_asset_bytes(asset_name: str) -> bytes:
    return fetch_remote_asset(REMOTE_MEDIA_ASSETS[asset_name])


def make_realistic_float16_tensor(label: str, shape: tuple[int, ...]) -> np.ndarray:
    element_count = math.prod(shape)
    raw_values = np.frombuffer(patterned_bytes(label, element_count * 2), dtype="<u2").copy()
    raw_values = (raw_values & np.uint16(0x03FF)) | np.uint16(0x3C00)
    return np.ascontiguousarray(raw_values.view(np.float16).reshape(shape))


def make_safetensors_bytes(
    label: str,
    tensor_specs: tuple[tuple[str, tuple[int, ...]], ...],
    *,
    metadata: dict[str, str] | None = None,
) -> tuple[bytes, int]:
    tensors: dict[str, np.ndarray] = {}
    total_tensor_bytes = 0

    for tensor_name, shape in tensor_specs:
        tensor = make_realistic_float16_tensor(f"{label}:{tensor_name}", shape)
        tensors[tensor_name] = tensor
        total_tensor_bytes += tensor.nbytes

    payload = save_safetensors(
        tensors,
        metadata={
            "format": "pt",
            "seed_label": label,
            **(metadata or {}),
        },
    )
    return payload, total_tensor_bytes


def make_single_checkpoint_bytes(
    label: str,
    tensor_specs: tuple[tuple[str, tuple[int, ...]], ...],
) -> bytes:
    payload, _ = make_safetensors_bytes(label, tensor_specs)
    return payload


def make_parquet_bytes(
    label: str,
    *,
    row_count: int = 12000,
    payload_size: int = 2048,
) -> bytes:
    base_payload = patterned_bytes(f"{label}-payload", payload_size)
    payloads = []
    sample_ids = []
    captions = []
    durations = []
    for row_index in range(row_count):
        prefix = f"{label}:{row_index:05d}|".encode("utf-8")
        payloads.append(prefix + base_payload[: payload_size - len(prefix)])
        sample_ids.append(f"{label}_{row_index:05d}")
        captions.append(
            f"{label} multimodal benchmark row {row_index:05d} for local dataset preview checks."
        )
        durations.append(round(1.5 + (row_index % 11) * 0.25, 3))

    table = pa.table(
        {
            "sample_id": pa.array(sample_ids, type=pa.string()),
            "caption": pa.array(captions, type=pa.string()),
            "duration_seconds": pa.array(durations, type=pa.float32()),
            "payload": pa.array(payloads, type=pa.binary()),
        }
    )

    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression="NONE",
        use_dictionary=False,
        row_group_size=512,
    )
    return buffer.getvalue()


def make_indexed_tar_bundle(
    label: str,
    files: tuple[tuple[str, bytes], ...],
) -> tuple[bytes, bytes]:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as handle:
        for path, content in files:
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            handle.addfile(info, io.BytesIO(content))

    tar_bytes = tar_buffer.getvalue()
    with tempfile.TemporaryDirectory(prefix="kohakuhub-seed-tar-") as tmp_dir:
        tar_path = Path(tmp_dir) / f"{label}.tar"
        tar_path.write_bytes(tar_bytes)
        index_info = hf_index.tar_get_index_info(str(tar_path), silent=True)

    index_bytes = json_bytes(index_info)
    return tar_bytes, index_bytes


def make_deep_tree_files(label: str) -> tuple[SeedFile, ...]:
    files: list[SeedFile] = []
    for section in range(1, 7):
        for shard in range(1, 9):
            for leaf in range(1, 7):
                path = (
                    f"catalog/section-{section:02d}/tier-{shard:02d}/"
                    f"branch-{leaf:02d}/node-{section:02d}-{shard:02d}-{leaf:02d}/"
                    f"entry-{section:02d}-{shard:02d}-{leaf:02d}.json"
                )
                files.append(
                    (
                        path,
                        json_bytes(
                            {
                                "checksum": hashlib.sha256(path.encode("utf-8")).hexdigest(),
                                "fixture": label,
                                "leaf": leaf,
                                "section": section,
                                "shard": shard,
                            }
                        ),
                    )
                )

    files.extend(
        (
            (
                "README.md",
                text_bytes(
                    """
                    # hierarchy-crawl-fixtures

                    This repo intentionally contains many files and deep path nesting so
                    local tree browsing, pagination, and search remain easy to exercise.
                    """
                ),
            ),
            (
                "manifests/root-index.json",
                json_bytes(
                    {
                        "depth": 4,
                        "generated_files": len(files),
                        "label": label,
                    }
                ),
            ),
        )
    )
    return tuple(files)


def build_repo_seeds() -> tuple[RepoSeed, ...]:
    return (
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="mai_lin",
            name="lineart-caption-base",
            private=False,
            commits=(
                CommitSeed(
                    summary="Bootstrap base caption model",
                    description=(
                        "Create the public demo model repo with a realistic README, "
                        "lightweight config, and a small LFS-tracked checkpoint."
                    ),
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: mit
                                library_name: transformers
                                pipeline_tag: image-to-text
                                tags:
                                  - captioning
                                  - line-art
                                  - document-vision
                                ---

                                # lineart-caption-base

                                A compact caption model tuned for monochrome line art,
                                icon-heavy diagrams, and OCR-adjacent illustrations.

                                ## Intended use

                                - draft captions for internal QA dashboards
                                - generate quick prompts for reviewers
                                - validate frontend metadata rendering
                                """
                            ),
                        ),
                        (
                            "config.json",
                            json_bytes(
                                {
                                    "architectures": ["VisionEncoderDecoderModel"],
                                    "decoder_layers": 6,
                                    "encoder_layers": 12,
                                    "image_size": 448,
                                    "model_type": "lineart-caption-base",
                                    "vocab_size": 32000,
                                }
                            ),
                        ),
                        (
                            "tokenizer.json",
                            json_bytes(
                                {
                                    "added_tokens": [],
                                    "normalizer": {"type": "NFKC"},
                                    "pre_tokenizer": {"type": "Whitespace"},
                                    "version": "1.0",
                                }
                            ),
                        ),
                        ("examples/prompt.txt", text_bytes("Describe the icon, layout, and visible text.")),
                        seed_file(
                            "checkpoints/lineart-caption-base.safetensors",
                            lambda: make_single_checkpoint_bytes(
                                "lineart-caption-base",
                                (
                                    (
                                        "encoder.vision_model.embeddings.patch_embedding.weight",
                                        (4096, 1024),
                                    ),
                                    ("decoder.model.embed_tokens.weight", (1024, 768)),
                                ),
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add eval notes and release metrics",
                    description="Follow-up commit so commit history and file updates are visible in local UI.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: mit
                                library_name: transformers
                                pipeline_tag: image-to-text
                                tags:
                                  - captioning
                                  - line-art
                                  - document-vision
                                ---

                                # lineart-caption-base

                                A compact caption model tuned for monochrome line art,
                                icon-heavy diagrams, and OCR-adjacent illustrations.

                                ## Current release

                                - validation CIDEr: 1.38
                                - latency target: <120 ms on local A10G
                                - known gap: dense legends still need manual review
                                """
                            ),
                        ),
                        (
                            "eval/metrics.json",
                            json_bytes(
                                {
                                    "cider": 1.38,
                                    "clip_score": 0.284,
                                    "latency_ms_p50": 87,
                                    "latency_ms_p95": 114,
                                }
                            ),
                        ),
                        (
                            "docs/training-notes.md",
                            text_bytes(
                                """
                                # Training Notes

                                - Base corpus: 82k internal line-art render pairs
                                - Additional hard negatives: 4k cluttered signage crops
                                - Checkpoint exported for small-batch browser smoke tests
                                """
                            ),
                        ),
                    ),
                ),
            ),
            branch="ablation-notes",
            tag="v0.2.1",
            download_path="checkpoints/lineart-caption-base.safetensors",
            download_sessions=4,
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="mai_lin",
            name="street-sign-zh-en",
            private=False,
            commits=(
                CommitSeed(
                    summary="Import bilingual street sign dataset",
                    description="Seed a CSV-backed dataset that exercises dataset preview and tree views.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: cc-by-4.0
                                task_categories:
                                  - image-text-to-text
                                language:
                                  - zh
                                  - en
                                pretty_name: Street Sign ZH EN
                                ---

                                # street-sign-zh-en

                                A small bilingual dataset for OCR-friendly sign translation and
                                layout QA. Rows keep the original text, translation, and scene tag.
                                """
                            ),
                        ),
                        (
                            "data/train.csv",
                            csv_bytes(
                                (
                                    ("image", "text_zh", "text_en", "scene"),
                                    ("img_0001.png", "\u5317\u4eac\u7ad9", "Beijing Railway Station", "station"),
                                    ("img_0002.png", "\u5c0f\u5fc3\u53f0\u9636", "Watch Your Step", "retail"),
                                    ("img_0003.png", "\u7981\u6b62\u5438\u70df", "No Smoking", "hospital"),
                                    ("img_0004.png", "\u53f3\u8f6c\u8f66\u9053", "Right Turn Only", "road"),
                                )
                            ),
                        ),
                        (
                            "data/validation.csv",
                            csv_bytes(
                                (
                                    ("image", "text_zh", "text_en", "scene"),
                                    ("val_0001.png", "\u51fa\u53e3", "Exit", "mall"),
                                    ("val_0002.png", "\u670d\u52a1\u53f0", "Service Desk", "airport"),
                                )
                            ),
                        ),
                        (
                            "metadata/features.json",
                            json_bytes(
                                {
                                    "image": "string",
                                    "text_zh": "string",
                                    "text_en": "string",
                                    "scene": "string",
                                }
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add preview samples for dataset viewer",
                    description="Include JSONL samples and notebook notes for local bug reproduction.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: cc-by-4.0
                                task_categories:
                                  - image-text-to-text
                                language:
                                  - zh
                                  - en
                                pretty_name: Street Sign ZH EN
                                ---

                                # street-sign-zh-en

                                A small bilingual dataset for OCR-friendly sign translation and
                                layout QA. Rows keep the original text, translation, and scene tag.

                                ## Notes

                                Validation rows intentionally mix transport, retail, and public
                                service scenarios so sorting and filtering bugs are easier to spot.
                                """
                            ),
                        ),
                        (
                            "previews/samples.jsonl",
                            jsonl_bytes(
                                (
                                    {
                                        "image": "img_0001.png",
                                        "text_zh": "\u5317\u4eac\u7ad9",
                                        "text_en": "Beijing Railway Station",
                                        "scene": "station",
                                    },
                                    {
                                        "image": "img_0002.png",
                                        "text_zh": "\u5c0f\u5fc3\u53f0\u9636",
                                        "text_en": "Watch Your Step",
                                        "scene": "retail",
                                    },
                                )
                            ),
                        ),
                        (
                            "notebooks/README.md",
                            text_bytes(
                                """
                                # Notebook Notes

                                This dataset is intentionally tiny in local dev. The point is to
                                exercise preview, pagination, and schema rendering without waiting
                                on a large bootstrap import.
                                """
                            ),
                        ),
                    ),
                ),
            ),
            branch="qa-pass",
            tag="2026-04-demo",
            download_path="data/train.csv",
            download_sessions=8,
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="space",
            namespace="mai_lin",
            name="mai_lin",
            private=False,
            commits=(
                CommitSeed(
                    summary="Create profile showcase space",
                    description="Provide a same-name space so local profile pages render a realistic card.",
                    files=profile_space_files(
                        "Mai Lin Workspace",
                        "Small utilities and pinned demos used for local reproduction.",
                        "amber",
                    ),
                ),
                CommitSeed(
                    summary="Add profile theme preset",
                    description="A second commit makes the space history non-empty for UI testing.",
                    files=(
                        (
                            "assets/theme.json",
                            json_bytes(
                                {
                                    "accent": "amber",
                                    "layout": "split",
                                    "panels": ["repos", "activity", "notes"],
                                }
                            ),
                        ),
                    ),
                ),
            ),
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="mai_lin",
            name="internal-evals",
            private=True,
            commits=(
                CommitSeed(
                    summary="Seed private eval artifacts",
                    description="Keep one private user-owned repo for auth and permission checks.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                # internal-evals

                                Private staging area for eval summaries and failure-case review.
                                This repo is intentionally private and only accessible to Mai.
                                """
                            ),
                        ),
                        (
                            "runs/2026-04-15-summary.json",
                            json_bytes(
                                {
                                    "caption_regressions": 7,
                                    "dataset": "street-sign-zh-en",
                                    "notes": "False positives cluster around mirrored storefront text.",
                                }
                            ),
                        ),
                        (
                            "data/failure_cases.jsonl",
                            jsonl_bytes(
                                (
                                    {
                                        "file": "eval_001.png",
                                        "issue": "mirror_text",
                                        "severity": "medium",
                                    },
                                    {
                                        "file": "eval_002.png",
                                        "issue": "crowded_legend",
                                        "severity": "high",
                                    },
                                )
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add reviewer checklist",
                    description="Second commit for commit-history coverage on a private repo.",
                    files=(
                        (
                            "notes/reviewer-checklist.md",
                            text_bytes(
                                """
                                # Reviewer Checklist

                                - confirm sample renders in dataset viewer
                                - compare translated text against bilingual CSV rows
                                - log UI regressions with the seeded repo name
                                """
                            ),
                        ),
                    ),
                ),
            ),
            download_path="runs/2026-04-15-summary.json",
            download_sessions=1,
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="space",
            namespace="aurora-labs",
            name="aurora-labs",
            private=False,
            commits=(
                CommitSeed(
                    summary="Create org showcase space",
                    description="Same-name org space keeps organization profile pages representative.",
                    files=profile_space_files(
                        "Aurora Labs Demo Portal",
                        "Landing page for OCR demos, pinned datasets, and release notes.",
                        "indigo",
                    ),
                ),
                CommitSeed(
                    summary="Add roadmap note",
                    description="A lightweight follow-up commit for org space history.",
                    files=(
                        (
                            "docs/roadmap.md",
                            text_bytes(
                                """
                                # Local Demo Roadmap

                                - tighten OCR-lite benchmark reporting
                                - keep receipt-layout-bench labels stable for bug repro
                                - mirror one private support model for permission testing
                                """
                            ),
                        ),
                    ),
                ),
            ),
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="aurora-labs",
            name="aurora-ocr-lite",
            private=False,
            commits=(
                CommitSeed(
                    summary="Publish OCR-lite baseline",
                    description="Public model repo with LFS checkpoint and readable metadata.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: apache-2.0
                                library_name: transformers
                                pipeline_tag: image-to-text
                                tags:
                                  - ocr
                                  - receipts
                                  - multilingual
                                ---

                                # aurora-ocr-lite

                                An OCR-focused checkpoint for receipt snippets, payment slips,
                                and service counter paperwork.
                                """
                            ),
                        ),
                        (
                            "config.json",
                            json_bytes(
                                {
                                    "backbone": "vit-small-patch16-384",
                                    "decoder": "bart-base",
                                    "max_position_embeddings": 512,
                                    "torch_dtype": "float16",
                                }
                            ),
                        ),
                        (
                            "vocab.txt",
                            text_bytes(
                                """
                                [PAD]
                                [UNK]
                                total
                                subtotal
                                tax
                                cashier
                                paid
                                """
                            ),
                        ),
                        seed_file(
                            "checkpoints/aurora-ocr-lite.safetensors",
                            lambda: make_single_checkpoint_bytes(
                                "aurora-ocr-lite",
                                (
                                    ("encoder.patch_embed.proj.weight", (6144, 1024)),
                                    ("decoder.model.embed_tokens.weight", (2048, 1024)),
                                ),
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add benchmark export and release notes",
                    description="Keep one public org model slightly more active for trending and history views.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: apache-2.0
                                library_name: transformers
                                pipeline_tag: image-to-text
                                tags:
                                  - ocr
                                  - receipts
                                  - multilingual
                                ---

                                # aurora-ocr-lite

                                An OCR-focused checkpoint for receipt snippets, payment slips,
                                and service counter paperwork.

                                ## Release notes

                                - reduced hallucinated currency markers on narrow receipt crops
                                - added benchmark export used by the admin dashboard smoke tests
                                """
                            ),
                        ),
                        (
                            "eval/benchmark.json",
                            json_bytes(
                                {
                                    "cer": 0.081,
                                    "wer": 0.119,
                                    "latency_ms_p50": 64,
                                    "latency_ms_p95": 92,
                                }
                            ),
                        ),
                        (
                            "scripts/export_notes.md",
                            text_bytes(
                                """
                                # Export Notes

                                Checkpoint is intentionally small and fake. It only exists so local
                                flows hit LFS, quota, and file-tree code paths.
                                """
                            ),
                        ),
                    ),
                ),
            ),
            branch="benchmark-v2",
            tag="v0.3.0",
            download_path="checkpoints/aurora-ocr-lite.safetensors",
            download_sessions=12,
        ),
        RepoSeed(
            actor="leo_park",
            repo_type="dataset",
            namespace="aurora-labs",
            name="receipt-layout-bench",
            private=False,
            commits=(
                CommitSeed(
                    summary="Create receipt layout benchmark",
                    description="Public dataset repo with JSONL splits for dataset preview coverage.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: cc-by-4.0
                                pretty_name: Receipt Layout Bench
                                task_categories:
                                  - token-classification
                                ---

                                # receipt-layout-bench

                                Annotation benchmark for merchant, total, tax, and timestamp spans.
                                """
                            ),
                        ),
                        (
                            "splits/train.jsonl",
                            jsonl_bytes(
                                (
                                    {
                                        "image": "train_0001.png",
                                        "merchant": "North Pier Cafe",
                                        "total": "18.40",
                                        "currency": "USD",
                                    },
                                    {
                                        "image": "train_0002.png",
                                        "merchant": "River Town Mart",
                                        "total": "42.15",
                                        "currency": "USD",
                                    },
                                )
                            ),
                        ),
                        (
                            "splits/test.jsonl",
                            jsonl_bytes(
                                (
                                    {
                                        "image": "test_0001.png",
                                        "merchant": "Airport Bento",
                                        "total": "9.80",
                                        "currency": "USD",
                                    },
                                    {
                                        "image": "test_0002.png",
                                        "merchant": "Harbor Books",
                                        "total": "27.10",
                                        "currency": "USD",
                                    },
                                )
                            ),
                        ),
                        (
                            "schema/fields.json",
                            json_bytes(
                                {
                                    "merchant": "string",
                                    "total": "string",
                                    "currency": "string",
                                    "timestamp": "string",
                                }
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add annotation guide",
                    description="Second dataset commit for history, tree diffing, and docs rendering.",
                    files=(
                        (
                            "docs/annotation-guide.md",
                            text_bytes(
                                """
                                # Annotation Guide

                                - mark printed totals, not handwritten notes
                                - keep currency in a dedicated field
                                - preserve merchant spelling from source image
                                """
                            ),
                        ),
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: cc-by-4.0
                                pretty_name: Receipt Layout Bench
                                task_categories:
                                  - token-classification
                                ---

                                # receipt-layout-bench

                                Annotation benchmark for merchant, total, tax, and timestamp spans.

                                The local seed intentionally mixes neat and messy receipts to cover
                                pagination, filters, and table previews.
                                """
                            ),
                        ),
                    ),
                ),
            ),
            branch="supplier-a-refresh",
            tag="v1.0.0",
            download_path="splits/test.jsonl",
            download_sessions=5,
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="aurora-labs",
            name="customer-support-rag",
            private=True,
            commits=(
                CommitSeed(
                    summary="Seed private support model workspace",
                    description="Private org repo for auth-only browsing and settings checks.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                # customer-support-rag

                                Internal-only retrieval and prompt assets for support workflows.
                                This repo is private and visible to Aurora Labs members only.
                                """
                            ),
                        ),
                        (
                            "prompt/system.txt",
                            text_bytes(
                                """
                                You are a cautious support assistant. Answer only with facts from
                                the indexed knowledge base, and cite the exact article title.
                                """
                            ),
                        ),
                        (
                            "retrieval/index-schema.json",
                            json_bytes(
                                {
                                    "article_id": "string",
                                    "channel": "string",
                                    "lang": "string",
                                    "text": "string",
                                }
                            ),
                        ),
                        (
                            "config.json",
                            json_bytes(
                                {
                                    "chunk_size": 384,
                                    "embedding_model": "bge-small-en-v1.5",
                                    "top_k": 6,
                                }
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add ops runbook",
                    description="Keep a second private-org commit for local history inspection.",
                    files=(
                        (
                            "docs/runbook.md",
                            text_bytes(
                                """
                                # Runbook

                                - refresh embeddings weekly
                                - snapshot prompts before frontend demos
                                - record regressions against the fixed local seed data
                                """
                            ),
                        ),
                    ),
                ),
            ),
            download_path="prompt/system.txt",
            download_sessions=1,
        ),
        RepoSeed(
            actor="noah_kim",
            repo_type="model",
            namespace="harbor-vision",
            name="marine-seg-small",
            private=False,
            commits=(
                CommitSeed(
                    summary="Publish marine segmentation starter model",
                    description="Public vision model with another fake LFS checkpoint.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: apache-2.0
                                pipeline_tag: image-segmentation
                                tags:
                                  - segmentation
                                  - marine
                                  - edge
                                ---

                                # marine-seg-small

                                Compact segmentation model for harbor waterlines, safety zones,
                                and dock equipment outlines.
                                """
                            ),
                        ),
                        (
                            "config.json",
                            json_bytes(
                                {
                                    "backbone": "convnext-tiny",
                                    "classes": ["water", "dock", "vessel", "buoy"],
                                    "input_size": 512,
                                }
                            ),
                        ),
                        (
                            "labels.json",
                            json_bytes(
                                {
                                    "0": "water",
                                    "1": "dock",
                                    "2": "vessel",
                                    "3": "buoy",
                                }
                            ),
                        ),
                        seed_file(
                            "checkpoints/marine-seg-small.safetensors",
                            lambda: make_single_checkpoint_bytes(
                                "marine-seg-small",
                                (
                                    ("backbone.stem.conv1.weight", (4096, 1536)),
                                    ("decode_head.classifier.weight", (1024, 1024)),
                                ),
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add harbor evaluation report",
                    description="Second model commit for history and stats coverage.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: apache-2.0
                                pipeline_tag: image-segmentation
                                tags:
                                  - segmentation
                                  - marine
                                  - edge
                                ---

                                # marine-seg-small

                                Compact segmentation model for harbor waterlines, safety zones,
                                and dock equipment outlines.

                                ## Eval highlights

                                - best IoU on waterline masks from overcast camera feeds
                                - weaker on stacked cargo edges during dusk
                                """
                            ),
                        ),
                        (
                            "eval/coastal-harbor.json",
                            json_bytes(
                                {
                                    "iou_dock": 0.84,
                                    "iou_vessel": 0.79,
                                    "iou_water": 0.91,
                                }
                            ),
                        ),
                    ),
                ),
            ),
            branch="saltwater-eval",
            tag="v1.1.0",
            download_path="checkpoints/marine-seg-small.safetensors",
            download_sessions=6,
        ),
        RepoSeed(
            actor="noah_kim",
            repo_type="space",
            namespace="harbor-vision",
            name="smoke-test-dashboard",
            private=True,
            commits=(
                CommitSeed(
                    summary="Create private smoke-test dashboard",
                    description="Private org space used for auth and space rendering checks.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                # smoke-test-dashboard

                                Private dashboard for camera ingest smoke tests and deployment sign-off.
                                """
                            ),
                        ),
                        (
                            "app.py",
                            text_bytes(
                                """
                                import gradio as gr

                                dashboard = gr.Interface(
                                    fn=lambda status: f"dashboard status: {status}",
                                    inputs=gr.Textbox(label="Input"),
                                    outputs=gr.Textbox(label="Output"),
                                    title="Smoke Test Dashboard",
                                )

                                if __name__ == "__main__":
                                    dashboard.launch()
                                """
                            ),
                        ),
                        ("requirements.txt", text_bytes("gradio>=4.44.0")),
                    ),
                ),
                CommitSeed(
                    summary="Add dashboard notes",
                    description="Second private-space commit for browsing stateful history locally.",
                    files=(
                        (
                            "dashboards/README.md",
                            text_bytes(
                                """
                                # Dashboard Notes

                                Fixed local fixtures are better than random telemetry when the goal
                                is to reproduce layout and auth bugs.
                                """
                            ),
                        ),
                    ),
                ),
            ),
            download_path="README.md",
            download_sessions=1,
        ),
        RepoSeed(
            actor="leo_park",
            repo_type="space",
            namespace="leo_park",
            name="formula-checker-lite",
            private=False,
            commits=(
                CommitSeed(
                    summary="Create public formula checker demo",
                    description="Lightweight public space for user profile and space listings.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                # formula-checker-lite

                                Small browser demo that validates spreadsheet-style formulas and
                                flags obviously broken references.
                                """
                            ),
                        ),
                        (
                            "app.py",
                            text_bytes(
                                """
                                import gradio as gr

                                def validate(expr: str) -> str:
                                    return "looks valid" if "=" in expr else "missing leading ="

                                demo = gr.Interface(
                                    fn=validate,
                                    inputs=gr.Textbox(label="Formula"),
                                    outputs=gr.Textbox(label="Status"),
                                    title="Formula Checker Lite",
                                )

                                if __name__ == "__main__":
                                    demo.launch()
                                """
                            ),
                        ),
                        ("requirements.txt", text_bytes("gradio>=4.44.0")),
                    ),
                ),
                CommitSeed(
                    summary="Add preset expressions",
                    description="Second commit keeps this user-owned space non-trivial.",
                    files=(
                        (
                            "assets/presets.json",
                            json_bytes(
                                {
                                    "valid": "=SUM(A1:A3)",
                                    "invalid": "SUM(A1:A3)",
                                    "cross_sheet": "=Sheet2!B4",
                                }
                            ),
                        ),
                    ),
                ),
            ),
            download_path="README.md",
            download_sessions=2,
        ),
        RepoSeed(
            actor="sara_chen",
            repo_type="dataset",
            namespace="sara_chen",
            name="invoice-entities-mini",
            private=False,
            commits=(
                CommitSeed(
                    summary="Seed invoice entity dataset",
                    description="Public user dataset so profile pages are not empty.",
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: cc-by-4.0
                                pretty_name: Invoice Entities Mini
                                task_categories:
                                  - token-classification
                                ---

                                # invoice-entities-mini

                                Tiny invoice entity dataset for local schema, preview, and table rendering checks.
                                """
                            ),
                        ),
                        (
                            "data/train.jsonl",
                            jsonl_bytes(
                                (
                                    {
                                        "invoice_id": "inv_1001",
                                        "vendor": "Blue Harbor Logistics",
                                        "amount": "1240.00",
                                    },
                                    {
                                        "invoice_id": "inv_1002",
                                        "vendor": "Northline Design",
                                        "amount": "315.50",
                                    },
                                )
                            ),
                        ),
                        (
                            "data/test.jsonl",
                            jsonl_bytes(
                                (
                                    {
                                        "invoice_id": "inv_2001",
                                        "vendor": "River Street Foods",
                                        "amount": "89.20",
                                    },
                                )
                            ),
                        ),
                        (
                            "schema.json",
                            json_bytes(
                                {
                                    "invoice_id": "string",
                                    "vendor": "string",
                                    "amount": "string",
                                }
                            ),
                        ),
                    ),
                ),
                CommitSeed(
                    summary="Add notebook notes",
                    description="Second public dataset commit for file tree and commit history coverage.",
                    files=(
                        (
                            "notebooks/README.md",
                            text_bytes(
                                """
                                # Notebook Notes

                                Keep the local seed tiny. If a preview bug shows up here, it is much
                                easier to reason about than a random large import.
                                """
                            ),
                        ),
                    ),
                ),
            ),
            download_path="data/train.jsonl",
            download_sessions=3,
        ),
    )


def build_open_media_core_repo_seeds() -> tuple[RepoSeed, ...]:
    archive_cache: dict[str, tuple[bytes, bytes]] = {}
    model_bundle_cache: dict[str, dict[str, bytes]] = {}

    top_level_image_assets = (
        SAFEBOORU_IMAGE_ASSETS[:4] + SAFEBOORU_IMAGE_ASSETS[-2:]
    )
    archive_image_assets = SAFEBOORU_IMAGE_ASSETS
    top_level_media_entries = (
        ("media/audio/voices-speech.wav", "voices-speech.wav"),
        ("media/audio/steam-train-whistle.wav", "steam-train-whistle.wav"),
        ("media/video/opencv-vtest.avi", "opencv-vtest.avi"),
        *(
            (f"media/images/{asset.cache_name}", asset.cache_name)
            for asset in top_level_image_assets
        ),
    )

    def archive_bundle() -> tuple[bytes, bytes]:
        cached = archive_cache.get("bundle")
        if cached is not None:
            return cached

        archived_files = tuple(
            (f"images/{asset.cache_name}", remote_asset_bytes(asset.cache_name))
            for asset in archive_image_assets
        ) + (
            (
                "annotations/captions.jsonl",
                jsonl_bytes(
                    tuple(
                        {
                            "asset": f"images/{asset.cache_name}",
                            "caption": f"SafeBooru fixture mirrored from {asset.source_url}.",
                            "source_url": asset.source_url,
                            "split": "train" if index < 6 else "validation",
                        }
                        for index, asset in enumerate(archive_image_assets)
                    )
                ),
            ),
            (
                "metadata/source-assets.json",
                json_bytes(
                    {
                        "assets": [
                            {
                                "path": f"images/{asset.cache_name}",
                                "sha256": asset.sha256,
                                "size": len(remote_asset_bytes(asset.cache_name)),
                                "source_url": asset.source_url,
                            }
                            for asset in archive_image_assets
                        ]
                    }
                ),
            ),
        )
        cached = make_indexed_tar_bundle("open-media-archive", archived_files)
        archive_cache["bundle"] = cached
        return cached

    def model_bundle() -> dict[str, bytes]:
        cached = model_bundle_cache.get("bundle")
        if cached is not None:
            return cached

        shard_specs = (
            (
                "model-00001-of-00003.safetensors",
                (
                    ("language_model.embed_tokens.weight", (7680, 4096)),
                    ("language_model.layers.0.mlp.down_proj.weight", (4096, 2048)),
                ),
            ),
            (
                "model-00002-of-00003.safetensors",
                (("language_model.layers.14.self_attn.q_proj.weight", (8192, 4096)),),
            ),
            (
                "model-00003-of-00003.safetensors",
                (
                    ("language_model.layers.27.mlp.up_proj.weight", (8192, 4096)),
                    ("vision_tower.vision_model.embeddings.class_embedding", (1, 1408)),
                ),
            ),
        )

        bundle: dict[str, bytes] = {}
        total_tensor_bytes = 0
        weight_map: dict[str, str] = {}
        for filename, tensor_specs in shard_specs:
            payload, tensor_bytes = make_safetensors_bytes(
                f"vision-language-assistant-3b:{filename}",
                tensor_specs,
            )
            bundle[filename] = payload
            total_tensor_bytes += tensor_bytes
            for tensor_name, _ in tensor_specs:
                weight_map[tensor_name] = filename

        bundle["model.safetensors.index.json"] = json_bytes(
            {
                "metadata": {"total_size": total_tensor_bytes},
                "weight_map": weight_map,
            }
        )
        model_bundle_cache["bundle"] = bundle
        return bundle

    multimodal_files: tuple[SeedFile, ...] = (
        (
            "README.md",
            text_bytes(
                """
                ---
                license: cc-by-4.0
                pretty_name: Open Media Multimodal Suite
                task_categories:
                  - automatic-speech-recognition
                  - image-to-text
                  - video-classification
                tags:
                  - parquet
                  - indexed-tar
                  - multimodal
                ---

                # multimodal-benchmark-suite

                Local benchmark dataset with real parquet shards, a hfutils.index-compatible
                tar archive, a larger SafeBooru image set, torchaudio sample WAV files, and an
                OpenCV sample video for frontend and admin demos.
                """
            ),
        ),
        (
            "dataset_infos.json",
            json_bytes(
                {
                    "default": {
                        "config_name": "default",
                        "features": {
                            "caption": {"dtype": "string", "_type": "Value"},
                            "duration_seconds": {"dtype": "float32", "_type": "Value"},
                            "payload": {"dtype": "binary", "_type": "Value"},
                            "sample_id": {"dtype": "string", "_type": "Value"},
                        },
                        "splits": {
                            "train": {
                                "name": "train",
                                "num_examples": 12000,
                            }
                        },
                    }
                }
            ),
        ),
        (
            "metadata/feature-card.json",
            json_bytes(
                {
                    "archive_index": "archives/raw-bundle-0000.json",
                    "archive_tar": "archives/raw-bundle-0000.tar",
                    "media_assets": [path for path, _ in top_level_media_entries],
                    "parquet_train": "parquet/train-00000-of-00001.parquet",
                }
            ),
        ),
        (
            "metadata/source-assets.json",
            json_bytes(
                {
                    "assets": [
                        {
                            "path": path,
                            "sha256": REMOTE_MEDIA_ASSETS[asset_name].sha256,
                            "size": len(remote_asset_bytes(asset_name)),
                            "source_url": REMOTE_MEDIA_ASSETS[asset_name].source_url,
                        }
                        for path, asset_name in top_level_media_entries
                    ]
                }
            ),
        ),
        seed_file(
            "parquet/train-00000-of-00001.parquet",
            lambda: make_parquet_bytes("open-media-train", row_count=12000, payload_size=2048),
        ),
        seed_file(
            "parquet/validation-00000-of-00001.parquet",
            lambda: make_parquet_bytes("open-media-validation", row_count=1500, payload_size=1024),
        ),
        *(
            seed_file(path, lambda asset_name=asset_name: remote_asset_bytes(asset_name))
            for path, asset_name in top_level_media_entries
        ),
        seed_file("archives/raw-bundle-0000.tar", lambda: archive_bundle()[0]),
        seed_file("archives/raw-bundle-0000.json", lambda: archive_bundle()[1]),
    )

    model_files: tuple[SeedFile, ...] = (
        (
            "README.md",
            text_bytes(
                """
                ---
                license: apache-2.0
                library_name: transformers
                pipeline_tag: image-text-to-text
                tags:
                  - multimodal
                  - sharded-weights
                  - local-dev
                ---

                # vision-language-assistant-3b

                Local multimodal checkpoint with real sharded safetensors weights,
                tokenizer assets, and processor configs.
                """
            ),
        ),
        (
            "config.json",
            json_bytes(
                {
                    "architectures": ["LlavaForConditionalGeneration"],
                    "hidden_size": 3072,
                    "max_position_embeddings": 8192,
                    "model_type": "llava",
                    "num_hidden_layers": 28,
                    "torch_dtype": "bfloat16",
                    "vocab_size": 128256,
                }
            ),
        ),
        (
            "generation_config.json",
            json_bytes(
                {
                    "do_sample": False,
                    "max_new_tokens": 512,
                    "temperature": 0.2,
                    "top_p": 0.9,
                }
            ),
        ),
        (
            "preprocessor_config.json",
            json_bytes(
                {
                    "crop_size": 448,
                    "do_center_crop": True,
                    "do_normalize": True,
                    "image_mean": [0.48145466, 0.4578275, 0.40821073],
                    "image_std": [0.26862954, 0.26130258, 0.27577711],
                }
            ),
        ),
        (
            "processor_config.json",
            json_bytes(
                {
                    "chat_template": "chat_template.jinja",
                    "image_processor_type": "CLIPImageProcessor",
                    "processor_class": "AutoProcessor",
                    "tokenizer_class": "PreTrainedTokenizerFast",
                }
            ),
        ),
        (
            "special_tokens_map.json",
            json_bytes(
                {
                    "bos_token": "<s>",
                    "eos_token": "</s>",
                    "image_token": "<image>",
                    "pad_token": "<pad>",
                }
            ),
        ),
        (
            "tokenizer_config.json",
            json_bytes(
                {
                    "add_bos_token": True,
                    "chat_template": "{% for message in messages %}{{ message['role'] }}: {{ message['content'] }}{% endfor %}",
                    "legacy": False,
                    "model_max_length": 8192,
                    "padding_side": "right",
                }
            ),
        ),
        (
            "tokenizer.json",
            json_bytes(
                {
                    "added_tokens": [{"content": "<image>", "id": 128000}],
                    "normalizer": {"type": "NFKC"},
                    "pre_tokenizer": {"type": "ByteLevel"},
                    "version": "1.0",
                }
            ),
        ),
        (
            "chat_template.jinja",
            text_bytes(
                "{{ bos_token }}{% for message in messages %}{{ message['role'] }}: {{ message['content'] }}{% endfor %}{{ eos_token }}"
            ),
        ),
        (
            "README.weights.md",
            text_bytes(
                """
                # Weight Layout

                The checkpoint is intentionally sharded into valid safetensors files so
                local LFS upload, download, and tree views can exercise a few hundred
                megabytes of realistic model payloads.
                """
            ),
        ),
        seed_file(
            "model.safetensors.index.json",
            lambda: model_bundle()["model.safetensors.index.json"],
        ),
        seed_file(
            "model-00001-of-00003.safetensors",
            lambda: model_bundle()["model-00001-of-00003.safetensors"],
        ),
        seed_file(
            "model-00002-of-00003.safetensors",
            lambda: model_bundle()["model-00002-of-00003.safetensors"],
        ),
        seed_file(
            "model-00003-of-00003.safetensors",
            lambda: model_bundle()["model-00003-of-00003.safetensors"],
        ),
    )

    return (
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="open-media-lab",
            name="multimodal-benchmark-suite",
            private=False,
            commits=(
                CommitSeed(
                    summary="Seed multimodal benchmark suite",
                    description=(
                        "Add a real parquet shard, indexed tar archive, and common media "
                        "formats to exercise local dataset browsing and LFS flows."
                    ),
                    files=multimodal_files,
                ),
                CommitSeed(
                    summary="Add archive notes and split manifest",
                    description="Keep the multimodal dataset active with a second commit and metadata refresh.",
                    files=(
                        (
                            "notes/archive-layout.md",
                            text_bytes(
                                """
                                # Archive Layout

                                The indexed tar bundle mirrors the hfutils.index layout so local
                                demos can inspect offsets, file sizes, and per-member checksums.
                                """
                            ),
                        ),
                        (
                            "metadata/splits.json",
                            json_bytes(
                                {
                                    "train": "parquet/train-00000-of-00001.parquet",
                                    "validation": "parquet/validation-00000-of-00001.parquet",
                                }
                            ),
                        ),
                    ),
                ),
            ),
            branch="curation-pass",
            tag="v2026.04-media",
            download_path="parquet/train-00000-of-00001.parquet",
            download_sessions=6,
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="open-media-lab",
            name="vision-language-assistant-3b",
            private=False,
            commits=(
                CommitSeed(
                    summary="Publish sharded multimodal assistant checkpoint",
                    description=(
                        "Add common Hugging Face model files and a few hundred megabytes "
                        "of sharded safetensors weights."
                    ),
                    files=model_files,
                ),
                CommitSeed(
                    summary="Add eval cards and prompt notes",
                    description="Follow-up commit for model history, metadata, and release-note views.",
                    files=(
                        (
                            "eval/benchmark.json",
                            json_bytes(
                                {
                                    "chart_qa_em": 0.71,
                                    "docvqa_anls": 0.63,
                                    "latency_ms_p95": 186,
                                }
                            ),
                        ),
                        (
                            "prompts/system.md",
                            text_bytes(
                                """
                                # System Prompt Notes

                                - prefer grounded answers over speculative OCR recovery
                                - preserve visible numbers and units
                                - mention image regions when ambiguity remains
                                """
                            ),
                        ),
                    ),
                ),
            ),
            branch="eval-refresh",
            tag="v0.9.0-local",
            download_path="model-00001-of-00003.safetensors",
            download_sessions=4,
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="open-media-lab",
            name="hierarchy-crawl-fixtures",
            private=False,
            commits=(
                CommitSeed(
                    summary="Seed deeply nested tree fixtures",
                    description=(
                        "Generate a repo with many files and several levels of nested paths "
                        "for tree navigation and search coverage."
                    ),
                    files=make_deep_tree_files("hierarchy-crawl"),
                ),
                CommitSeed(
                    summary="Add tree smoke-test notes",
                    description="Keep one extra commit so history and diff views remain non-trivial.",
                    files=(
                        (
                            "notes/path-review.md",
                            text_bytes(
                                """
                                # Path Review

                                This repo exists to keep large tree browsing reproducible. When a
                                pagination or sorting bug appears, use these fixtures first.
                                """
                            ),
                        ),
                    ),
                ),
            ),
            branch="path-review",
            tag="tree-fixtures-2026-04",
            download_path=(
                "catalog/section-06/tier-08/branch-06/node-06-08-06/"
                "entry-06-08-06.json"
            ),
            download_sessions=2,
        ),
    )


def build_open_media_showcase_repo_seeds() -> tuple[RepoSeed, ...]:
    specs = (
        ("model", "dock-caption-lite", False, "dock captioning smoke-test model"),
        ("dataset", "quay-ops-snippets", False, "operations dataset for list and preview checks"),
        ("space", "repo-browser-demo", False, "space used to pin org landing content"),
        ("model", "layout-distill-small", False, "small layout parser release used for org pages"),
        ("dataset", "table-scan-fixtures", False, "table extraction fixtures for repeated browsing"),
        ("space", "taxonomy-review-room", True, "private review board for annotation changes"),
        ("model", "invoice-embeddings-small", False, "embedding checkpoint metadata fixture"),
        ("dataset", "ui-search-fixtures", False, "search and pagination samples"),
        ("space", "annotation-hotfix-board", True, "private space for triage workflows"),
        ("model", "signal-router-mini", False, "tiny routing model used in showcase cards"),
    )

    repos: list[RepoSeed] = []
    for repo_type, name, private, summary in specs:
        readme = text_bytes(
            f"""
            # {name}

            {summary.capitalize()}.
            This repository exists to give open-media-lab a realistic repo count in local dev.
            """
        )

        if repo_type == "model":
            files: tuple[SeedFile, ...] = (
                ("README.md", readme),
                (
                    "config.json",
                    json_bytes(
                        {
                            "hidden_size": 768,
                            "model_type": name,
                            "num_hidden_layers": 12,
                        }
                    ),
                ),
                seed_file(
                    f"weights/{name}.safetensors",
                    lambda name=name: make_single_checkpoint_bytes(
                        name,
                        (
                            ("model.embed_tokens.weight", (2048, 1024)),
                            ("model.layers.0.mlp.up_proj.weight", (1024, 512)),
                        ),
                    ),
                ),
            )
            download_path = f"weights/{name}.safetensors"
        elif repo_type == "dataset":
            files = (
                ("README.md", readme),
                (
                    "data/rows.jsonl",
                    jsonl_bytes(
                        (
                            {"id": f"{name}-0001", "label": "alpha"},
                            {"id": f"{name}-0002", "label": "beta"},
                        )
                    ),
                ),
                (
                    "metadata/features.json",
                    json_bytes({"id": "string", "label": "string"}),
                ),
            )
            download_path = "data/rows.jsonl"
        else:
            files = (
                ("README.md", readme),
                (
                    "app.py",
                    text_bytes(
                        f"""
                        import gradio as gr

                        demo = gr.Interface(
                            fn=lambda text: "{name}: " + text.strip(),
                            inputs=gr.Textbox(label="Input"),
                            outputs=gr.Textbox(label="Output"),
                            title="{name}",
                        )

                        if __name__ == "__main__":
                            demo.launch()
                        """
                    ),
                ),
                ("requirements.txt", text_bytes("gradio>=4.44.0")),
            )
            download_path = "README.md"

        repos.append(
            RepoSeed(
                actor="mai_lin",
                repo_type=repo_type,
                namespace="open-media-lab",
                name=name,
                private=private,
                commits=(
                    CommitSeed(
                        summary=f"Seed {name}",
                        description="Create a compact org repo so the listing page has real density.",
                        files=files,
                    ),
                ),
                download_path=download_path,
                download_sessions=1 if not private else 0,
            )
        )

    return tuple(repos)


REPO_SEEDS = (
    build_repo_seeds()
    + build_open_media_core_repo_seeds()
    + build_open_media_showcase_repo_seeds()
)

LIKES: tuple[tuple[str, str, str, str], ...] = (
    ("leo_park", "model", "mai_lin", "lineart-caption-base"),
    ("leo_park", "dataset", "mai_lin", "street-sign-zh-en"),
    ("leo_park", "model", "harbor-vision", "marine-seg-small"),
    ("sara_chen", "model", "mai_lin", "lineart-caption-base"),
    ("sara_chen", "model", "aurora-labs", "aurora-ocr-lite"),
    ("sara_chen", "dataset", "aurora-labs", "receipt-layout-bench"),
    ("noah_kim", "model", "aurora-labs", "aurora-ocr-lite"),
    ("noah_kim", "dataset", "mai_lin", "street-sign-zh-en"),
    ("noah_kim", "space", "leo_park", "formula-checker-lite"),
    ("ivy_ops", "model", "mai_lin", "lineart-caption-base"),
    ("ivy_ops", "model", "aurora-labs", "aurora-ocr-lite"),
    ("ivy_ops", "dataset", "sara_chen", "invoice-entities-mini"),
    ("mai_lin", "model", "harbor-vision", "marine-seg-small"),
    ("mai_lin", "space", "leo_park", "formula-checker-lite"),
    ("mai_lin", "dataset", "aurora-labs", "receipt-layout-bench"),
)


def account_index() -> dict[str, AccountSeed]:
    return {account.username: account for account in ACCOUNTS}


def repo_slug(repo: RepoSeed) -> str:
    return f"{repo.repo_type}-{repo.namespace}-{repo.name}".replace("/", "-")


def make_avatar_bytes(label: str, background: str, accent: str) -> bytes:
    image = Image.new("RGB", (512, 512), background)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((48, 48, 464, 464), radius=96, outline=accent, width=16)
    draw.ellipse((120, 120, 392, 392), fill=accent)

    initials = "".join(part[0].upper() for part in label.replace("-", " ").split()[:2])
    font = ImageFont.load_default()
    text_box = draw.textbbox((0, 0), initials, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    draw.text(
        ((512 - text_width) / 2, (512 - text_height) / 2),
        initials,
        fill=background,
        font=font,
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def describe_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = response.text
    return f"HTTP {response.status_code}: {payload}"


async def ensure_response(
    response: httpx.Response,
    action: str,
    allowed_statuses: tuple[int, ...] = (200,),
) -> httpx.Response:
    if response.status_code not in allowed_statuses:
        raise SeedError(f"{action} failed with {describe_error(response)}")
    return response


def url_to_internal_path(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def manifest_matches_current_seed() -> bool:
    if not MANIFEST_PATH.exists():
        return False

    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False

    return payload.get("seed_version") == SEED_VERSION


def representative_seed_repositories() -> tuple[RepoSeed, ...]:
    seen_types: set[str] = set()
    selected: list[RepoSeed] = []

    for repo in REPO_SEEDS:
        if repo.private or repo.repo_type in seen_types:
            continue
        seen_types.add(repo.repo_type)
        selected.append(repo)

    return tuple(selected)


async def detect_seed_state(client: httpx.AsyncClient) -> str:
    response = await client.get(
        f"/api/users/{PRIMARY_USERNAME}/type",
        params={"fallback": "false"},
    )
    if response.status_code == 404:
        return "missing"
    await ensure_response(response, f"check existing seed for {PRIMARY_USERNAME}")

    if not manifest_matches_current_seed():
        return "incomplete"

    for repo in representative_seed_repositories():
        info_response = await client.get(f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}")
        if info_response.status_code == 404:
            return "incomplete"
        await ensure_response(
            info_response,
            f"verify seeded repo metadata for {repo.namespace}/{repo.name}",
        )

        tree_response = await client.get(
            f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}/tree/main"
        )
        if tree_response.status_code == 404:
            return "incomplete"
        await ensure_response(
            tree_response,
            f"verify seeded repo storage for {repo.namespace}/{repo.name}",
        )

    return "ready"


async def register_account(client: httpx.AsyncClient, account: AccountSeed) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "username": account.username,
            "email": account.email,
            "password": DEFAULT_PASSWORD,
        },
    )
    if response.status_code == 200:
        return

    if response.status_code == 400:
        message = str(response.json())
        if "exists" in message or "conflicts" in message:
            return

    raise SeedError(f"register {account.username} failed with {describe_error(response)}")


async def login_account(client: httpx.AsyncClient, account: AccountSeed) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": account.username, "password": DEFAULT_PASSWORD},
    )
    await ensure_response(response, f"login {account.username}")

    if "session_id" not in client.cookies:
        raise SeedError(f"login {account.username} did not set a session cookie")


async def upload_avatar(
    client: httpx.AsyncClient,
    path: str,
    label: str,
    background: str,
    accent: str,
) -> None:
    response = await client.post(
        path,
        files={
            "file": (
                f"{label}.png",
                make_avatar_bytes(label, background, accent),
                "image/png",
            )
        },
    )
    await ensure_response(response, f"upload avatar for {label}")


async def configure_user_profile(client: httpx.AsyncClient, account: AccountSeed) -> None:
    response = await client.put(
        f"/api/users/{account.username}/settings",
        json={
            "email": account.email,
            "full_name": account.full_name,
            "bio": account.bio,
            "website": account.website,
            "social_media": account.social_media,
        },
    )
    await ensure_response(response, f"update user settings for {account.username}")
    await upload_avatar(
        client,
        f"/api/users/{account.username}/avatar",
        account.username,
        account.avatar_bg,
        account.avatar_accent,
    )


async def create_organization(
    client: httpx.AsyncClient, organization: OrganizationSeed
) -> None:
    response = await client.post(
        "/org/create",
        json={
            "name": organization.name,
            "description": organization.description,
        },
    )
    if response.status_code == 200:
        return

    if response.status_code == 400 and "already exists" in str(response.json()):
        return

    raise SeedError(
        f"create organization {organization.name} failed with {describe_error(response)}"
    )


async def ensure_org_member(
    client: httpx.AsyncClient,
    org_name: str,
    username: str,
    role: str,
) -> None:
    response = await client.post(
        f"/org/{org_name}/members",
        json={"username": username, "role": role},
    )
    if response.status_code not in (200, 400):
        raise SeedError(
            f"add {username} to {org_name} failed with {describe_error(response)}"
        )

    # PUT keeps roles deterministic even if the member already existed.
    response = await client.put(
        f"/org/{org_name}/members/{username}",
        json={"role": role},
    )
    await ensure_response(response, f"set role for {username} in {org_name}")


async def configure_organization(
    client: httpx.AsyncClient, organization: OrganizationSeed
) -> None:
    response = await client.put(
        f"/api/organizations/{organization.name}/settings",
        json={
            "description": organization.description,
            "bio": organization.bio,
            "website": organization.website,
            "social_media": organization.social_media,
        },
    )
    await ensure_response(response, f"update organization settings for {organization.name}")
    await upload_avatar(
        client,
        f"/api/organizations/{organization.name}/avatar",
        organization.name,
        organization.avatar_bg,
        organization.avatar_accent,
    )


async def create_repo(client: httpx.AsyncClient, repo: RepoSeed) -> None:
    payload = {
        "type": repo.repo_type,
        "name": repo.name,
        "private": repo.private,
    }
    if repo.namespace != repo.actor:
        payload["organization"] = repo.namespace

    response = await client.post("/api/repos/create", json=payload)
    if response.status_code == 200:
        return

    if response.status_code == 400 and "already exists" in str(response.json()):
        return

    raise SeedError(f"create repo {repo.namespace}/{repo.name} failed with {describe_error(response)}")


async def upload_lfs_object(
    client: httpx.AsyncClient,
    repo: RepoSeed,
    content: bytes,
) -> tuple[str, int]:
    oid = hashlib.sha256(content).hexdigest()
    size = len(content)

    response = await client.post(
        f"/{repo.repo_type}s/{repo.namespace}/{repo.name}.git/info/lfs/objects/batch",
        json={
            "operation": "upload",
            "transfers": ["basic"],
            "objects": [{"oid": oid, "size": size}],
            "hash_algo": "sha256",
            # Local dev uses the frontend base_url publicly, so the seed script rewrites
            # verify URLs back onto the in-process backend transport.
            "is_browser": True,
        },
    )
    await ensure_response(response, f"prepare LFS upload for {repo.namespace}/{repo.name}")

    batch_data = response.json()
    obj = batch_data["objects"][0]
    if obj.get("error"):
        raise SeedError(f"LFS batch returned an error for {repo.namespace}/{repo.name}: {obj['error']}")

    upload_action = (obj.get("actions") or {}).get("upload")
    if upload_action:
        upload_headers = upload_action.get("header") or {}
        async with httpx.AsyncClient(follow_redirects=False, timeout=60.0) as network_client:
            upload_response = await network_client.put(
                upload_action["href"],
                content=content,
                headers=upload_headers,
            )

        if upload_response.status_code not in (200, 201):
            raise SeedError(
                f"LFS upload failed for {repo.namespace}/{repo.name}: "
                f"HTTP {upload_response.status_code} {upload_response.text}"
            )

        verify_action = (obj.get("actions") or {}).get("verify")
        if verify_action:
            verify_response = await client.post(
                url_to_internal_path(verify_action["href"]),
                json={"oid": oid, "size": size},
            )
            await ensure_response(
                verify_response,
                f"verify LFS upload for {repo.namespace}/{repo.name}",
            )

    return oid, size


async def commit_files(
    client: httpx.AsyncClient,
    repo: RepoSeed,
    commit: CommitSeed,
) -> None:
    materialized_files = [materialize_seed_file(file_entry) for file_entry in commit.files]
    metadata = []

    for path, content in materialized_files:
        sha256 = hashlib.sha256(content).hexdigest()
        metadata.append(
            {
                "path": path,
                "size": len(content),
                "sha256": sha256,
            }
        )

    preupload_response = await client.post(
        f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}/preupload/main",
        json={"files": metadata},
    )
    await ensure_response(
        preupload_response,
        f"preupload {repo.namespace}/{repo.name}",
    )
    preupload_results = {
        item["path"]: item for item in preupload_response.json().get("files", [])
    }

    ndjson_lines = [
        {
            "key": "header",
            "value": {
                "summary": commit.summary,
                "description": commit.description,
            },
        }
    ]

    for path, content in materialized_files:
        mode = preupload_results[path]["uploadMode"]

        if preupload_results[path]["shouldIgnore"]:
            continue

        if mode == "lfs":
            oid, size = await upload_lfs_object(client, repo, content)
            ndjson_lines.append(
                {
                    "key": "lfsFile",
                    "value": {
                        "path": path,
                        "oid": oid,
                        "size": size,
                        "algo": "sha256",
                    },
                }
            )
            continue

        ndjson_lines.append(
            {
                "key": "file",
                "value": {
                    "path": path,
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
            }
        )

    ndjson_payload = "\n".join(json.dumps(line, sort_keys=True) for line in ndjson_lines)
    response = await client.post(
        f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}/commit/main",
        content=ndjson_payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    await ensure_response(response, f"commit {repo.namespace}/{repo.name}")


async def create_branch(client: httpx.AsyncClient, repo: RepoSeed) -> None:
    if not repo.branch:
        return

    response = await client.post(
        f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}/branch",
        json={"branch": repo.branch, "revision": "main"},
    )
    if response.status_code == 200:
        return

    if response.status_code in (400, 409) and "already exists" in str(response.json()):
        return

    raise SeedError(
        f"create branch {repo.branch} for {repo.namespace}/{repo.name} failed with "
        f"{describe_error(response)}"
    )


async def create_tag(client: httpx.AsyncClient, repo: RepoSeed) -> None:
    if not repo.tag:
        return

    response = await client.post(
        f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}/tag",
        json={"tag": repo.tag, "revision": "main"},
    )
    if response.status_code == 200:
        return

    if response.status_code in (400, 409) and "already exists" in str(response.json()):
        return

    raise SeedError(
        f"create tag {repo.tag} for {repo.namespace}/{repo.name} failed with "
        f"{describe_error(response)}"
    )


async def like_repo(
    client: httpx.AsyncClient,
    repo_type: str,
    namespace: str,
    name: str,
) -> None:
    response = await client.post(f"/api/{repo_type}s/{namespace}/{name}/like")
    if response.status_code == 200:
        return

    if response.status_code == 400 and "already liked" in str(response.json()):
        return

    raise SeedError(
        f"like {repo_type}/{namespace}/{name} failed with {describe_error(response)}"
    )


async def trigger_download(
    client: httpx.AsyncClient,
    repo: RepoSeed,
    path: str,
    *,
    cookies: dict[str, str] | None = None,
) -> None:
    response = await client.get(
        f"/api/{repo.repo_type}s/{repo.namespace}/{repo.name}/resolve/main/{path}",
        cookies=cookies,
    )
    if response.status_code not in (302, 307):
        raise SeedError(
            f"download seed for {repo.namespace}/{repo.name}:{path} failed with "
            f"{describe_error(response)}"
        )


def build_manifest() -> dict:
    return {
        "seed_version": SEED_VERSION,
        "manifest_path": str(MANIFEST_PATH),
        "main_ui_url": cfg.app.base_url,
        "backend_url": INTERNAL_BASE_URL,
        "main_login": {
            "username": PRIMARY_USERNAME,
            "password": DEFAULT_PASSWORD,
        },
        "additional_users": [
            {
                "username": account.username,
                "password": DEFAULT_PASSWORD,
                "email": account.email,
            }
            for account in ACCOUNTS
            if account.username != PRIMARY_USERNAME
        ],
        "admin_ui": {
            "url": "http://127.0.0.1:5174",
            "token": cfg.admin.secret_token,
        },
        "organizations": [
            {
                "name": organization.name,
                "members": [
                    {"username": username, "role": role}
                    for username, role in organization.members
                ],
            }
            for organization in ORGANIZATIONS
        ],
        "repositories": [
            {
                "type": repo.repo_type,
                "namespace": repo.namespace,
                "name": repo.name,
                "private": repo.private,
            }
            for repo in REPO_SEEDS
        ],
    }


def write_manifest() -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def print_summary(seed_applied: bool) -> None:
    state = "Seeded" if seed_applied else "Seed already present"
    print(f"{state}: {SEED_VERSION}")
    print(f"Manifest: {MANIFEST_PATH}")
    print(f"Main UI: {cfg.app.base_url}")
    print(f"Backend: {INTERNAL_BASE_URL}")
    print(f"Login: {PRIMARY_USERNAME} / {DEFAULT_PASSWORD}")
    print(f"Admin UI token: {cfg.admin.secret_token}")


async def seed_demo_data() -> None:
    init_storage()
    transport = httpx.ASGITransport(app=app)
    accounts_by_name = account_index()

    async with AsyncExitStack() as stack:
        seed_client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=transport,
                base_url=INTERNAL_BASE_URL,
                follow_redirects=False,
            )
        )

        seed_state = await detect_seed_state(seed_client)
        if seed_state == "ready":
            write_manifest()
            print_summary(seed_applied=False)
            return
        if seed_state == "incomplete":
            raise SeedError(
                "Local demo seed is only partially present. "
                "Run `make reset-local-data` and then retry `make seed-demo`."
            )

        for account in ACCOUNTS:
            await register_account(seed_client, account)

        authed_clients: dict[str, httpx.AsyncClient] = {}
        for account in ACCOUNTS:
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=transport,
                    base_url=INTERNAL_BASE_URL,
                    follow_redirects=False,
                )
            )
            await login_account(client, account)
            await configure_user_profile(client, account)
            authed_clients[account.username] = client

        primary_client = authed_clients[PRIMARY_USERNAME]
        for organization in ORGANIZATIONS:
            await create_organization(primary_client, organization)
            for username, role in organization.members:
                if username == PRIMARY_USERNAME:
                    continue
                await ensure_org_member(primary_client, organization.name, username, role)
            await configure_organization(primary_client, organization)

        for repo in REPO_SEEDS:
            repo_client = authed_clients[repo.actor]
            await create_repo(repo_client, repo)
            for commit in repo.commits:
                await commit_files(repo_client, repo, commit)
            await create_branch(repo_client, repo)
            await create_tag(repo_client, repo)

        for liker, repo_type, namespace, name in LIKES:
            await like_repo(authed_clients[liker], repo_type, namespace, name)

        anon_client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=transport,
                base_url=INTERNAL_BASE_URL,
                follow_redirects=False,
            )
        )

        for repo in REPO_SEEDS:
            if not repo.download_path:
                continue

            if repo.private:
                await trigger_download(
                    authed_clients[PRIMARY_USERNAME],
                    repo,
                    repo.download_path,
                )
                continue

            for session_number in range(repo.download_sessions):
                await trigger_download(
                    anon_client,
                    repo,
                    repo.download_path,
                    cookies={
                        "hf_download_session": f"seed-{repo_slug(repo)}-{session_number:02d}"
                    },
                )

        # Download tracking happens in background tasks off the API response path.
        await asyncio.sleep(0.5)

    write_manifest()
    print_summary(seed_applied=True)


def main() -> int:
    try:
        asyncio.run(seed_demo_data())
    except SeedError as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
