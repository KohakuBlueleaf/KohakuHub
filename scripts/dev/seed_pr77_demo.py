"""Standalone verification script for the PR #77 demo repos.

The demo repos themselves are now seeded as part of
``scripts/dev/seed_demo_data.py`` (so they appear after
``make reset-and-seed``). This script is *not* a seed — it just runs
end-to-end ``huggingface_hub`` calls against a live khub and asserts
each demo's stated contract holds.

Run after the dev backend is up (``make backend``) and seeded
(``make reset-and-seed``):

    PYTHONPATH=src python scripts/dev/seed_pr77_demo.py

Each step prints OK / FAIL with a short note. Exits with non-zero on
any failure. The verification commands themselves are also documented
in each demo repo's ``README.md``, browseable at
``http://127.0.0.1:48888/{namespace}/{name}``.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import requests

KHUB = "http://127.0.0.1:48888"
SEED_USER = "mai_lin"
SEED_PWD = "KohakuDev123!"


def _hf_error(name: str):
    try:
        mod = __import__("huggingface_hub.errors", fromlist=[name])
        return getattr(mod, name)
    except (ImportError, AttributeError):
        pass
    mod = __import__("huggingface_hub.utils", fromlist=[name])
    return getattr(mod, name)


def step(s: str): print(f"\n--- {s} ---")
def ok(s: str): print(f"  ✓ {s}")
def bad(s: str): print(f"  ✗ {s}")
def info(s: str): print(f"  · {s}")


def login_token() -> str:
    r = requests.post(
        f"{KHUB}/api/auth/login",
        json={"username": SEED_USER, "password": SEED_PWD},
        timeout=10,
    )
    r.raise_for_status()
    cookies = r.cookies
    r2 = requests.post(
        f"{KHUB}/api/auth/tokens/create",
        json={"name": "pr77-verify"},
        cookies=cookies,
        timeout=10,
    )
    r2.raise_for_status()
    return r2.json()["token"]


def main() -> int:
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        print("ERROR: huggingface_hub not installed", file=sys.stderr)
        return 1

    EntryNotFoundError = _hf_error("EntryNotFoundError")
    RevisionNotFoundError = _hf_error("RevisionNotFoundError")
    RepositoryNotFoundError = _hf_error("RepositoryNotFoundError")
    GatedRepoError = _hf_error("GatedRepoError")

    try:
        token = login_token()
    except Exception as e:
        print(f"ERROR: login failed — {e}", file=sys.stderr)
        print(
            "Hint: make sure dev backend is up and `make reset-and-seed` ran.",
            file=sys.stderr,
        )
        return 1

    api = HfApi(endpoint=KHUB, token=token)
    failures: list[str] = []

    def _exc_label(exc_class):
        if isinstance(exc_class, tuple):
            return "/".join(c.__name__ for c in exc_class)
        return exc_class.__name__

    def _expect_exception(label, exc_class, callable_, *args, **kwargs):
        try:
            callable_(*args, **kwargs)
            failures.append(label)
            bad(f"{label}: expected {_exc_label(exc_class)}, got success")
        except exc_class as e:
            ok(f"{label}: raised {type(e).__name__}")
        except Exception as e:
            failures.append(label)
            bad(f"{label}: expected {_exc_label(exc_class)}, got {type(e).__name__}: {e}")

    # ---------------- openai-community/gpt2 ----------------
    step("openai-community/gpt2 — namespace priority + EntryNotFound + RevisionNotFound")

    try:
        files = api.list_repo_files("openai-community/gpt2")
        if files == ["README.md"]:
            ok(f"list_repo_files == ['README.md'] (local data wins)")
        else:
            failures.append("openai-community/gpt2: list_repo_files mismatch")
            bad(f"list_repo_files = {files}; expected ['README.md']")
    except Exception as e:
        failures.append("openai-community/gpt2: list_repo_files crashed")
        bad(f"list_repo_files: {type(e).__name__}: {e}")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            p = hf_hub_download(
                "openai-community/gpt2", "README.md",
                endpoint=KHUB, token=token, cache_dir=tmp,
            )
            body = Path(p).read_text()
            if "PR #77 demo" in body:
                ok("README.md download: local bytes (contains 'PR #77 demo')")
            else:
                failures.append("openai-community/gpt2: README content mismatch")
                bad(f"README.md content unexpected: {body[:120]!r}")
        except Exception as e:
            failures.append("openai-community/gpt2: README download failed")
            bad(f"README download: {type(e).__name__}: {e}")

    _expect_exception(
        "openai-community/gpt2: missing config.json",
        EntryNotFoundError,
        hf_hub_download, "openai-community/gpt2", "config.json",
        endpoint=KHUB, token=token,
    )

    _expect_exception(
        "openai-community/gpt2: missing revision",
        (RevisionNotFoundError, EntryNotFoundError),
        hf_hub_download, "openai-community/gpt2", "README.md",
        revision="bogus-branch", endpoint=KHUB, token=token,
    )

    # ---------------- bigscience/bloom ----------------
    step("bigscience/bloom — sister case, different namespace")

    try:
        files = api.list_repo_files("bigscience/bloom")
        if sorted(files) == ["README.md", "demo-note.txt"]:
            ok(f"list_repo_files == {sorted(files)} (local data wins)")
        else:
            failures.append("bigscience/bloom: list_repo_files mismatch")
            bad(f"list_repo_files = {files}")
    except Exception as e:
        failures.append("bigscience/bloom: list_repo_files crashed")
        bad(f"list_repo_files: {type(e).__name__}: {e}")

    _expect_exception(
        "bigscience/bloom: missing config.json",
        EntryNotFoundError,
        hf_hub_download, "bigscience/bloom", "config.json",
        endpoint=KHUB, token=token,
    )

    # ---------------- meta-llama/Llama-2-7b (gated upstream) ----------------
    step("meta-llama/Llama-2-7b — gated upstream, local namespace owns")

    _expect_exception(
        "meta-llama/Llama-2-7b: missing config.json (must NOT surface as GatedRepo)",
        EntryNotFoundError,
        hf_hub_download, "meta-llama/Llama-2-7b", "config.json",
        endpoint=KHUB, token=token,
    )

    # Wire-level: confirm no X-Source-Count / no GatedRepo signal
    head_resp = requests.head(
        f"{KHUB}/meta-llama/Llama-2-7b/resolve/main/config.json",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10, allow_redirects=False,
    )
    if (
        head_resp.headers.get("X-Error-Code") == "EntryNotFound"
        and head_resp.headers.get("X-Source-Count") is None
        and head_resp.headers.get("X-Error-Code") != "GatedRepo"
    ):
        ok("wire-level: X-Error-Code=EntryNotFound, no X-Source-Count, no GatedRepo")
    else:
        failures.append("meta-llama/Llama-2-7b: wire-level signal mismatch")
        bad(f"unexpected headers: {dict(head_resp.headers)}")

    # ---------------- pure-fallback path ----------------
    step("narugo1992-pr77-demo — pure fallback (no local collision)")

    try:
        info_obj = api.model_info("google-bert/bert-base-uncased")
        if getattr(info_obj, "id", None):
            ok(f"google-bert/bert-base-uncased via fallback: id={info_obj.id}")
        else:
            failures.append("pure-fallback success: model_info missing id")
            bad("model_info returned no id")
    except Exception as e:
        failures.append("pure-fallback success failed")
        bad(f"model_info: {type(e).__name__}: {e}")

    _expect_exception(
        "pure-fallback miss: aggregated RepoNotFound",
        RepositoryNotFoundError,
        api.model_info, "narugo1992-pr77-demo/this-doesnt-exist-anywhere-77777",
    )

    # gated upstream without local collision -> GatedRepoError
    # (hf_hub_download wraps GatedRepoError in some flows; tolerate
    # either GatedRepoError or LocalEntryNotFoundError per the
    # version of hf_hub installed).
    try:
        LocalEntryNotFoundError = _hf_error("LocalEntryNotFoundError")
    except Exception:
        LocalEntryNotFoundError = GatedRepoError  # fallback alias
    _expect_exception(
        "pure-fallback gated: hf_hub raises GatedRepoError or wrapper",
        (GatedRepoError, LocalEntryNotFoundError),
        hf_hub_download, "meta-llama/Llama-2-70b", "config.json",
        endpoint=KHUB, token=token,
    )

    # ---------------- summary ----------------
    print()
    if failures:
        print(f"FAIL — {len(failures)} step(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — every PR #77 demo contract holds end-to-end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
