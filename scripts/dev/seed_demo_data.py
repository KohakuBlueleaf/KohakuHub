#!/usr/bin/env python3
"""Create deterministic local demo data through KohakuHub's API surface."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import sys
import textwrap
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kohakuhub.config import cfg
from kohakuhub.main import app
from kohakuhub.utils.s3 import init_storage

SEED_VERSION = "local-dev-demo-v1"
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
    files: tuple[tuple[str, bytes], ...]


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


def lfs_blob(label: str) -> bytes:
    header = f"SEED-LFS::{label}\n".encode("utf-8")
    return header + (b"0123456789abcdef" * 64)


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
                        (
                            "checkpoints/lineart-caption-base.safetensors",
                            lfs_blob("lineart-caption-base"),
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
                        (
                            "checkpoints/aurora-ocr-lite.safetensors",
                            lfs_blob("aurora-ocr-lite"),
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
                        (
                            "checkpoints/marine-seg-small.safetensors",
                            lfs_blob("marine-seg-small"),
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


REPO_SEEDS = build_repo_seeds()

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
    metadata = []
    payload_by_path = {}

    for path, content in commit.files:
        sha256 = hashlib.sha256(content).hexdigest()
        metadata.append(
            {
                "path": path,
                "size": len(content),
                "sha256": sha256,
            }
        )
        payload_by_path[path] = content

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

    for path, content in commit.files:
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
