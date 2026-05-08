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
from seed_shared import SEED_VERSION

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kohakuhub.config import cfg
from kohakuhub.main import app
from kohakuhub.utils.s3 import init_storage

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
class DeletedFileSeed:
    """Mark a path for deletion in a commit.

    Emits a `deletedFile` NDJSON op against the commit endpoint. Used by
    fixtures that want to exercise repos with churn (add/delete/restore
    cycles) instead of monotonically-growing histories.
    """

    path: str


@dataclass(frozen=True)
class DeletedFolderSeed:
    """Mark a folder (recursive) for deletion in a commit.

    Emits a `deletedFolder` NDJSON op. Path is the folder prefix without
    a trailing slash; the commit endpoint normalises it. Used to stress
    repos with prefix-level changes that touch many descendants at once.
    """

    path: str


@dataclass(frozen=True)
class CopyFileSeed:
    """Copy a file from one path to another within the repo.

    Emits a `copyFile` NDJSON op with `srcPath` / `srcRevision`. The
    source path must exist on `srcRevision` (defaulting to `main`) at
    commit time, otherwise LakeFS rejects the link.
    """

    dest_path: str
    src_path: str
    src_revision: str = "main"


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


SeedFile = (
    tuple[str, bytes] | FileSeed | DeletedFileSeed | DeletedFolderSeed | CopyFileSeed
)


@dataclass(frozen=True)
class RemoteAsset:
    cache_name: str
    url: str
    sha256: str
    source_url: str


@dataclass(frozen=True)
class SeedKeypair:
    """A real ed25519 keypair shipped with the seed.

    The private half is included so manual local testing (e.g. `git push`
    over SSH) can sign with the matching key without having to generate
    one. These are explicitly *not* production credentials — they only
    ever live in the dev seed and the test baseline.
    """

    public_key: str
    private_key: str
    fingerprint: str


@dataclass(frozen=True)
class SeedSshKeyPlant:
    user: str
    title: str
    keypair: SeedKeypair
    last_used_days_ago: int | None  # None = never used


@dataclass(frozen=True)
class SeedTokenPlant:
    user: str
    name: str
    plaintext: str
    last_used_days_ago: int | None  # None = never used


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


# PR #77 strict-consistency demo orgs. Each one's name is identical to a
# real HuggingFace org/user, so any local repo created under it
# collides on path with HF and exercises the namespace-priority +
# X-Error-Code gating contracts. mai_lin owns each as super-admin to
# keep the management surface simple — see ``build_pr77_demo_repo_seeds``
# for the corresponding repos.
PR77_DEMO_ORGS_MEMBERS: tuple[tuple[str, str], ...] = (
    ("mai_lin", "super-admin"),
)
ORGANIZATIONS = ORGANIZATIONS + (
    OrganizationSeed(
        name="openai-community",
        description=(
            "PR #77 demo: locally collides with the real HuggingFace org of "
            "the same name. Houses a structurally-different gpt2 repo to "
            "exercise the namespace-priority + EntryNotFound short-circuit."
        ),
        bio=(
            "Local-only demo org. Browse the gpt2 repo to see the README "
            "explaining the strict-consistency contract this PR locks down."
        ),
        website="",
        social_media={},
        avatar_bg="#1f2937",
        avatar_accent="#fbbf24",
        members=PR77_DEMO_ORGS_MEMBERS,
    ),
    OrganizationSeed(
        name="bigscience",
        description=(
            "PR #77 demo: locally collides with the HF bigscience org. "
            "Houses a tiny bloom repo (sister case to openai-community/gpt2)."
        ),
        bio=(
            "Local-only demo org for namespace-collision tests. The bloom "
            "repo here is README + a small text note — enough to prove the "
            "rule isn't tied to one specific repo path."
        ),
        website="",
        social_media={},
        avatar_bg="#7c2d12",
        avatar_accent="#fed7aa",
        members=PR77_DEMO_ORGS_MEMBERS,
    ),
    OrganizationSeed(
        name="meta-llama",
        description=(
            "PR #77 demo: locally collides with the HF meta-llama org. "
            "Llama-2-7b on HF is GATED; the local-namespace-ownership "
            "contract holds even against gated upstreams."
        ),
        bio=(
            "Local-only demo org. The Llama-2-7b repo here demonstrates "
            "that strict consistency holds even when the same path on HF "
            "is gated — the gated 401 never reaches the client when local "
            "owns the namespace."
        ),
        website="",
        social_media={},
        avatar_bg="#312e81",
        avatar_accent="#a5b4fc",
        members=PR77_DEMO_ORGS_MEMBERS,
    ),
    OrganizationSeed(
        name="narugo1992-pr77-demo",
        description=(
            "PR #77 demo: pure-fallback path. Repos referenced from the "
            "`guide` README don't exist locally; this org just hosts the "
            "guide itself."
        ),
        bio=(
            "Pure-fallback walk-through: this README documents how the "
            "fallback chain serves HF-only repos (success, 404, gated) "
            "when local has no collision."
        ),
        website="",
        social_media={},
        avatar_bg="#0f766e",
        avatar_accent="#ccfbf1",
        members=PR77_DEMO_ORGS_MEMBERS,
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

# Real Danbooru fetches that back the indexed-tar showcase repo. Four
# Arknights posts at each of the four Danbooru ratings (g/s/q/e per the
# ratings wiki), plus eight mixed-IP posts spanning the same rating
# spectrum so the gallery exercises rating diversity inside one tar.
DANBOORU_INDEXED_TAR_ASSETS: tuple[RemoteAsset, ...] = (
    RemoteAsset(
        cache_name="danbooru-arknights-g-4670495.jpg",
        url="https://cdn.donmai.us/original/ec/3c/ec3c916ed93030b519ddac467e9cf1ca.jpg",
        sha256="b5af1d9cf48974acdee99340ad5ad60e428e4f27f6c5fb96eae8468a44af96b8",
        source_url="https://danbooru.donmai.us/posts/4670495",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-g-5466880.jpg",
        url="https://cdn.donmai.us/original/b3/a9/b3a9190677c39031048cd271795696de.jpg",
        sha256="8c4d116cb1e0a19974c3fe3539b9e55c2ff2e7aca0c569a7845f522ac25b1d24",
        source_url="https://danbooru.donmai.us/posts/5466880",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-g-9106605.jpg",
        url="https://cdn.donmai.us/original/c1/bb/c1bb22e771347c4467f43725f2ae62c4.jpg",
        sha256="4aa76923de04b8a976fe77e89e3880e0019571d5da16952b98765285cba773f1",
        source_url="https://danbooru.donmai.us/posts/9106605",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-g-9457445.jpg",
        url="https://cdn.donmai.us/original/cd/a4/cda466c2d71c3e252e34b73377ace0e1.jpg",
        sha256="4b8dd816cbb6d611e696817b4a18025755f18d8abed83e2bfe697cf13280f138",
        source_url="https://danbooru.donmai.us/posts/9457445",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-s-7576297.jpg",
        url="https://cdn.donmai.us/original/87/b7/87b7ab2aa407403a86761bbcceab1a12.jpg",
        sha256="91b636a5974629b889a007ad91f0413d3fa5b4873df9c26dce00a518edaeb918",
        source_url="https://danbooru.donmai.us/posts/7576297",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-s-8318664.jpg",
        url="https://cdn.donmai.us/original/06/ef/06ef3487c9cb6d3391eef61cdaf5c1f9.jpg",
        sha256="cdf065647dac294d9981c9c99cec04d7761ea41fd7fceb41708c082855f8b6f1",
        source_url="https://danbooru.donmai.us/posts/8318664",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-s-8422542.jpg",
        url="https://cdn.donmai.us/original/71/3c/713cbd309fff56c0d1203ff92531dd70.jpg",
        sha256="a41c4a62761aea3e73dd662afee302c13eff7b8c680c2a14e75107af6adc9811",
        source_url="https://danbooru.donmai.us/posts/8422542",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-s-9280691.jpg",
        url="https://cdn.donmai.us/original/b8/8a/b88a38f12180704ffcc97cddac338ae1.jpg",
        sha256="b4da597358b5a9930d1b893e7388fc9366ba527bd681f58c51af0b231aecf023",
        source_url="https://danbooru.donmai.us/posts/9280691",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-q-3850730.jpg",
        url="https://cdn.donmai.us/original/3e/ce/3eced4d3bbfaa70b2fccfdc9d64e81ee.jpg",
        sha256="c4f246b6916dfe2b9ed7fdce50523b4e5e4627144125e9da525a0f8f137c5694",
        source_url="https://danbooru.donmai.us/posts/3850730",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-q-3898266.jpg",
        url="https://cdn.donmai.us/original/c5/c0/c5c04d5831e27533569ddd0cb105a6dd.jpg",
        sha256="8a1c89f96506c70ebc5f413c591724ce61651952c0c6409054d77facf60cb7da",
        source_url="https://danbooru.donmai.us/posts/3898266",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-q-5296250.jpg",
        url="https://cdn.donmai.us/original/dc/bf/dcbf73bca941b7f9e2ac88dbbb1f8897.jpg",
        sha256="403615e52d908da36fc3e196e66112fca7abc9924d14d41d418729d15ae986b1",
        source_url="https://danbooru.donmai.us/posts/5296250",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-q-6495120.jpg",
        url="https://cdn.donmai.us/original/9b/b3/9bb33d31cc5cc744786aaa3892bc7da7.jpg",
        sha256="caabedf2c2f6a2641285796f4e4012b0663e08ffccc8ee8d3236c34162dd882c",
        source_url="https://danbooru.donmai.us/posts/6495120",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-e-3856111.jpg",
        url="https://cdn.donmai.us/original/ec/5e/ec5e0c5a84f81309713290499d4a2965.jpg",
        sha256="b7fb84d6629d1793ffa39da807e0b64ac1ad760ae21b5d31f70aae452d7c8538",
        source_url="https://danbooru.donmai.us/posts/3856111",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-e-4151927.jpg",
        url="https://cdn.donmai.us/original/d7/d3/d7d3f29d336015c910e856861d205bb3.jpg",
        sha256="9cc422fcb224a682c9165204966a450b031e98b6df2e22459050eb39e8e6b760",
        source_url="https://danbooru.donmai.us/posts/4151927",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-e-6143658.jpg",
        url="https://cdn.donmai.us/original/2a/3c/2a3ca65af1528c38022e60f145354c4b.jpg",
        sha256="ad1e41e90e2590002fbec384a2ae5327dd0a70b118bfefa3af3bee69acbf5fd7",
        source_url="https://danbooru.donmai.us/posts/6143658",
    ),
    RemoteAsset(
        cache_name="danbooru-arknights-e-10784078.jpg",
        url="https://cdn.donmai.us/original/3b/cb/3bcbce16bc507306821abbe85269127b.jpg",
        sha256="9b720042fd63c1e5558c939f8eb065fe0ae6e1769d0f41c5af5427ff2fe95d64",
        source_url="https://danbooru.donmai.us/posts/10784078",
    ),
    RemoteAsset(
        cache_name="danbooru-genshin-impact-g-7293585.png",
        url="https://cdn.donmai.us/original/01/8f/018f785ee3789953094e0a4feb65ed27.png",
        sha256="e499bb38d915394641452349ad310b70b2063fcdc03c9b783aa0b40ed8f3a9ce",
        source_url="https://danbooru.donmai.us/posts/7293585",
    ),
    RemoteAsset(
        cache_name="danbooru-genshin-impact-g-8524789.jpg",
        url="https://cdn.donmai.us/original/b8/92/b892a8b367606b16b57d6e3305208f14.jpg",
        sha256="73af216e63dd8470edaf6e30b7a1a3f3b317fd4cd8509709204c916d1a02425e",
        source_url="https://danbooru.donmai.us/posts/8524789",
    ),
    RemoteAsset(
        cache_name="danbooru-blue-archive-s-8990007.jpg",
        url="https://cdn.donmai.us/original/7e/05/7e05e05f71749f9c5a8d775364fb0668.jpg",
        sha256="0935cfdbd902142a8f97453ebf8b20cdee9a8426cfed324969461f7ce9e55325",
        source_url="https://danbooru.donmai.us/posts/8990007",
    ),
    RemoteAsset(
        cache_name="danbooru-blue-archive-s-9286565.jpg",
        url="https://cdn.donmai.us/original/13/f8/13f8f67b5c1a37ad50523b698f82c252.jpg",
        sha256="57544143adee46fd0a5151225b7982267ccd4af21c73fa0a7ca55b77fb939af6",
        source_url="https://danbooru.donmai.us/posts/9286565",
    ),
    RemoteAsset(
        cache_name="danbooru-hololive-q-3648775.png",
        url="https://cdn.donmai.us/original/72/ea/72ea404ecf8aa2c0a8414d612e3e30f1.png",
        sha256="251ddab56bbe3f05b7c794b84e16e59923a8a43a95efdeba436667794e61aa07",
        source_url="https://danbooru.donmai.us/posts/3648775",
    ),
    RemoteAsset(
        cache_name="danbooru-hololive-q-6336205.jpg",
        url="https://cdn.donmai.us/original/a0/c8/a0c844e0f8e49754cedd7affd8d0a6c2.jpg",
        sha256="abcdb17e3f02908dc6bb593e138d45aca50870190742bb54fbd2e5e8a26d9071",
        source_url="https://danbooru.donmai.us/posts/6336205",
    ),
    RemoteAsset(
        cache_name="danbooru-original-e-11239919.jpg",
        url="https://cdn.donmai.us/original/db/75/db75c5b50d1a98a2d7a1505e0140fc14.jpg",
        sha256="8b5d109e07a8fc722f0086c58382bd54cd3b3c864cea6d97abc8be4c6b193693",
        source_url="https://danbooru.donmai.us/posts/11239919",
    ),
    RemoteAsset(
        cache_name="danbooru-original-e-11240082.jpg",
        url="https://cdn.donmai.us/original/0a/25/0a259c798419e77e7d7b06d641985f0c.jpg",
        sha256="fd8b769867dcd4117cb6a8af392322ffd1d391380891ac9c98f0998c487b5b81",
        source_url="https://danbooru.donmai.us/posts/11240082",
    ),
)


REMOTE_MEDIA_ASSETS: dict[str, RemoteAsset] = {
    asset.cache_name: asset
    for asset in (
        *SAFEBOORU_IMAGE_ASSETS,
        *DANBOORU_INDEXED_TAR_ASSETS,
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
        # Real HF-hosted fixtures used to exercise the pure-client preview
        # path (issue #27). Both files are small (~500 KB each), pinned by
        # sha256, and sourced from long-stable public HF test artifacts so
        # the seed stays deterministic across runs.
        RemoteAsset(
            cache_name="hf-tiny-random-bert.safetensors",
            url="https://huggingface.co/hf-internal-testing/tiny-random-bert/resolve/main/model.safetensors",
            sha256="965f02b6a7e5520fc12f710e4e3b6132f697f1c8f648819553c5ade86752d2de",
            source_url="https://huggingface.co/hf-internal-testing/tiny-random-bert/blob/main/model.safetensors",
        ),
        RemoteAsset(
            cache_name="hf-no-robots-test.parquet",
            url="https://huggingface.co/datasets/HuggingFaceH4/no_robots/resolve/main/data/test-00000-of-00001.parquet",
            sha256="60707b2636a46e37bb0c1e9ca263a18553f430317b7a53c691676d6a492fc0f2",
            source_url="https://huggingface.co/datasets/HuggingFaceH4/no_robots/blob/main/data/test-00000-of-00001.parquet",
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


def make_sine_wav_bytes(
    label: str,
    *,
    duration_seconds: float = 0.4,
    sample_rate: int = 8000,
) -> bytes:
    """Tiny mono PCM WAV with a deterministic sine pitch derived from the
    label. Avoids pulling in scipy/wave round-trips — the RIFF header is
    written by hand."""
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    base_freq = 220.0 + (digest[0] % 64) * 4
    sample_count = int(duration_seconds * sample_rate)
    t = np.arange(sample_count, dtype=np.float32) / sample_rate
    samples = (np.sin(2 * np.pi * base_freq * t) * 0.4 * 32767).astype(np.int16)

    pcm = samples.tobytes()
    byte_rate = sample_rate * 2
    block_align = 2
    riff = b"RIFF"
    chunk_size = 36 + len(pcm)
    fmt_chunk = (
        b"fmt \x10\x00\x00\x00"
        + b"\x01\x00"  # PCM
        + b"\x01\x00"  # mono
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + b"\x10\x00"  # 16-bit
    )
    data_chunk = b"data" + len(pcm).to_bytes(4, "little") + pcm
    return riff + chunk_size.to_bytes(4, "little") + b"WAVE" + fmt_chunk + data_chunk


def make_indexed_tar_with_overrides(
    label: str,
    files: tuple[tuple[str, bytes], ...],
    *,
    overrides: dict | None = None,
) -> tuple[bytes, bytes]:
    """Same as `make_indexed_tar_bundle`, but post-processes the index
    JSON before serialization. Used to seed the "stale" (hash_lfs forced
    to a wrong value) and "no-hash" (hash + hash_lfs stripped) showcase
    cases without having to handcraft the JSON shape."""
    tar_bytes, index_bytes = make_indexed_tar_bundle(label, files)
    if overrides is None:
        return tar_bytes, index_bytes

    payload = json.loads(index_bytes.decode("utf-8"))
    for key, value in overrides.items():
        payload[key] = value
    return tar_bytes, json_bytes(payload)


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
        # Real HF-sourced parquet so the pure-client preview (issue #27)
        # can be exercised against a file that actually came off the
        # Hugging Face hub, not just locally generated pyarrow output.
        seed_file(
            "fixtures/hf-no-robots-test.parquet",
            lambda: remote_asset_bytes("hf-no-robots-test.parquet"),
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
        # Real HF-sourced safetensors (tiny-random-bert, ~520 KB) so the
        # pure-client preview (issue #27) can be exercised against a file
        # that actually came off the Hugging Face hub, not just locally
        # generated safetensors.numpy.save output.
        seed_file(
            "fixtures/hf-tiny-random-bert.safetensors",
            lambda: remote_asset_bytes("hf-tiny-random-bert.safetensors"),
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
        # Private mirror of the multimodal benchmark — exercises range-read
        # previews (parquet + hfutils.index tar + safetensors) on a repo
        # that only the logged-in owner can resolve. Regression coverage
        # for the 404-on-missing-session bug seen on private datasets in
        # production.
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="mai_lin",
            name="private-range-preview-bench",
            private=True,
            commits=(
                CommitSeed(
                    summary="Seed private range-preview fixtures",
                    description=(
                        "One real parquet shard and one hfutils.index tar pair "
                        "so the SPA's range-read preview paths can be exercised "
                        "against a repo that requires the session cookie."
                    ),
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: cc-by-4.0
                                pretty_name: Private Range-Preview Bench
                                tags:
                                  - parquet
                                  - indexed-tar
                                  - private
                                ---

                                # private-range-preview-bench

                                Private dataset used to verify that the SPA's
                                client-side parquet, indexed-tar, and tar-thumbnail
                                preview paths still resolve when the only thing
                                identifying the user is the same-origin session
                                cookie. Owner-only by design.
                                """
                            ),
                        ),
                        seed_file(
                            "parquet/sample-00000-of-00001.parquet",
                            lambda: make_parquet_bytes(
                                "private-range-preview", row_count=512, payload_size=512
                            ),
                        ),
                        seed_file(
                            "archives/raw-bundle-0000.tar",
                            lambda: archive_bundle()[0],
                        ),
                        seed_file(
                            "archives/raw-bundle-0000.json",
                            lambda: archive_bundle()[1],
                        ),
                    ),
                ),
            ),
            download_path="parquet/sample-00000-of-00001.parquet",
            download_sessions=0,
        ),
        # Private safetensors checkpoint — same regression coverage but
        # for the model side. One shard + index keeps the seed cheap;
        # the standalone-blob safetensors preview still has a real header
        # to parse over a Range read.
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="mai_lin",
            name="private-vision-checkpoint",
            private=True,
            commits=(
                CommitSeed(
                    summary="Seed private safetensors shard",
                    description=(
                        "One sharded safetensors file plus its index manifest, "
                        "private, so the safetensors header preview can be "
                        "exercised against a session-gated /resolve/ path."
                    ),
                    files=(
                        (
                            "README.md",
                            text_bytes(
                                """
                                ---
                                license: apache-2.0
                                library_name: transformers
                                tags:
                                  - private
                                  - safetensors
                                ---

                                # private-vision-checkpoint

                                Private mirror of one shard from the public
                                vision-language-assistant-3b bundle. Exists so
                                the SPA's safetensors header preview can be
                                verified against a private repo where the only
                                identity hint is the session cookie.
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
                    ),
                ),
            ),
            download_path="model-00001-of-00003.safetensors",
            download_sessions=0,
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


def build_indexed_tar_showcase_repo_seeds() -> tuple[RepoSeed, ...]:
    """Showcase repo for the read-only indexed-tar browser.

    Each tar+sidecar pair lives in its own subfolder so the sibling
    detection (file-preview.js → hasIndexSibling) lights the icon
    only on the .tar in that folder. The five subfolders cover, in
    order: rich nested navigation, pagination scale, hash-mismatch
    warning, no-hash notice, and inner safetensors / parquet
    metadata reuse from inside the archive.
    """

    # Per-rating Arknights buckets + mixed-IP supplement so the
    # in-archive listing exercises each Danbooru rating (g/s/q/e) and
    # the breadcrumb navigates a real-looking 24-image gallery.
    arknights_by_rating = {
        "g": (
            "danbooru-arknights-g-4670495.jpg",
            "danbooru-arknights-g-5466880.jpg",
            "danbooru-arknights-g-9106605.jpg",
            "danbooru-arknights-g-9457445.jpg",
        ),
        "s": (
            "danbooru-arknights-s-7576297.jpg",
            "danbooru-arknights-s-8318664.jpg",
            "danbooru-arknights-s-8422542.jpg",
            "danbooru-arknights-s-9280691.jpg",
        ),
        "q": (
            "danbooru-arknights-q-3850730.jpg",
            "danbooru-arknights-q-3898266.jpg",
            "danbooru-arknights-q-5296250.jpg",
            "danbooru-arknights-q-6495120.jpg",
        ),
        "e": (
            "danbooru-arknights-e-3856111.jpg",
            "danbooru-arknights-e-4151927.jpg",
            "danbooru-arknights-e-6143658.jpg",
            "danbooru-arknights-e-10784078.jpg",
        ),
    }
    misc_ip_assets = (
        ("genshin-impact", "g", "danbooru-genshin-impact-g-7293585.png"),
        ("genshin-impact", "g", "danbooru-genshin-impact-g-8524789.jpg"),
        ("blue-archive", "s", "danbooru-blue-archive-s-8990007.jpg"),
        ("blue-archive", "s", "danbooru-blue-archive-s-9286565.jpg"),
        ("hololive", "q", "danbooru-hololive-q-3648775.png"),
        ("hololive", "q", "danbooru-hololive-q-6336205.jpg"),
        ("original", "e", "danbooru-original-e-11239919.jpg"),
        ("original", "e", "danbooru-original-e-11240082.jpg"),
    )

    gallery_members_list: list[SeedFile] = [
        (
            "README.md",
            text_bytes(
                """
                # Indexed tar gallery

                Mixed-content archive used by the local dev browser to
                exercise breadcrumb navigation and per-member preview
                routing (image / audio / text / markdown). The image
                tree mirrors Danbooru's four ratings (g/s/q/e per the
                howto:rate wiki) for Arknights, plus a mixed-IP
                supplement at the same rating spread.
                """
            ),
        ),
        ("text/notes.txt", text_bytes("alpha\nbeta\ngamma\n")),
        (
            "text/log.csv",
            csv_bytes(
                (
                    ("timestamp", "level", "message"),
                    ("2026-04-27T08:00:00Z", "INFO", "browser opened"),
                    ("2026-04-27T08:00:01Z", "INFO", "ranged read 1"),
                    ("2026-04-27T08:00:02Z", "INFO", "ranged read 2"),
                )
            ),
        ),
        (
            "text/config.toml",
            text_bytes(
                """
                [browser]
                page_size = 100
                view = "list"

                [browser.icons]
                tar = "carbon-archive"
                """
            ),
        ),
        (
            "docs/guide.md",
            text_bytes(
                """
                # Member preview guide

                Click any leaf node in the listing to open a member.
                Use the **Back** button to return to the listing
                without losing your in-tar path stack.
                """
            ),
        ),
        (
            "docs/examples/sample.json",
            json_bytes(
                {
                    "id": "sample-001",
                    "labels": ["alpha", "beta", "gamma"],
                    "score": 0.42,
                }
            ),
        ),
        (
            "docs/examples/schema.yaml",
            text_bytes(
                """
                version: 1
                fields:
                  - name: id
                    type: string
                  - name: score
                    type: float32
                """
            ),
        ),
        (
            "audio/bell.wav",
            make_sine_wav_bytes("indexed-tar-bell"),
        ),
        (
            "audio/notes/intro.md",
            text_bytes("# Audio bundle\n\nA short sine tone for preview testing.\n"),
        ),
    ]
    for rating, asset_names in arknights_by_rating.items():
        for asset_name in asset_names:
            ext = asset_name.rsplit(".", 1)[1]
            post_id = asset_name.rsplit("-", 1)[1].rsplit(".", 1)[0]
            path = f"images/arknights/{rating}/{post_id}.{ext}"
            gallery_members_list.append(
                seed_file(path, lambda n=asset_name: remote_asset_bytes(n))
            )
    for ip_slug, rating, asset_name in misc_ip_assets:
        ext = asset_name.rsplit(".", 1)[1]
        post_id = asset_name.rsplit("-", 1)[1].rsplit(".", 1)[0]
        path = f"images/misc/{ip_slug}/{rating}/{post_id}.{ext}"
        gallery_members_list.append(
            seed_file(path, lambda n=asset_name: remote_asset_bytes(n))
        )
    # Materialize once so the tar is built deterministically and the
    # callable closures do not have to be re-invoked downstream.
    gallery_members: tuple[tuple[str, bytes], ...] = tuple(
        materialize_seed_file(entry) for entry in gallery_members_list
    )

    # Tar 1b — flat-images sibling. All 24 Danbooru images directly at
    # the tar root (no subfolders). Lives alongside bundle.tar in
    # archives/gallery/ so a single folder visit produces a long
    # vertical list of thumbnails — the right shape for eyeballing
    # the lazy-loading + concurrency-pool behaviour while scrolling.
    flat_assets: list[str] = []
    for asset_names in arknights_by_rating.values():
        flat_assets.extend(asset_names)
    flat_assets.extend(name for _, _, name in misc_ip_assets)
    flat_members_list: list[SeedFile] = [
        (
            "README.md",
            text_bytes(
                """
                # Flat-image gallery

                Sibling of `bundle.tar` in the same archives/gallery/
                folder. Holds all 24 Danbooru showcase images at the
                tar root with no subfolders, so the full set is
                visible in a single scrollable listing — handy for
                checking that the in-listing thumbnail lazy-load
                only kicks in for rows currently on screen.
                """
            ),
        ),
    ]
    for asset_name in flat_assets:
        flat_members_list.append(
            seed_file(asset_name, lambda n=asset_name: remote_asset_bytes(n))
        )
    flat_members: tuple[tuple[str, bytes], ...] = tuple(
        materialize_seed_file(entry) for entry in flat_members_list
    )

    # Tar 2 — synthetic pagination corpus. ~600 tiny JSON files split
    # across ten folders so the browser exercises both folder-level
    # navigation and the page-size selector.
    large_members_list: list[tuple[str, bytes]] = [
        (
            "README.md",
            text_bytes(
                """
                # Indexed tar large bundle

                Synthetic 600-entry archive used to exercise the
                pagination + search filter inside the indexed-tar
                browser modal.
                """
            ),
        ),
    ]
    for page in range(1, 11):
        for item in range(1, 61):
            path = f"catalog/page-{page:03d}/item-{item:04d}.json"
            large_members_list.append(
                (
                    path,
                    json_bytes(
                        {
                            "page": page,
                            "item": item,
                            "label": f"entry-{page:03d}-{item:04d}",
                            "checksum": hashlib.sha256(path.encode("utf-8"))
                            .hexdigest()[:16],
                        }
                    ),
                )
            )
    large_members: tuple[tuple[str, bytes], ...] = tuple(large_members_list)

    # Tar 3 — same shape as gallery but the index advertises a deliberately
    # wrong sha256 so the modal banner exercises the "hash mismatch"
    # warning path. The actual tar bytes here are still valid and
    # browseable; only the hash recorded inside the JSON is poisoned.
    stale_members: tuple[tuple[str, bytes], ...] = (
        (
            "README.md",
            text_bytes(
                """
                # Indexed tar with stale sidecar hash

                The .json sidecar in this folder advertises a sha256
                that does not match the .tar bytes. Opening the
                archive in the browser should surface a warning
                banner before showing the listing.
                """
            ),
        ),
        ("text/note.txt", text_bytes("stale-hash demo entry\n")),
        (
            "images/sample.jpg",
            remote_asset_bytes("danbooru-genshin-impact-g-8524789.jpg"),
        ),
    )

    # Tar 4 — hash + hash_lfs stripped from the index so the modal
    # exercises the "unknown hash" notice (info banner, not warning).
    no_hash_members: tuple[tuple[str, bytes], ...] = (
        (
            "README.md",
            text_bytes(
                """
                # Indexed tar without hash metadata

                The sidecar for this archive has its `hash` and
                `hash_lfs` fields cleared, so the browser cannot
                verify consistency. Listings still work — only the
                top banner is downgraded to a notice.
                """
            ),
        ),
        ("text/manifest.txt", text_bytes("entries:\n  - alpha\n  - beta\n")),
        (
            "audio/note.wav",
            make_sine_wav_bytes("indexed-tar-no-hash"),
        ),
        (
            "images/sample.jpg",
            remote_asset_bytes("danbooru-blue-archive-s-8990007.jpg"),
        ),
    )

    # Tar 5 — archive containing a real safetensors and a real parquet
    # so the inner-preview reuses FilePreviewDialog directly on the
    # extracted blob. Demonstrates that the safetensors / parquet
    # metadata view works for in-archive members.
    models_members: tuple[tuple[str, bytes], ...] = (
        (
            "README.md",
            text_bytes(
                """
                # Indexed tar with model artifacts

                Real safetensors + parquet members so the inner
                metadata preview can be exercised end to end from
                inside the archive.
                """
            ),
        ),
        (
            "weights/router.safetensors",
            make_single_checkpoint_bytes(
                "indexed-tar-router",
                (
                    ("encoder.embed.weight", (256, 64)),
                    ("encoder.layer0.attn.q_proj.weight", (64, 64)),
                ),
            ),
        ),
        (
            "data/sample.parquet",
            make_parquet_bytes(
                "indexed-tar-models",
                row_count=512,
                payload_size=512,
            ),
        ),
        (
            "metadata/feature-card.json",
            json_bytes(
                {
                    "shards": [
                        "weights/router.safetensors",
                        "data/sample.parquet",
                    ],
                    "purpose": "in-archive metadata preview demo",
                }
            ),
        ),
    )

    gallery_tar, gallery_idx = make_indexed_tar_bundle(
        "indexed-tar-gallery", gallery_members
    )
    flat_tar, flat_idx = make_indexed_tar_bundle(
        "indexed-tar-flat-images", flat_members
    )
    large_tar, large_idx = make_indexed_tar_bundle(
        "indexed-tar-large", large_members
    )
    fake_sha256 = "0" * 64
    stale_tar, stale_idx = make_indexed_tar_with_overrides(
        "indexed-tar-stale",
        stale_members,
        overrides={"hash_lfs": fake_sha256},
    )
    no_hash_tar, no_hash_idx = make_indexed_tar_with_overrides(
        "indexed-tar-no-hash",
        no_hash_members,
        overrides={"hash": "", "hash_lfs": ""},
    )
    models_tar, models_idx = make_indexed_tar_bundle(
        "indexed-tar-models", models_members
    )

    files: tuple[SeedFile, ...] = (
        (
            "README.md",
            text_bytes(
                """
                ---
                license: cc-by-4.0
                pretty_name: Indexed Tar Showcase
                tags:
                  - indexed-tar
                  - hfutils-index
                  - local-dev-fixture
                ---

                # indexed-tar-showcase

                Local-dev dataset for the read-only indexed-tar browser.
                Each subfolder under `archives/` holds a .tar + .json
                sidecar pair that surfaces a different facet of the
                modal:

                | Folder            | Demonstrates                                              |
                |-------------------|-----------------------------------------------------------|
                | archives/gallery  | Nested + flat tar pair: 24 real Danbooru fan-art (4 rates)|
                | archives/large    | Pagination + search inside a single archive (~600 entries)|
                | archives/stale    | Tar bytes diverging from sidecar hash → warning banner    |
                | archives/no-hash  | Sidecar with stripped hashes → info notice banner         |
                | archives/models   | Safetensors / parquet metadata preview from inside the tar|

                The gallery archive has its image members organised by
                Danbooru's four ratings (g/s/q/e per the howto:rate wiki):
                sixteen Arknights posts (four per rating) plus an
                eight-image mixed-IP supplement spanning the same
                rating spread (Genshin Impact / Blue Archive / Hololive
                / original).
                """
            ),
        ),
        (
            "metadata/showcase.json",
            json_bytes(
                {
                    "subfolders": [
                        "archives/gallery",
                        "archives/large",
                        "archives/stale",
                        "archives/no-hash",
                        "archives/models",
                    ],
                    "format": "hfutils.index",
                }
            ),
        ),
        seed_file("archives/gallery/bundle.tar", lambda: gallery_tar),
        seed_file("archives/gallery/bundle.json", lambda: gallery_idx),
        seed_file("archives/gallery/flat-images.tar", lambda: flat_tar),
        seed_file("archives/gallery/flat-images.json", lambda: flat_idx),
        seed_file("archives/large/bundle.tar", lambda: large_tar),
        seed_file("archives/large/bundle.json", lambda: large_idx),
        seed_file("archives/stale/bundle.tar", lambda: stale_tar),
        seed_file("archives/stale/bundle.json", lambda: stale_idx),
        seed_file("archives/no-hash/bundle.tar", lambda: no_hash_tar),
        seed_file("archives/no-hash/bundle.json", lambda: no_hash_idx),
        seed_file("archives/models/bundle.tar", lambda: models_tar),
        seed_file("archives/models/bundle.json", lambda: models_idx),
    )

    return (
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="open-media-lab",
            name="indexed-tar-showcase",
            private=False,
            commits=(
                CommitSeed(
                    summary="Seed indexed-tar showcase",
                    description=(
                        "Plant five tar+sidecar pairs that cover navigation, "
                        "pagination, hash mismatch, missing-hash notice, and "
                        "in-archive safetensors / parquet metadata preview."
                    ),
                    files=files,
                ),
            ),
            download_path="archives/gallery/bundle.json",
            download_sessions=3,
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


def build_big_indexed_tar_pagination_seeds() -> tuple[RepoSeed, ...]:
    """Pagination UAT fixture.

    A single dataset whose root carries 250 hfutils.index-compatible
    tar/json pairs (so 500 entries plus a README) — enough for the
    file-list pager to walk through 10 pages at the default 50/page,
    and 3 pages at the 200/page setting, while keeping the initial
    seed under a minute. Reusing one identical bundle across every
    pair keeps the seed cheap (single tar+index materialization)
    while still exercising the listing surface against real LakeFS
    object counts: LakeFS dedupes by content hash, so the underlying
    storage stays small even though the repo metadata grows. The two
    halves of each pair are adjacent alphabetically, which is the
    "loaded-listing fast path" the sidecar predicate prefers — the
    HEAD-probe fallback is exercised separately by the unit tests.
    """

    bundle_count = 250

    bundle_cache: dict[str, tuple[bytes, bytes]] = {}

    def shared_bundle() -> tuple[bytes, bytes]:
        cached = bundle_cache.get("bundle")
        if cached is not None:
            return cached
        cached = make_indexed_tar_bundle(
            "big-indexed-tar-pagination-shared",
            (
                ("member.json", json_bytes({"shard": "demo", "version": 1})),
            ),
        )
        bundle_cache["bundle"] = cached
        return cached

    files: list[SeedFile] = [
        (
            "README.md",
            text_bytes(
                f"""
                ---
                license: cc-by-4.0
                pretty_name: Indexed-Tar Pagination Bench
                tags:
                  - indexed-tar
                  - pagination
                  - dev-fixture
                ---

                # big-indexed-tar-bench

                Pagination UAT fixture: {bundle_count} hfutils.index-compatible
                tar/json pairs sit under `archives/` so the new file-list
                pager can be exercised against a directory that genuinely
                needs paging. Default 50 entries/page → 10 pages; 200/page
                → 3. The two halves of each pair are alphabetically
                adjacent so the indexed-tar icon lights up via the loaded
                listing — the HEAD-probe fallback is covered by the unit
                tests in `test_repo_viewer_paths.test.js`.

                Every bundle is a clone of the same minimal tar (one
                trivial JSON member). LakeFS dedupes by content hash so
                the underlying object storage is one bundle, not 1000.
                """
            ),
        ),
    ]
    for idx in range(bundle_count):
        tag = f"{idx:04d}"
        files.append(
            seed_file(
                f"archives/bundle-{tag}.tar",
                lambda: shared_bundle()[0],
            )
        )
        files.append(
            seed_file(
                f"archives/bundle-{tag}.json",
                lambda: shared_bundle()[1],
            )
        )

    return (
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="mai_lin",
            name="big-indexed-tar-bench",
            private=False,
            commits=(
                CommitSeed(
                    summary=f"Seed {bundle_count} indexed-tar pairs for pagination UAT",
                    description=(
                        f"Plant {bundle_count} adjacent tar/json pairs so the "
                        "repo file-list pager has a real-shape directory to "
                        "navigate. All bundles share one underlying tar so "
                        "the seed stays cheap on both upload and storage."
                    ),
                    files=tuple(files),
                ),
            ),
            download_path="archives/bundle-0000.tar",
            download_sessions=0,
        ),
    )


def build_tree_expand_stress_seeds() -> tuple[RepoSeed, ...]:
    """Synthetic repo with **highly chaotic** commit history for `/tree?expand=true`.

    The mix is add / modify / delete / restore / folder-delete, deterministic from
    a hash-based byte stream so the output is byte-identical across runs (no
    `random` module — see AGENTS §2 "no random seed fixtures").

    Note: `copyFile` is intentionally not exercised here. KohakuHub's current
    `process_copy_file` re-links the source's internal LakeFS physical address,
    which LakeFS 1.80 rejects with "address is not signed: link address invalid"
    for non-LFS sources (verified live). That's a pre-existing backend limitation
    orthogonal to this fixture's purpose; once the copy path is fixed, copy ops
    can be added back to this churn loop.

    Path-selection bias: the pool is split into a small "hot" tier (most ops
    keep hammering the same handful of paths so they get modified, deleted,
    restored, modified again, ...), a "warm" tier with regular activity, and
    a "cold" tier of one-shot files. This produces individual paths with
    ~30–50 lifecycle transitions each — exactly the pattern that the old
    `resolve_last_commits_for_paths` walker has to chase commit-by-commit.

    Acceptance target: ~40-80 surviving files at HEAD, 150-350 commits,
    where every surviving path's last-touching commit sits at a different
    depth and a meaningful subset has been deleted-and-restored multiple
    times. This is the benchmark fixture for the `resolve_last_commits_for_paths`
    rewrite (issue #59 Plan E).
    """
    pool_size = 80
    num_commits = 280
    paths = [f"shard/group_{i // 10:02d}/file_{i:03d}.json" for i in range(pool_size)]
    folder_prefixes = sorted({p.rsplit("/", 1)[0] for p in paths})  # shard/group_NN

    # Path-selection tiers — biased so a small hot set absorbs the bulk of
    # the churn. Indices live inside the same single pool so all surviving
    # files share the same path schema; only the per-tier weight differs.
    hot_indices = list(range(0, 12))         # 12 paths take ~50% of ops
    warm_indices = list(range(12, 40))       # 28 paths take ~35%
    cold_indices = list(range(40, pool_size))  # 40 paths take ~15%

    def churn_digest(scope: str, ordinal: int) -> bytes:
        """Hash-based deterministic byte source. Slicing it gives op counts,
        path indices, and op-kind rolls without invoking the `random` module.
        """
        return hashlib.sha256(f"{scope}:{ordinal}".encode("utf-8")).digest()

    def pick_index(digest: bytes, byte_offset: int) -> int:
        """Pick a path index biased toward the hot tier."""
        tier_roll = digest[byte_offset] / 256.0
        slot = int.from_bytes(digest[byte_offset + 1 : byte_offset + 5], "big")
        if tier_roll < 0.50:
            return hot_indices[slot % len(hot_indices)]
        if tier_roll < 0.85:
            return warm_indices[slot % len(warm_indices)]
        return cold_indices[slot % len(cold_indices)]

    def file_payload(path: str, version: int) -> bytes:
        digest = hashlib.sha256(f"{path}:{version}".encode("utf-8")).digest()
        body = {
            "path": path,
            "version": version,
            "fingerprint": digest.hex()[:16],
            "tags": ["tree-expand-stress", "dev-fixture"],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # Per-path lifecycle state. `alive[path]` holds the current write counter
    # so each modify gets a fresh sha256; once deleted the path moves to
    # `deleted` (preserving the highest seen version so restores keep climbing).
    alive: dict[str, int] = {}
    deleted: dict[str, int] = {}
    commits: list[CommitSeed] = []

    # Initial commit: plant README + a starter slice spanning all three tiers,
    # so the very first churn round has something to modify/delete/copy from.
    initial_files: list[SeedFile] = [
        FileSeed(
            "README.md",
            (
                "# tree-expand stress fixture\n\n"
                "Synthetic dataset planted by `seed_demo_data.py` to acceptance-test\n"
                "`/tree?expand=true` performance under a chaotic commit history.\n"
                "\n"
                "Hot/warm/cold path tiers, biased churn (modify / delete / restore /\n"
                "copy / folder-delete) — see `build_tree_expand_stress_seeds()`.\n"
            ).encode("utf-8"),
        ),
    ]
    starter_indices = list(hot_indices[:6]) + list(warm_indices[:6]) + list(cold_indices[:3])
    for idx in starter_indices:
        path = paths[idx]
        initial_files.append(FileSeed(path, file_payload(path, 0)))
        alive[path] = 0
    commits.append(
        CommitSeed(
            summary="Initial import of tree-expand stress fixture",
            description="Plant README and a tier-spanning starter slice before the churn loop.",
            files=tuple(initial_files),
        )
    )

    # Churn loop. Heavier per-commit op counts (1-7) drive harder per-path
    # cycling.  Op-kind probabilities (when the path is alive):
    #   modify: 0.55, delete: 0.30, folder-delete: 0.05 (rare, capped one
    #   per commit), default modify: 0.10.
    # When the picked path is currently deleted → restore-via-write (1.0).
    # New paths get added when the tier roll picks an index that has never
    # been touched yet.
    for ordinal in range(1, num_commits):
        head = churn_digest("ops", ordinal)
        ops_count = (head[0] % 7) + 1  # 1..7 ops per commit
        ops: list[SeedFile] = []
        # Track which paths/folders we've already touched in this commit so
        # one round doesn't both modify-and-delete the same file (LakeFS
        # tolerates it but it muddies the lifecycle bookkeeping).
        round_paths: set[str] = set()
        round_folders: set[str] = set()
        # Folder-delete is a heavy op; cap it to at most one per commit.
        folder_delete_done = False
        # Snapshot of `alive` at the start of this commit. Copy ops must use
        # this set as the source, because srcRevision='main' resolves to the
        # PRIOR commit — paths added by earlier ops in this same commit are
        # not yet visible on main and would 404 on the LakeFS link step.
        alive_at_commit_start = frozenset(alive)

        for op_idx in range(ops_count):
            picker = churn_digest(f"ops:{ordinal}:pick", op_idx)
            idx = pick_index(picker, 0)
            path = paths[idx]
            if path in round_paths:
                # Re-pick once with shifted bytes to avoid touching the same
                # path twice in one commit; if still colliding, just skip.
                idx = pick_index(picker, 16)
                path = paths[idx]
                if path in round_paths:
                    continue
            folder = path.rsplit("/", 1)[0]
            if folder in round_folders:
                continue

            roll = picker[6] / 256.0
            copy_roll = picker[7] / 256.0

            if path not in alive and path not in deleted:
                # Never seen → add.
                ops.append(FileSeed(path, file_payload(path, 0)))
                alive[path] = 0
                round_paths.add(path)
                continue

            if path in alive:
                if roll < 0.55:
                    # Modify in place.
                    alive[path] += 1
                    ops.append(FileSeed(path, file_payload(path, alive[path])))
                    round_paths.add(path)
                elif roll < 0.85:
                    # Delete (soft).
                    deleted[path] = alive[path]
                    del alive[path]
                    ops.append(DeletedFileSeed(path))
                    round_paths.add(path)
                elif (
                    roll < 0.90
                    and not folder_delete_done
                    and ordinal > 20  # let some history accrue first
                ):
                    # Folder-delete: drop one entire group_NN/. Pick the
                    # folder deterministically from the digest. Only fires
                    # when at least 3 alive paths live in that folder, so
                    # the op actually erases something meaningful.
                    folder_idx = picker[12] % len(folder_prefixes)
                    folder = folder_prefixes[folder_idx]
                    affected = [p for p in list(alive.keys()) if p.startswith(folder + "/")]
                    if len(affected) >= 3:
                        for p in affected:
                            deleted[p] = alive[p]
                            del alive[p]
                            round_paths.add(p)
                        round_folders.add(folder)
                        ops.append(DeletedFolderSeed(folder))
                        folder_delete_done = True
                    else:
                        # Fallback to plain modify if folder is too sparse.
                        alive[path] += 1
                        ops.append(FileSeed(path, file_payload(path, alive[path])))
                        round_paths.add(path)
                else:
                    # Default: modify.
                    alive[path] += 1
                    ops.append(FileSeed(path, file_payload(path, alive[path])))
                    round_paths.add(path)
            else:
                # Currently deleted → restore with a bumped version.
                next_version = deleted[path] + 1
                ops.append(FileSeed(path, file_payload(path, next_version)))
                del deleted[path]
                alive[path] = next_version
                round_paths.add(path)

        if not ops:
            continue
        commits.append(
            CommitSeed(
                summary=f"Churn round {ordinal:03d}",
                description=(
                    f"Deterministic add/modify/delete/restore/copy/folder-delete "
                    f"round (ordinal={ordinal})."
                ),
                files=tuple(ops),
            )
        )

    return (
        RepoSeed(
            actor="mai_lin",
            repo_type="dataset",
            namespace="mai_lin",
            name="tree-expand-stress-bench",
            private=False,
            commits=tuple(commits),
            download_path="README.md",
            download_sessions=0,
        ),
    )


def build_pr77_demo_repo_seeds() -> tuple[RepoSeed, ...]:
    """Four local-only repos whose ``(namespace, name)`` collides with
    real HuggingFace repos but whose content is intentionally
    structurally different. Each repo's ``README.md`` is the
    verification guide for the contract that repo demonstrates;
    visit them in the SPA at ``/{namespace}/{name}`` after seeding.
    See PR #77 for the design rationale.
    """
    gpt2_readme = text_bytes(
        """
        # PR #77 demo: openai-community/gpt2 (LOCAL)

        This is a **local-only** repository at `openai-community/gpt2`.
        The same path on HuggingFace is the famous GPT-2 with weights,
        config, tokenizer, etc. This local repo holds **only this README** —
        no model weights, no `config.json`, no tokenizer.

        ## What this demonstrates

        The dev backend is configured with a single fallback source
        pointing at `https://huggingface.co`. PR #77's strict-consistency
        rule: **the local namespace owns its name**. Even when the
        requested file exists on HF, a local repo at the same
        `(namespace, name)` is the only place the response can come from.

        ## One-shot verification command

        Asserts every contract below in one go:

        ```bash
        PYTHONPATH=src python scripts/dev/seed_pr77_demo.py
        ```

        Output ends with `OK — every PR #77 demo contract holds end-to-end`
        when the contracts are intact.

        ## Wire-level checks (curl, copy-pasteable)

        ### Missing-file short-circuit (the headline contract)

        ```bash
        curl -i http://127.0.0.1:48888/openai-community/gpt2/resolve/main/config.json
        ```

        Expected response:

        ```
        HTTP/1.1 404 Not Found
        X-Error-Code: EntryNotFound
        X-Error-Message: Entry 'config.json' not found in repository 'openai-community/gpt2' at revision 'main'
        ```

        Critical: **no** `X-Source` / `X-Source-Count` headers — the
        fallback chain did not run. Compare with `?fallback=false`:

        ```bash
        curl -i 'http://127.0.0.1:48888/openai-community/gpt2/resolve/main/config.json?fallback=false'
        ```

        Headers should be byte-identical (modulo `Date` / `X-Request-Id`).

        ### Missing-revision short-circuit

        ```bash
        curl -i http://127.0.0.1:48888/openai-community/gpt2/resolve/no-such-branch/README.md
        ```

        Expected: `404 + X-Error-Code: EntryNotFound` (or
        `RevisionNotFound` depending on the local handler version),
        no `X-Source-Count`.

        ### Local-success wins on the README

        ```bash
        curl -sL http://127.0.0.1:48888/openai-community/gpt2/resolve/main/README.md | head -1
        ```

        Expected: `# PR #77 demo: openai-community/gpt2 (LOCAL)` —
        NOT HuggingFace's GPT-2 README.

        ## Python equivalents (huggingface_hub)

        ```python
        from huggingface_hub import HfApi, hf_hub_download
        from huggingface_hub.errors import (
            EntryNotFoundError, RevisionNotFoundError,
        )

        api = HfApi(endpoint="http://127.0.0.1:48888", token="<your-khub-token>")

        # (a) local data wins
        assert api.list_repo_files("openai-community/gpt2") == ["README.md"]

        # (b) missing file -> EntryNotFoundError (NOT silent HF download)
        try:
            hf_hub_download(
                "openai-community/gpt2", "config.json",
                endpoint="http://127.0.0.1:48888",
                token="<your-khub-token>",
            )
            raise AssertionError("FAIL: cross-source mixing")
        except EntryNotFoundError:
            pass

        # (c) missing revision -> RevisionNotFoundError, no fallback
        try:
            hf_hub_download(
                "openai-community/gpt2", "README.md",
                revision="bogus-branch",
                endpoint="http://127.0.0.1:48888",
                token="<your-khub-token>",
            )
        except (RevisionNotFoundError, EntryNotFoundError):
            pass
        ```

        ## Pre-fix counterfactual

        Pre-#77 the chain would have walked to HF, served HF's GPT-2
        config.json bytes for what the client thinks is *this* local
        repo — two distinct repos collapsed into one `repo_id`. The
        contracts above are the wire-level discriminator between the
        fixed and the buggy behavior.
        """
    )

    bloom_readme = text_bytes(
        """
        # PR #77 demo: bigscience/bloom (LOCAL)

        Local-only repo at `bigscience/bloom`. HF has the real BLOOM 176B
        at the same path. Sister case to `openai-community/gpt2` — same
        contract, different namespace, proves the rule isn't tied to one
        repo path.

        ## One-shot verification

        ```bash
        PYTHONPATH=src python scripts/dev/seed_pr77_demo.py
        ```

        Output includes `bigscience/bloom — sister case, different namespace`
        with two ✓ lines.

        ## Wire-level (curl)

        ```bash
        curl -i http://127.0.0.1:48888/bigscience/bloom/resolve/main/config.json
        ```

        Expected:

        ```
        HTTP/1.1 404 Not Found
        X-Error-Code: EntryNotFound
        X-Error-Message: Entry 'config.json' not found in repository 'bigscience/bloom' at revision 'main'
        ```

        No `X-Source-Count` — local short-circuit, HF's BLOOM never asked.

        ## Python equivalent

        ```python
        from huggingface_hub import HfApi, hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError

        api = HfApi(endpoint="http://127.0.0.1:48888", token="<your-khub-token>")
        assert sorted(api.list_repo_files("bigscience/bloom")) == ["README.md", "demo-note.txt"]

        try:
            hf_hub_download(
                "bigscience/bloom", "config.json",
                endpoint="http://127.0.0.1:48888",
                token="<your-khub-token>",
            )
            raise AssertionError("FAIL: cross-source mixing")
        except EntryNotFoundError:
            pass
        ```
        """
    )

    bloom_demo_note = text_bytes(
        """
        This file proves the local repo has non-trivial content.

        The PR #77 contract: a request for any of the *real* HF BLOOM
        files (config.json, tokenizer.json, model shards, etc.) against
        this khub MUST raise EntryNotFoundError, NOT silently download
        HF's bytes.
        """
    )

    llama_readme = text_bytes(
        """
        # PR #77 demo: meta-llama/Llama-2-7b (LOCAL, with gated HF sibling)

        Local-only repo at `meta-llama/Llama-2-7b`. The same path on HF
        is **gated** — anonymous callers get `401 + X-Error-Code: GatedRepo`.
        This local repo holds only this README.

        ## What this demonstrates

        The strict-consistency contract holds against a *gated* upstream:
        local namespace ownership is absolute. The gated HF response
        never reaches the client - not as content, not as a misleading
        `GatedRepoError`, not as anything.

        ## One-shot verification

        ```bash
        PYTHONPATH=src python scripts/dev/seed_pr77_demo.py
        ```

        Output includes a `meta-llama/Llama-2-7b — gated upstream` block
        with two ✓ lines (the EntryNotFound assertion + a wire-level
        header check).

        ## Wire-level (curl) — the headline contract

        Local has the namespace, HF gates the same path. Strict
        consistency requires `EntryNotFound`, not `GatedRepo`.

        ```bash
        curl -i http://127.0.0.1:48888/meta-llama/Llama-2-7b/resolve/main/config.json
        ```

        Expected:

        ```
        HTTP/1.1 404 Not Found
        X-Error-Code: EntryNotFound
        X-Error-Message: Entry 'config.json' not found in repository 'meta-llama/Llama-2-7b' at revision 'main'
        ```

        Critical: **no** `X-Source-Count`, **no** `GatedRepo` header.
        HF's gated 401 was never seen.

        ### Counter-example — gated HF repo without local collision

        ```bash
        curl -i http://127.0.0.1:48888/meta-llama/Llama-2-70b/resolve/main/config.json
        ```

        Expected (note the SAME backend, only difference is whether
        local owns the namespace):

        ```
        HTTP/1.1 401 Unauthorized
        X-Error-Code: GatedRepo
        X-Source-Count: 1
        X-Error-Message: Upstream source requires authentication ...
        ```

        Same upstream gating, opposite outcomes — decided entirely by
        whether the local namespace owns the path.

        ## Python equivalent

        ```python
        from huggingface_hub import HfApi, hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError, GatedRepoError

        api = HfApi(endpoint="http://127.0.0.1:48888", token="<your-khub-token>")
        assert api.list_repo_files("meta-llama/Llama-2-7b") == ["README.md"]

        try:
            hf_hub_download(
                "meta-llama/Llama-2-7b", "config.json",
                endpoint="http://127.0.0.1:48888",
                token="<your-khub-token>",
            )
            raise AssertionError("FAIL: chain ran past local")
        except EntryNotFoundError:
            pass  # local short-circuit
        except GatedRepoError:
            raise AssertionError("FAIL: HF's gated 401 reached the client")
        ```
        """
    )

    guide_readme = text_bytes(
        """
        # PR #77 demo: pure fallback (no local collision)

        Guide repo — no namespace collision with HF. Exists locally only
        to host these instructions. The interesting cases below are
        repos that this khub does NOT have locally; the goal is to show
        what `huggingface_hub` calls produce when the chain has to do
        the work.

        ## One-shot verification

        ```bash
        PYTHONPATH=src python scripts/dev/seed_pr77_demo.py
        ```

        Output includes a `narugo1992-pr77-demo — pure fallback` block
        with three ✓ lines (success / RepoNotFound / GatedRepo).

        ## Wire-level (curl)

        ### HF-only success: chain serves via HuggingFace

        ```bash
        curl -i http://127.0.0.1:48888/google-bert/bert-base-uncased/resolve/main/config.json
        ```

        Expected (note the `X-Source*` headers — the chain DID run):

        ```
        HTTP/1.1 307 Temporary Redirect
        Location: https://huggingface.co/...
        X-Source: HuggingFace
        X-Source-URL: https://huggingface.co
        X-Source-Status: 307
        ```

        ### Genuinely missing path: aggregate

        ```bash
        curl -i http://127.0.0.1:48888/narugo1992-pr77-demo/this-doesnt-exist-anywhere/resolve/main/x
        ```

        Expected:

        ```
        HTTP/1.1 404 Not Found
        X-Error-Code: RepoNotFound
        X-Source-Count: 1
        ```

        ### Gated upstream without local collision

        ```bash
        curl -i http://127.0.0.1:48888/meta-llama/Llama-2-70b/resolve/main/config.json
        ```

        Expected:

        ```
        HTTP/1.1 401 Unauthorized
        X-Error-Code: GatedRepo
        X-Source-Count: 1
        ```

        For the *contrasting* case (local collision + HF gated, where
        the gated signal is suppressed in favor of local
        EntryNotFound), see `meta-llama/Llama-2-7b` on this same khub.

        ## Python equivalents

        ```python
        from huggingface_hub import HfApi, hf_hub_download
        from huggingface_hub.errors import (
            RepositoryNotFoundError, GatedRepoError,
        )

        api = HfApi(endpoint="http://127.0.0.1:48888", token="<your-khub-token>")

        # HF-only success
        info = api.model_info("google-bert/bert-base-uncased")
        assert info.id == "google-bert/bert-base-uncased"

        # Genuinely missing
        try:
            api.model_info("narugo1992-pr77-demo/this-doesnt-exist-anywhere-77777")
            raise AssertionError("FAIL: succeeded")
        except RepositoryNotFoundError:
            pass

        # Gated, no local collision
        try:
            hf_hub_download(
                "meta-llama/Llama-2-70b", "config.json",
                endpoint="http://127.0.0.1:48888",
                token="<your-khub-token>",
            )
        except GatedRepoError:
            pass  # or LocalEntryNotFoundError on hf_hub_download wrapper
        ```

        ## Cross-reference

        - Local-namespace-priority + EntryNotFound/RevisionNotFound short-circuit:
          `openai-community/gpt2`, `bigscience/bloom`, `meta-llama/Llama-2-7b`.
        - All chain-level decisions are exhaustively unit-tested in
          `test/kohakuhub/api/fallback/test_chain_enumeration.py` (4368 cases).
        """
    )

    return (
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="openai-community",
            name="gpt2",
            private=False,
            commits=(
                CommitSeed(
                    summary="PR #77 demo: namespace-priority + EntryNotFound short-circuit",
                    description="Local-only repo to prove the local namespace owns the name.",
                    files=(("README.md", gpt2_readme),),
                ),
            ),
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="bigscience",
            name="bloom",
            private=False,
            commits=(
                CommitSeed(
                    summary="PR #77 demo: chain short-circuit on EntryNotFound (different namespace)",
                    description="Sister of openai-community/gpt2 — proves the rule isn't path-tied.",
                    files=(
                        ("README.md", bloom_readme),
                        ("demo-note.txt", bloom_demo_note),
                    ),
                ),
            ),
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="meta-llama",
            name="Llama-2-7b",
            private=False,
            commits=(
                CommitSeed(
                    summary="PR #77 demo: strict consistency vs. gated upstream",
                    description="HF's same-path repo is gated; local namespace ownership still wins.",
                    files=(("README.md", llama_readme),),
                ),
            ),
        ),
        RepoSeed(
            actor="mai_lin",
            repo_type="model",
            namespace="narugo1992-pr77-demo",
            name="guide",
            private=False,
            commits=(
                CommitSeed(
                    summary="PR #77 demo: pure-fallback path walk-through",
                    description="No namespace collision; documents how HF-only repos work via fallback.",
                    files=(("README.md", guide_readme),),
                ),
            ),
        ),
    )


REPO_SEEDS = (
    build_repo_seeds()
    + build_open_media_core_repo_seeds()
    + build_indexed_tar_showcase_repo_seeds()
    + build_open_media_showcase_repo_seeds()
    + build_big_indexed_tar_pagination_seeds()
    + build_tree_expand_stress_seeds()
    + build_pr77_demo_repo_seeds()
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

# Global fallback sources installed via the admin API so a fresh local seed can
# resolve public HuggingFace repos out-of-the-box. Namespace "" = global scope.
FALLBACK_SOURCE_SEEDS: tuple[dict, ...] = (
    {
        "namespace": "",
        "url": "https://huggingface.co",
        "token": None,
        "priority": 1000,
        "name": "HuggingFace",
        "source_type": "huggingface",
        "enabled": True,
    },
)


# ---------------------------------------------------------------------------
# Credential plants (API tokens + SSH keys)
# ---------------------------------------------------------------------------
#
# Three real ed25519 keypairs share by both the dev seed and the test
# baseline. Hardcoding them here means every fresh `make reset-and-seed`
# run produces the same fingerprints, so local SSH-based smoke testing
# can rely on a known good private half.

SEED_KEYPAIR_PRIMARY = SeedKeypair(
    public_key=(
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAICkTsun+Px+5LKYR5hM1PFHI07H0mEdBCkjnieQBa8La "
        "seed-primary"
    ),
    private_key=(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACApE7Lp/j8fuSymEeYTNTxRyNOx9JhHQQpI54nkAWvC2gAAAIgSvm6wEr5usAAAAAtzc2gt\n"
        "ZWQyNTUxOQAAACApE7Lp/j8fuSymEeYTNTxRyNOx9JhHQQpI54nkAWvC2gAAAEAR+JseVIp318U4\n"
        "qACfo8LGhfSE0tgeEyg4ieaaxYZMdCkTsun+Px+5LKYR5hM1PFHI07H0mEdBCkjnieQBa8LaAAAA\n"
        "AAECAwQF\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    ),
    fingerprint="SHA256:iK3NzYswWRZyxvuXMcA5x7DscKDXBqdcJDHcnsAmSl0",
)

SEED_KEYPAIR_SECONDARY = SeedKeypair(
    public_key=(
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIM9LPgCG2V6b6eusP4Ds32HSeT9XI5kEh8znwZJL8Kon "
        "seed-secondary"
    ),
    private_key=(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACDPSz4Ahtlem+nrrD+A7N9h0nk/VyOZBIfM58GSS/CqJwAAAIgdEjqnHRI6pwAAAAtzc2gt\n"
        "ZWQyNTUxOQAAACDPSz4Ahtlem+nrrD+A7N9h0nk/VyOZBIfM58GSS/CqJwAAAEARKCxI67mFiA8F\n"
        "KohS5CM4TZ3Yr1XmegpG6k39BVGyz89LPgCG2V6b6eusP4Ds32HSeT9XI5kEh8znwZJL8KonAAAA\n"
        "AAECAwQF\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    ),
    fingerprint="SHA256:V64HYiVM8qORIqyxawv2j9z+f001Zlb2Gfe6es+1yME",
)

SEED_KEYPAIR_TERTIARY = SeedKeypair(
    public_key=(
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIEE6+Zx4EGmF78hvFxw7V99nO+2AMlMq4P3HwC2J1JLl "
        "seed-tertiary"
    ),
    private_key=(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACBBOvmceBBphe/IbxccO1ffZzvtgDJTKuD9x8AtidSS5QAAAIjPifOsz4nzrAAAAAtzc2gt\n"
        "ZWQyNTUxOQAAACBBOvmceBBphe/IbxccO1ffZzvtgDJTKuD9x8AtidSS5QAAAEABkNyXrWp46jN2\n"
        "rlPPMjrdliTdytyHw4SrwcmwUFFwzkE6+Zx4EGmF78hvFxw7V99nO+2AMlMq4P3HwC2J1JLlAAAA\n"
        "AAECAwQF\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    ),
    fingerprint="SHA256:UHiMFHDl1bHuDziVnLOYlAHSDQlah+DAk6yVUe10ZWI",
)

# Demo accounts get a mix of recent / stale / never-used credentials so the
# admin Credentials page exercises every filter against real seeded rows.
SEED_SSH_KEY_PLANTS: tuple[SeedSshKeyPlant, ...] = (
    SeedSshKeyPlant(
        user="mai_lin",
        title="Workstation",
        keypair=SEED_KEYPAIR_PRIMARY,
        last_used_days_ago=1,
    ),
    SeedSshKeyPlant(
        user="mai_lin",
        title="Archived MBP",
        keypair=SEED_KEYPAIR_TERTIARY,
        last_used_days_ago=210,
    ),
    SeedSshKeyPlant(
        user="leo_park",
        title="Leo's Frontend Box",
        keypair=SEED_KEYPAIR_SECONDARY,
        last_used_days_ago=None,
    ),
)

SEED_TOKEN_PLANTS: tuple[SeedTokenPlant, ...] = (
    SeedTokenPlant(
        user="mai_lin",
        name="ci-token",
        plaintext="khub_dev_mai_lin_ci_token_d8f1a2",
        last_used_days_ago=1,
    ),
    SeedTokenPlant(
        user="mai_lin",
        name="archived-cron",
        plaintext="khub_dev_mai_lin_archived_cron_3b91c4",
        last_used_days_ago=180,
    ),
    SeedTokenPlant(
        user="mai_lin",
        name="never-used",
        plaintext="khub_dev_mai_lin_never_used_91dd2e",
        last_used_days_ago=None,
    ),
    SeedTokenPlant(
        user="leo_park",
        name="frontend-deploy",
        plaintext="khub_dev_leo_park_frontend_deploy_4f2c0a",
        last_used_days_ago=7,
    ),
    SeedTokenPlant(
        user="sara_chen",
        name="annotation-import",
        plaintext="khub_dev_sara_chen_annotation_import_77ab09",
        last_used_days_ago=30,
    ),
    SeedTokenPlant(
        user="ivy_ops",
        name="release-bot",
        plaintext="khub_dev_ivy_ops_release_bot_a17e93",
        last_used_days_ago=None,
    ),
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


def plant_seed_tokens() -> None:
    """Insert deterministic API tokens directly into the database.

    Going through ``POST /api/auth/tokens/create`` would generate random
    plaintexts, which means the seed manifest could not name the canonical
    Bearer values. We bypass the API and write rows directly so the
    plaintexts in ``SEED_TOKEN_PLANTS`` are the authoritative answer to
    "which tokens does the dev seed leave behind".
    """
    from datetime import datetime, timedelta, timezone

    from kohakuhub.auth.utils import hash_token
    from kohakuhub.db import Token, User

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for spec in SEED_TOKEN_PLANTS:
        user = User.get_or_none(User.username == spec.user)
        if user is None:
            raise SeedError(
                f"plant token for unknown user '{spec.user}'"
            )

        token_hash = hash_token(spec.plaintext)
        if Token.select().where(Token.token_hash == token_hash).exists():
            # Idempotent: re-running the seed without a full reset is a
            # no-op for already-planted tokens.
            continue

        last_used = (
            None
            if spec.last_used_days_ago is None
            else now - timedelta(days=spec.last_used_days_ago)
        )
        Token.create(
            user=user,
            token_hash=token_hash,
            name=spec.name,
            last_used=last_used,
        )


async def plant_seed_ssh_keys(
    authed_clients: dict[str, httpx.AsyncClient],
) -> None:
    """Plant SSH keys via the public API so fingerprints are computed.

    Using the same endpoint a real user would hit means the planted
    fingerprints are the canonical ones — admin tooling and future
    Git-over-SSH smokes can assert against the values in
    ``SEED_SSH_KEY_PLANTS`` without having to recompute them.
    """
    from datetime import datetime, timedelta, timezone

    from kohakuhub.db import SSHKey, User

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for spec in SEED_SSH_KEY_PLANTS:
        client = authed_clients.get(spec.user)
        if client is None:
            raise SeedError(
                f"plant ssh key for non-authed user '{spec.user}'"
            )

        user = User.get_or_none(User.username == spec.user)
        if user is None:
            raise SeedError(
                f"plant ssh key for unknown user '{spec.user}'"
            )

        already = (
            SSHKey.select()
            .where(
                (SSHKey.user == user)
                & (SSHKey.fingerprint == spec.keypair.fingerprint)
            )
            .exists()
        )
        if not already:
            response = await client.post(
                "/api/user/keys",
                json={"title": spec.title, "key": spec.keypair.public_key},
            )
            await ensure_response(
                response, f"plant ssh key '{spec.title}' for {spec.user}"
            )

        if spec.last_used_days_ago is not None:
            cutoff = now - timedelta(days=spec.last_used_days_ago)
            SSHKey.update(last_used=cutoff).where(
                (SSHKey.user == user)
                & (SSHKey.fingerprint == spec.keypair.fingerprint)
            ).execute()


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


def admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": cfg.admin.secret_token}


async def ensure_fallback_source(
    client: httpx.AsyncClient, source: dict
) -> None:
    list_response = await client.get(
        "/admin/api/fallback-sources",
        params={"namespace": source["namespace"]},
        headers=admin_headers(),
    )
    await ensure_response(
        list_response,
        f"list fallback sources for namespace={source['namespace']!r}",
    )

    normalized_url = source["url"].rstrip("/")
    for existing in list_response.json():
        if existing["url"].rstrip("/") == normalized_url:
            return

    create_response = await client.post(
        "/admin/api/fallback-sources",
        json=source,
        headers=admin_headers(),
    )
    await ensure_response(
        create_response,
        f"create fallback source {source['name']} ({normalized_url})",
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
    # Split the commit's entries by op kind. Only content-bearing entries
    # (FileSeed / tuple) need preupload; delete / folder-delete / copy ops
    # carry no payload.
    delete_paths: list[str] = []
    delete_folder_paths: list[str] = []
    copy_ops: list[CopyFileSeed] = []
    file_entries: list[FileSeed | tuple[str, bytes]] = []
    for entry in commit.files:
        if isinstance(entry, DeletedFileSeed):
            delete_paths.append(entry.path)
        elif isinstance(entry, DeletedFolderSeed):
            delete_folder_paths.append(entry.path)
        elif isinstance(entry, CopyFileSeed):
            copy_ops.append(entry)
        else:
            file_entries.append(entry)

    materialized_files = [materialize_seed_file(entry) for entry in file_entries]
    metadata = [
        {
            "path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in materialized_files
    ]

    preupload_results: dict[str, dict] = {}
    if metadata:
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

    for path in delete_paths:
        ndjson_lines.append(
            {
                "key": "deletedFile",
                "value": {"path": path},
            }
        )

    for path in delete_folder_paths:
        ndjson_lines.append(
            {
                "key": "deletedFolder",
                "value": {"path": path},
            }
        )

    for op in copy_ops:
        ndjson_lines.append(
            {
                "key": "copyFile",
                "value": {
                    "path": op.dest_path,
                    "srcPath": op.src_path,
                    "srcRevision": op.src_revision,
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
        "fallback_sources": [
            {
                "namespace": source["namespace"],
                "url": source["url"].rstrip("/"),
                "name": source["name"],
                "source_type": source["source_type"],
                "priority": source["priority"],
            }
            for source in FALLBACK_SOURCE_SEEDS
        ],
        "api_tokens": [
            {
                "user": spec.user,
                "name": spec.name,
                "plaintext": spec.plaintext,
                "last_used_days_ago": spec.last_used_days_ago,
            }
            for spec in SEED_TOKEN_PLANTS
        ],
        "ssh_keys": [
            {
                "user": spec.user,
                "title": spec.title,
                "fingerprint": spec.keypair.fingerprint,
                "public_key": spec.keypair.public_key,
                "private_key": spec.keypair.private_key,
                "last_used_days_ago": spec.last_used_days_ago,
            }
            for spec in SEED_SSH_KEY_PLANTS
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

        for fallback_source in FALLBACK_SOURCE_SEEDS:
            await ensure_fallback_source(seed_client, fallback_source)

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

        plant_seed_tokens()
        await plant_seed_ssh_keys(authed_clients)

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
