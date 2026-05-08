"""Parametric mock HuggingFace server for end-to-end matrix tests.

Each "scenario" name maps to a specific upstream response shape from
the #75 status-code matrix. Tests configure scenarios by encoding
them in the source URL path: a fallback source URL like
``http://mockhf:8000/scenario/404_entry_not_found`` causes every
request through that source to trigger the EntryNotFound branch.

The design is stateless on purpose — no per-test mutation of
server-side state — so the same uvicorn process can serve any number
of scenarios concurrently. 3-source chain tests just point each
chain entry at a different scenario URL; the mock dispatches per
incoming request based on the leading ``/scenario/{name}/...``
prefix in the path.

Scenarios cover every row of the matrix in #75, anchored to actual
HuggingFace responses captured 2026-04-30::

    200_ok                    — 200 + body / JSON
    307_canonical_redirect    — 307 to canonical-name (info / resolve HEAD)
    307_resolve_cache         — 307 to /api/resolve-cache (resolve HEAD only)
    404_entry_not_found       — 404 + X-Error-Code: EntryNotFound
    404_revision_not_found    — 404 + X-Error-Code: RevisionNotFound
    404_repo_not_found        — 404 + X-Error-Code: RepoNotFound
    401_gated                 — 401 + X-Error-Code: GatedRepo
    403_gated                 — 403 + X-Error-Code: GatedRepo
    401_bare_anti_enum        — 401 + ``Invalid username or password.``
    401_bare_invalid_creds    — 401 + ``Invalid credentials in Authorization header``
    403_bare                  — 403 with no X-Error-Code
    404_bare                  — 404 with no X-Error-Code
    503                       — server error (TRY_NEXT_SOURCE / aggregate 502)
    disabled                  — ``Access to this resource is disabled.``
                                 X-Error-Message marker (no X-Error-Code)

For the ``200_ok`` happy-path scenario, three operation types are
served from the same dispatch logic:

    HEAD / GET on ``/.../resolve/{rev}/{path}`` → 200 + ``OK_BODY``
    GET on ``/api/{type}s/{ns}/{name}``         → 200 + ``OK_INFO_JSON``
    GET on ``/api/{type}s/{ns}/{name}/tree/...``→ 200 + ``OK_TREE_JSON``
    POST on ``/api/{type}s/{ns}/{name}/paths-info/{rev}`` → 200 + ``OK_PATHS_INFO_JSON``

This keeps the same mock usable by every matrix-row × every
operation combination without per-op forks at the call site.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response


# Deterministic content the e2e tests assert on.
OK_BODY = (
    b"# scenario_hf_server happy-path body\n"
    + b"OK-OK-OK-OK-OK-OK-OK-OK\n" * 100
)
OK_ETAG = "scenario-200-etag-deadbeef"
COMMIT = "scenario0123456789abcdef0123456789abcdef0"

OK_INFO_JSON: dict[str, Any] = {
    "id": "owner/scenario-repo",
    "modelId": "owner/scenario-repo",
    "author": "owner",
    "private": False,
    "tags": [],
    "downloads": 0,
    "likes": 0,
    "lastModified": "2026-04-30T00:00:00.000Z",
    "createdAt": "2026-04-30T00:00:00.000Z",
    "sha": COMMIT,
    "siblings": [{"rfilename": "config.json"}],
}

OK_TREE_JSON: list[dict[str, Any]] = [
    {
        "type": "file",
        "path": "config.json",
        "size": len(OK_BODY),
        "oid": OK_ETAG,
    },
]

OK_PATHS_INFO_JSON: list[dict[str, Any]] = [
    {
        "type": "file",
        "path": "config.json",
        "size": len(OK_BODY),
        "oid": OK_ETAG,
    },
]


def _kind_from_path(rest_path: str, method: str) -> str:
    """Infer which fallback operation the inbound URL corresponds to,
    so the 200_ok scenario can return the right body shape.

    Order matters: ``/api/.../tree/...`` and
    ``/api/.../paths-info/...`` are *also* under ``/api/`` so we
    must match those first; everything else under ``/api/`` is the
    bare repo info endpoint.
    """
    p = "/" + rest_path
    # ``/api/resolve-cache/...`` is HF's resolve-cache CDN URL which
    # serves the actual file bytes (the post-307 hop). Treat it as a
    # resolve op so the 200_ok scenario delivers OK_BODY there too.
    if "/resolve/" in p or "/resolve-cache/" in p:
        return "resolve"
    # Tree and paths-info detection must precede the bare ``/api/``
    # branch since both prefix-overlap.
    if "/tree/" in p:
        return "tree"
    if "/paths-info/" in p:
        return "paths_info"
    if p.startswith("/api/"):
        return "info"
    # /api/whoami-v2 etc. — not in fallback's repo flow; treat as info-like
    return "info"


def _ok_response(rest_path: str, request: Request) -> Response:
    """Happy-path responses, keyed by inferred operation."""
    kind = _kind_from_path(rest_path, request.method)

    if kind == "resolve":
        # Direct 200 (no redirect) — pattern_c-style. The
        # 307-redirect-to-resolve-cache is a separate scenario
        # ("307_resolve_cache") because it exercises a different
        # branch (Content-Length backfill, X-Linked-Size handling).
        headers = {
            "content-type": "application/octet-stream",
            "content-length": str(len(OK_BODY)),
            "etag": f'"{OK_ETAG}"',
            "x-repo-commit": COMMIT,
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(content=OK_BODY, status_code=200, headers=headers)

    if kind == "tree":
        return JSONResponse(
            status_code=200, content=OK_TREE_JSON,
            headers={"x-repo-commit": COMMIT},
        )

    if kind == "paths_info":
        return JSONResponse(status_code=200, content=OK_PATHS_INFO_JSON)

    # info / fallback default — return the model info shape. Most
    # HfApi info methods (model_info, dataset_info, etc.) parse
    # any JSON they get into the *_Info dataclass; the fields not
    # present default to None. So a single shape works for all
    # repo types in tests where we just want "info worked".
    return JSONResponse(
        status_code=200, content=OK_INFO_JSON,
        headers={"x-repo-commit": COMMIT},
    )


def _scenario_response(scenario: str, rest_path: str, request: Request) -> Response:
    """Build the response for a given scenario name. See module docstring
    for the full matrix mapping; the priority of branches mirrors
    ``classify_upstream`` so a scenario name maps to exactly one
    decision outcome on the fallback side."""
    if scenario == "200_ok":
        return _ok_response(rest_path, request)

    if scenario == "307_canonical_redirect":
        # HF's ``bert-base-uncased`` → ``google-bert/bert-base-uncased``
        # redirect. Captured 2026-04-30 against
        # ``GET /api/models/bert-base-uncased`` — the response is a
        # bare 307 with **only** ``content-type`` and ``location`` —
        # no ``x-repo-commit``, no ``x-linked-*``. The full HF
        # response shape we replicate here:
        #
        #     HTTP/2 307
        #     content-type: text/plain; charset=utf-8
        #     location: /api/models/google-bert/bert-base-uncased
        #
        # Real HF emits this same shape on /api/{type}s/{name},
        # /api/{type}s/{name}/tree/{rev}, AND
        # /api/{type}s/{name}/paths-info/{rev} — the redirect is
        # cross-method and httpx-followed transparently for the
        # canonical name. (The ``/{name}/resolve/...`` route does
        # NOT 307 to canonical; HF routes resolve directly to the
        # canonical-named storage. See ``RESOLVE_MATRIX`` for why
        # this scenario is excluded from the resolve matrix.)
        return Response(
            status_code=307,
            headers={
                "content-type": "text/plain; charset=utf-8",
                "location": f"/scenario/200_ok/{rest_path}",
            },
        )

    if scenario == "307_resolve_cache":
        # HF's resolve-cache redirect (PR #21). Carries
        # ``X-Linked-Size`` so KohakuHub's resolve-HEAD path skips
        # the extra-HEAD-for-Content-Length backfill. The final hop
        # is served from the same URL under the 200_ok scenario,
        # which detects ``/resolve/`` (or ``/resolve-cache/`` after
        # the kind-detection update) and returns OK_BODY bytes.
        return Response(
            status_code=307,
            headers={
                "location": f"/scenario/200_ok/{rest_path}",
                "content-length": "278",
                "x-linked-size": str(len(OK_BODY)),
                "x-linked-etag": f'"{OK_ETAG}"',
                "x-repo-commit": COMMIT,
            },
        )

    if scenario == "404_entry_not_found":
        return Response(
            status_code=404,
            headers={
                "x-error-code": "EntryNotFound",
                "x-error-message": "Entry not found",
                "content-type": "text/plain; charset=utf-8",
            },
        )

    if scenario == "404_revision_not_found":
        return Response(
            status_code=404,
            headers={
                "x-error-code": "RevisionNotFound",
                "x-error-message": "Invalid rev id: refs",
                "content-type": "text/plain; charset=utf-8",
            },
        )

    if scenario == "404_repo_not_found":
        return Response(
            status_code=404,
            headers={
                "x-error-code": "RepoNotFound",
                "x-error-message": "Repository not found",
                "content-type": "application/json; charset=utf-8",
            },
        )

    if scenario == "401_gated":
        return Response(
            status_code=401,
            headers={
                "x-error-code": "GatedRepo",
                "x-error-message": (
                    "Access to model owner/scenario-repo is restricted. "
                    "You must have access to it and be authenticated to "
                    "access it. Please log in."
                ),
                "content-type": "text/plain; charset=utf-8",
            },
        )

    if scenario == "403_gated":
        return Response(
            status_code=403,
            headers={
                "x-error-code": "GatedRepo",
                "x-error-message": (
                    "Access to model owner/scenario-repo is restricted "
                    "and you are not in the authorized list. Visit ... "
                    "to ask for access."
                ),
                "content-type": "text/plain; charset=utf-8",
            },
        )

    if scenario == "401_bare_anti_enum":
        # HF's anti-enumeration response to anonymous callers: 401
        # with no X-Error-Code, X-Error-Message "Invalid username
        # or password." hf_hub maps this to RepositoryNotFoundError
        # via its 401-anti-enum branch.
        return Response(
            status_code=401,
            headers={
                "x-error-message": "Invalid username or password.",
                "www-authenticate": 'Bearer realm="Authentication required"',
                "content-type": "application/json; charset=utf-8",
            },
        )

    if scenario == "401_bare_invalid_creds":
        # The genuine-broken-token case. hf_hub specifically excludes
        # this exact message string from its 401→RepoNotFound mapping.
        return Response(
            status_code=401,
            headers={
                "x-error-message": "Invalid credentials in Authorization header",
                "www-authenticate": 'Bearer realm="Authentication required"',
                "content-type": "application/json; charset=utf-8",
            },
        )

    if scenario == "403_bare":
        return Response(
            status_code=403,
            headers={"content-type": "application/json; charset=utf-8"},
        )

    if scenario == "404_bare":
        return Response(
            status_code=404,
            headers={"content-type": "application/json; charset=utf-8"},
        )

    if scenario == "503":
        return Response(
            status_code=503,
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    if scenario == "disabled":
        # X-Error-Message string match — hf_hub keys off this exact
        # value to raise DisabledRepoError (no X-Error-Code is set).
        return Response(
            status_code=403,
            headers={
                "x-error-message": "Access to this resource is disabled.",
                "content-type": "text/plain; charset=utf-8",
            },
        )

    # Unknown scenario name — surface as a 500 so a typo in tests is
    # loud. Prevents a fixture mistake from silently passing as
    # "TRY_NEXT_SOURCE / 502 aggregate".
    return JSONResponse(
        status_code=500,
        content={"error": f"unknown scenario: {scenario!r}"},
    )


def build_scenario_hf_app() -> FastAPI:
    """Build the FastAPI app. One process per uvicorn handle; tests
    encode scenarios in the source URL path so this same app can serve
    any combination of matrix rows for any number of concurrent test
    sources."""
    app = FastAPI()

    @app.get("/health")
    async def _health():
        return {"status": "ok"}

    @app.api_route(
        "/scenario/{scenario}/{rest:path}",
        methods=["GET", "HEAD", "POST", "PUT", "DELETE"],
    )
    async def _dispatch(scenario: str, rest: str, request: Request):
        return _scenario_response(scenario, rest, request)

    return app
