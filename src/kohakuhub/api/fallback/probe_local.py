"""Local-handler probe for the chain-tester (#78 redesign v2).

The chain tester is intentionally simulate-only: it must show what the
*current code* would do for a given (user, op, repo, draft sources)
combination without actually issuing a production request that would
mutate the bind cache or follow the live fallback config. To keep the
local-hop fidelity ("local hit"/"local entry-not-found" reproductions
match production exactly), we don't reimplement the local handler
logic — we call the *real* inner handler via ``__wrapped__`` (which
``functools.wraps`` exposes through the ``with_repo_fallback``
decorator), so this stays in lockstep with whatever the production
handler does, with two carefully-bounded exceptions:

- The wrapper short-circuits before the fallback decorator's chain
  logic runs (we pass ``fallback=False`` *and* call the unwrapped
  function), so a local 404 doesn't reach into the live config.
- We construct a synthetic ``Request`` (``SimpleNamespace`` shaped
  the same way the test suite does in ``test_decorators.py``) since
  there is no real ASGI scope at the call site.

Output shape is a ``ProbeAttempt`` with ``kind="local"`` so the chain
tester UI can render it on the same timeline as fallback hops, and so
``probe_full_chain`` (in ``core.py``) can decide whether to continue
into the fallback chain (only ``LOCAL_MISS`` advances).
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import Response

from kohakuhub.db import User
from kohakuhub.logger import get_logger
from kohakuhub.api.fallback.core import (
    ProbeAttempt,
    _BODY_PREVIEW_LIMIT,
    _build_kohaku_path,
)

logger = get_logger("FALLBACK_PROBE_LOCAL")


def _classify_local(
    *,
    status: int,
    x_error_code: Optional[str],
) -> str:
    """Map ``(status, X-Error-Code)`` to a local-hop decision.

    Mirrors the gating rule the production decorator implements
    (``api.fallback.decorators._classify_local_response``):

    - 2xx/3xx → ``LOCAL_HIT`` (local owns the repo and serves it).
    - 404 + ``EntryNotFound`` / ``RevisionNotFound`` → ``LOCAL_FILTERED``
      (local owns the repo, only the entry/revision is missing —
      production stops here, the fallback chain is *not* consulted).
    - 404 (any other or no error code) → ``LOCAL_MISS`` (local doesn't
      have this repo — fallback chain may run).
    - any other 4xx/5xx → ``LOCAL_OTHER_ERROR`` (local error surfaces
      verbatim, no fallback in production).
    """
    if 200 <= status < 400:
        return "LOCAL_HIT"
    if status == 404:
        if x_error_code in ("EntryNotFound", "RevisionNotFound"):
            return "LOCAL_FILTERED"
        return "LOCAL_MISS"
    return "LOCAL_OTHER_ERROR"


def _preview_local_body(content) -> Optional[str]:
    """Best-effort body preview for the local response (JSON dict / Response / list).

    The chain-tester timeline displays this so the operator can confirm
    the local response shape — same role ``ProbeAttempt.response_body_preview``
    plays for fallback hops. Caps at 4 KB to mirror the upstream
    preview limit.
    """
    if content is None:
        return None
    if isinstance(content, Response):
        raw = bytes(content.body) if content.body is not None else b""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"[binary, {len(raw)} bytes]"
        return text[:_BODY_PREVIEW_LIMIT]
    if isinstance(content, (dict, list)):
        try:
            import json

            text = json.dumps(content, default=str)
            return text[:_BODY_PREVIEW_LIMIT]
        except Exception:
            return repr(content)[:_BODY_PREVIEW_LIMIT]
    return str(content)[:_BODY_PREVIEW_LIMIT]


def _build_synthetic_request(
    *,
    method: str,
    upstream_path: str,
) -> SimpleNamespace:
    """Construct the minimum Request shape the inner handlers consume.

    Mirrors the ``_request`` helper in ``test/.../test_decorators.py``.
    Only the attributes the local handlers actually touch are present —
    ``method``, ``url.path``, ``query_params``, ``state.external_tokens``.
    Any handler reaching for something else will surface as a clean
    AttributeError that the simulate endpoint reports as
    ``LOCAL_OTHER_ERROR``.

    ``state.external_tokens`` is hard-coded empty: local handlers in
    this codebase don't read it (it's the fallback decorator's
    private channel for forwarding ``Bearer xxx|url,token|...``
    overrides into the chain). The simulate endpoint *does* compute a
    per-user token overlay (see ``_resolve_user_token_overlay`` in
    ``admin/routers/fallback.py``) but applies it only to the
    fallback source list — never to the local probe — which matches
    the production split. Wired this way intentionally rather than
    plumbed-but-empty-on-the-call-site so a future reader doesn't
    misread the shape.
    """
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=upstream_path),
        query_params={},
        state=SimpleNamespace(external_tokens={}),
    )


def _resolve_inner(op: str):
    """Return the unwrapped (decorator-stripped) local handler for ``op``.

    The ``with_repo_fallback`` decorator uses ``functools.wraps``,
    so calling ``handler.__wrapped__`` yields the raw FastAPI handler
    minus the fallback chain wrapping. That's the function we want —
    it runs the real local logic (DB lookup, permission check,
    LakeFS fetch) but cannot reach into the live fallback config.
    """
    if op == "info":
        from kohakuhub.api.repo.routers.info import get_repo_info
        return get_repo_info.__wrapped__
    if op == "tree":
        from kohakuhub.api.repo.routers.tree import list_repo_tree
        return list_repo_tree.__wrapped__
    if op == "paths_info":
        from kohakuhub.api.repo.routers.tree import get_paths_info
        return get_paths_info.__wrapped__
    if op == "resolve":
        from kohakuhub.api.files import resolve_file_head
        return resolve_file_head.__wrapped__
    raise ValueError(f"Unsupported probe op: {op!r}")


def _build_kwargs(
    op: str,
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    file_path: str,
    paths: Optional[list[str]],
    request: SimpleNamespace,
    user: Optional[User],
) -> dict:
    """Translate simulate-endpoint params into per-op handler kwargs.

    Each handler has a slightly different parameter shape (e.g. the
    info handler doesn't take ``revision``; the tree handler takes
    ``recursive`` / ``expand`` / ``limit`` / ``cursor``). Defaults
    here mirror the values FastAPI would inject for an unset query
    parameter, so the handler exercises the same code path it would
    for a minimal real request.
    """
    if op == "info":
        return {
            "namespace": namespace,
            "repo_name": name,
            "request": request,
            "fallback": False,
            "user": user,
        }
    if op == "tree":
        return {
            "repo_type": repo_type,
            "namespace": namespace,
            "repo_name": name,
            "request": request,
            "revision": revision,
            "path": file_path or "",
            "recursive": False,
            "expand": False,
            "limit": None,
            "cursor": None,
            "name_prefix": None,
            "fallback": False,
            "user": user,
        }
    if op == "paths_info":
        return {
            "repo_type": repo_type,
            "namespace": namespace,
            "repo_name": name,
            "revision": revision,
            "request": request,
            "paths": paths or [],
            "expand": False,
            "fallback": False,
            "user": user,
        }
    if op == "resolve":
        return {
            "repo_type": repo_type,
            "namespace": namespace,
            "name": name,
            "revision": revision,
            "path": file_path or "",
            "request": request,
            "fallback": False,
            "user": user,
        }
    raise ValueError(f"Unsupported probe op: {op!r}")  # pragma: no cover


def _attempt_from_response(
    *,
    method: str,
    upstream_path: str,
    started: float,
    status: int,
    x_error_code: Optional[str],
    x_error_message: Optional[str],
    body_preview: Optional[str],
) -> ProbeAttempt:
    """Build a ``ProbeAttempt`` for a local hop with ``kind="local"``."""
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ProbeAttempt(
        source_name="local",
        source_url="",
        source_type="local",
        method=method,
        upstream_path=upstream_path,
        status_code=status,
        x_error_code=x_error_code,
        x_error_message=x_error_message,
        decision=_classify_local(status=status, x_error_code=x_error_code),
        duration_ms=duration_ms,
        error=None,
        response_body_preview=body_preview,
        response_headers={},
        kind="local",
    )


async def probe_local(
    op: str,
    repo_type: str,
    namespace: str,
    name: str,
    *,
    revision: str = "main",
    file_path: str = "",
    paths: Optional[list[str]] = None,
    user: Optional[User] = None,
) -> ProbeAttempt:
    """Run the local handler for ``op`` against (``namespace``/``name``).

    Returns a ``ProbeAttempt`` capturing the local response (status,
    ``X-Error-Code``, decoded body preview) so the chain tester can
    render the local hop on the same timeline as fallback hops.

    The handler is invoked via its ``__wrapped__`` attribute so the
    fallback decorator's chain-traversal logic is *not* executed —
    we deliberately stop at the local response, regardless of what
    the live fallback config would do for it.
    """
    started = time.perf_counter()
    method = "HEAD" if op == "resolve" else ("POST" if op == "paths_info" else "GET")
    # Both ``_build_kohaku_path`` and ``_resolve_inner`` raise
    # ``ValueError`` on an unrecognised op. We catch either here and
    # surface the message as ``LOCAL_OTHER_ERROR`` so the operator
    # sees the issue inline on the timeline rather than the simulate
    # endpoint blowing up with a 400 / 500.
    try:
        upstream_path = _build_kohaku_path(
            op, repo_type, namespace, name, revision, file_path
        )
        inner = _resolve_inner(op)
    except ValueError as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ProbeAttempt(
            source_name="local",
            source_url="",
            source_type="local",
            method=method,
            upstream_path="",
            status_code=500,
            x_error_code=None,
            x_error_message=str(e),
            decision="LOCAL_OTHER_ERROR",
            duration_ms=duration_ms,
            error=str(e),
            response_body_preview=None,
            response_headers={},
            kind="local",
        )

    request = _build_synthetic_request(
        method=method,
        upstream_path=upstream_path,
    )

    kwargs = _build_kwargs(
        op, repo_type, namespace, name, revision, file_path, paths, request, user
    )

    # ``RepoReadDeniedError`` is imported lazily here because the test
    # harness reloads ``kohakuhub.auth.permissions`` between sessions
    # (per-test backend isolation), giving the *route handler* and this
    # module two different class objects with the same name —
    # ``isinstance(exc, ImportedAtModuleLoad)`` then returns False even
    # though they're "the same" exception. Re-resolving the class on the
    # call path picks up whichever copy is currently bound to the
    # handler chain.
    from kohakuhub.auth.permissions import RepoReadDeniedError

    try:
        result = await inner(**kwargs)
    except RepoReadDeniedError as e:
        # ``RepoReadDeniedError`` propagates past ``with_repo_fallback``
        # in production: the decorator's ``except HTTPException`` skips
        # it (wrong base class) **and** an explicit ``except
        # RepoReadDeniedError: raise`` re-raises it past the generic
        # ``except Exception`` catch — see
        # ``api/fallback/decorators.py``. The global FastAPI handler in
        # ``main.py`` then converts it to ``404 + X-Error-Code:
        # RepoNotFound``. The chain-tester probe here bypasses both the
        # decorator and the global handler (we call the unwrapped inner
        # directly), so we have to reproduce the conversion locally to
        # keep the simulate output in lockstep with what production
        # actually emits for this case.
        return _attempt_from_response(
            method=method,
            upstream_path=upstream_path,
            started=started,
            status=404,
            x_error_code="RepoNotFound",
            x_error_message=f"Repository '{e.repo_id}' ({e.repo_type}) not found",
            body_preview=None,
        )
    except HTTPException as e:
        x_code = (e.headers or {}).get("X-Error-Code") or (e.headers or {}).get(
            "x-error-code"
        )
        x_msg = (e.headers or {}).get("X-Error-Message") or (e.headers or {}).get(
            "x-error-message"
        )
        return _attempt_from_response(
            method=method,
            upstream_path=upstream_path,
            started=started,
            status=e.status_code,
            x_error_code=x_code,
            x_error_message=x_msg,
            body_preview=_preview_local_body(e.detail),
        )
    except Exception as e:  # pragma: no cover - safety net
        # Any unhandled exception surfaces as 500 LOCAL_OTHER_ERROR
        # so the simulate endpoint never blows up the request.
        logger.exception(f"probe_local unexpected error for {op}: {e}")
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ProbeAttempt(
            source_name="local",
            source_url="",
            source_type="local",
            method=method,
            upstream_path=upstream_path,
            status_code=500,
            x_error_code=None,
            x_error_message=f"{type(e).__name__}: {e}",
            decision="LOCAL_OTHER_ERROR",
            duration_ms=duration_ms,
            error=f"{type(e).__name__}: {e}",
            response_body_preview=None,
            response_headers={},
            kind="local",
        )

    # Result is a Response object (JSONResponse / Response) or a dict / list.
    if isinstance(result, Response):
        status = getattr(result, "status_code", 200)
        x_code = result.headers.get("x-error-code") if result.headers else None
        x_msg = result.headers.get("x-error-message") if result.headers else None
        return _attempt_from_response(
            method=method,
            upstream_path=upstream_path,
            started=started,
            status=status,
            x_error_code=x_code,
            x_error_message=x_msg,
            body_preview=_preview_local_body(result),
        )

    # dict / list / other Python value → success (FastAPI auto-encodes
    # to a JSON 200).
    return _attempt_from_response(
        method=method,
        upstream_path=upstream_path,
        started=started,
        status=200,
        x_error_code=None,
        x_error_message=None,
        body_preview=_preview_local_body(result),
    )
