"""Pure chain-probe primitive used by the admin debug surface (#78).

``probe_chain`` walks an explicitly-supplied source list, performs the
appropriate HTTP probe per source for the requested operation, classifies
each response with ``utils.classify_upstream``, and returns a structured
``ProbeReport`` capturing every attempt plus the final binding outcome.

Differences from the production path in ``operations.py``:

- **No cache.** Doesn't read or write the per-process ``RepoSourceCache``;
  doesn't take the per-repo binding lock. Safe for repeated tester
  invocations without polluting the live binding state.
- **No FastAPI request context.** Sources, tokens, and probe params come
  in as plain values — no dependency on FastAPI's ``Request`` /
  ``Depends`` machinery. Callable from unit tests, scripts, and the
  admin debug endpoint alike.
- **Single-call per source.** Where ``operations._resolve_one_source``
  does HEAD-then-GET and commits to a bound source for the GET phase,
  the tester probe issues exactly one HTTP call per source matched to
  the operation type (HEAD for ``resolve``, GET for ``info`` / ``tree``,
  POST for ``paths_info``). The single-call surface is enough to show
  what each source returns and which one would bind.

The classifier behaviour matches ``utils.classify_upstream`` exactly,
so a tester verdict (``BIND_AND_RESPOND`` / ``BIND_AND_PROPAGATE`` /
``TRY_NEXT_SOURCE``) on a source carries the same meaning as the
production path's verdict on that source given identical input.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

from kohakuhub.logger import get_logger
from kohakuhub.api.fallback.client import FallbackClient
from kohakuhub.api.fallback.utils import (
    FallbackDecision,
    apply_resolve_head_postprocess,
    classify_upstream,
)

logger = get_logger("FALLBACK_CORE")

ProbeOp = str  # "resolve" | "info" | "tree" | "paths_info"
SUPPORTED_OPS = ("resolve", "info", "tree", "paths_info")


# Response-body preview cap. Long bodies (especially HF metadata JSON
# pages) can run to ~50–100 kB; capping the preview keeps the
# ProbeReport JSON small enough to render in a browser timeline without
# DOM-thrash. Caller can re-issue the probe directly if they need
# more.
_BODY_PREVIEW_LIMIT = 4096

# Curated headers worth surfacing in the UI per attempt — the rest are
# internal CDN bookkeeping not useful for a chain-debug timeline.
#
# Mirrored on the frontend at
# ``src/kohaku-hub-admin/src/utils/api.js:_PROBE_RELEVANT_HEADERS``.
# When adding / removing entries here, update the frontend list too;
# they're deliberately separate (build-time bundle decoupling) but
# semantically the same allowlist.
_RELEVANT_HEADERS = {
    "content-type",
    "content-length",
    "etag",
    "location",
    "x-error-code",
    "x-error-message",
    "x-linked-size",
    "x-repo-commit",
    "x-source",
    "x-source-url",
    "x-source-status",
    "www-authenticate",
}


def _preview_body(response: httpx.Response) -> Optional[str]:
    """Return a UTF-8 preview of the response body, truncated.

    Falls back to a ``[binary, N bytes]`` placeholder when the body
    isn't decodable (e.g. resolve target is a model weight file).
    """
    if response is None:
        return None
    raw = response.content or b""
    if not raw:
        return ""
    truncated = raw[:_BODY_PREVIEW_LIMIT]
    try:
        text = truncated.decode("utf-8")
    except UnicodeDecodeError:
        return f"[binary, {len(raw)} bytes]"
    if len(raw) > _BODY_PREVIEW_LIMIT:
        return text + f"\n…[truncated, total {len(raw)} bytes]"
    return text


def _curated_headers(response: httpx.Response) -> dict[str, str]:
    if response is None:
        return {}
    return {
        k.lower(): v
        for k, v in response.headers.items()
        if k.lower() in _RELEVANT_HEADERS
    }


def _extract_message_from_preview(preview: Optional[str]) -> Optional[str]:
    """Best-effort port of ``utils.extract_error_message`` that operates
    on a ``ProbeAttempt.response_body_preview`` string instead of a live
    ``httpx.Response``.

    Returns the error/message/detail/msg field of a JSON body, the raw
    preview text otherwise, or ``None`` when there's nothing useful
    (empty / truncation-only marker). Caller falls back to ``HTTP
    {status}`` in that case to match production exactly.
    """
    if not preview:
        return None
    # Truncation marker added by ``_preview_body`` on overlong bodies —
    # the JSON parse below would fail on it, so strip before trying.
    text = preview.split("\n…[truncated", 1)[0]
    if not text:
        return None
    try:
        import json
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(parsed, dict):
        for field in ("error", "message", "detail", "msg"):
            if field in parsed:
                value = parsed[field]
                if isinstance(value, str):
                    return value
                if isinstance(value, dict) and isinstance(
                    value.get("message"), str
                ):
                    return value["message"]
        return str(parsed)
    return str(parsed)


@dataclass
class ProbeAttempt:
    """A single source's probe result.

    ``decision`` is a string-name version of ``FallbackDecision`` plus
    transport-failure outcomes (``NETWORK_ERROR`` / ``TIMEOUT``).

    ``kind`` distinguishes the local probe from fallback-source probes
    so the chain-tester UI can render the timeline with the correct
    badge per hop. Defaults to ``"fallback"``; the local-hop builder
    in ``probe_local`` sets ``"local"`` explicitly.

    ``response_body_preview`` is the first ``_BODY_PREVIEW_LIMIT`` bytes
    of the upstream's response body, decoded as UTF-8 when possible.
    ``response_headers`` is a curated subset (see ``_RELEVANT_HEADERS``).
    Both are ``None`` when the per-source request raised before a
    response was produced (timeout / network failure).
    """

    source_name: str
    source_url: str
    source_type: str
    method: str
    upstream_path: str
    status_code: Optional[int]
    x_error_code: Optional[str]
    x_error_message: Optional[str]
    decision: str
    duration_ms: int
    error: Optional[str]
    response_body_preview: Optional[str] = None
    response_headers: dict = field(default_factory=dict)
    kind: str = "fallback"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProbeReport:
    """Aggregate result of a tester probe.

    ``final_response`` mirrors what a production caller would receive
    given the same probe inputs:

    - ``BIND_AND_RESPOND`` / ``BIND_AND_PROPAGATE``: the bound source's
      response (status, headers, body preview).
    - ``CHAIN_EXHAUSTED``: ``None`` here (the production aggregate
      response is built by ``utils.build_aggregate_failure_response``
      and depends on op-specific ``aggregate_scope`` semantics that
      the tester deliberately does not bake in — the per-attempt list
      is enough to diagnose what each source said).
    """

    op: ProbeOp
    repo_id: str
    revision: Optional[str]
    file_path: Optional[str]
    attempts: list[ProbeAttempt] = field(default_factory=list)
    final_outcome: str = "CHAIN_EXHAUSTED"
    bound_source: Optional[dict] = None
    duration_ms: int = 0
    final_response: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "file_path": self.file_path,
            "attempts": [a.to_dict() for a in self.attempts],
            "final_outcome": self.final_outcome,
            "bound_source": self.bound_source,
            "duration_ms": self.duration_ms,
            "final_response": self.final_response,
        }


def _build_kohaku_path(
    op: ProbeOp,
    repo_type: str,
    namespace: str,
    name: str,
    revision: str,
    file_path: str,
) -> str:
    """Construct the upstream path the chain will hit for one op.

    Mirrors the path shapes used by ``operations.try_fallback_*`` so
    a tester verdict aligns 1:1 with the production probe's verdict.
    """
    repo_id = f"{namespace}/{name}"
    if op == "resolve":
        return f"/{repo_type}s/{repo_id}/resolve/{revision}/{file_path}"
    if op == "tree":
        clean = (file_path or "").lstrip("/")
        return f"/api/{repo_type}s/{repo_id}/tree/{revision}/{clean}".rstrip("/")
    if op == "paths_info":
        return f"/api/{repo_type}s/{repo_id}/paths-info/{revision}"
    if op == "info":
        return f"/api/{repo_type}s/{repo_id}"
    raise ValueError(f"Unsupported probe op: {op!r}")  # pragma: no cover


async def _probe_one_source(
    source: dict,
    op: ProbeOp,
    repo_type: str,
    kohaku_path: str,
    paths: Optional[list[str]],
    client_factory,
) -> ProbeAttempt:
    """Run a single per-source HTTP probe + classification.

    Builds a ``ProbeAttempt`` regardless of outcome — including
    timeouts, network errors, and unexpected exceptions — so callers
    can present a complete row per source even when something blew up.
    """
    method = "HEAD" if op == "resolve" else (
        "POST" if op == "paths_info" else "GET"
    )
    started = time.perf_counter()
    response: Optional[httpx.Response] = None
    err: Optional[str] = None
    decision_name: str

    client = client_factory(
        source_url=source["url"],
        source_type=source.get("source_type", "huggingface"),
        token=source.get("token"),
    )

    try:
        if op == "resolve":
            response = await client.head(kohaku_path, repo_type)
        elif op == "paths_info":
            response = await client.post(
                kohaku_path,
                repo_type,
                data={"paths": paths or [], "expand": False},
            )
        elif op == "tree":
            response = await client.get(
                kohaku_path, repo_type, params={"recursive": False, "expand": False}
            )
        else:  # info
            response = await client.get(kohaku_path, repo_type)
    except httpx.TimeoutException as e:
        err = f"timeout: {e}"
        decision_name = "TIMEOUT"
    except Exception as e:  # network / unknown
        err = f"{type(e).__name__}: {e}"
        decision_name = "NETWORK_ERROR"

    duration_ms = int((time.perf_counter() - started) * 1000)

    if response is None:
        return ProbeAttempt(
            source_name=source.get("name", source["url"]),
            source_url=source["url"],
            source_type=source.get("source_type", "huggingface"),
            method=method,
            upstream_path=kohaku_path,
            status_code=None,
            x_error_code=None,
            x_error_message=None,
            decision=decision_name,
            duration_ms=duration_ms,
            error=err,
            response_body_preview=None,
            response_headers={},
        )

    decision = classify_upstream(response)
    decision_name = decision.name

    # Default: curated subset of the raw upstream response headers.
    response_headers = _curated_headers(response)

    # Resolve fidelity (#78 v3): the production HEAD path runs the
    # response through ``apply_resolve_head_postprocess`` before
    # forwarding to the client (Location urljoin + non-LFS follow-HEAD
    # backfill + xet strip + X-Source*). Without replaying that here
    # the simulate's ``response_headers`` shows HF's raw 307 — relative
    # Location, redirect-body Content-Length, no X-Source — none of
    # which is what an hf_hub client actually receives. Replay the
    # postprocess on BIND_AND_RESPOND so simulate matches production.
    # Skip on BIND_AND_PROPAGATE (4xx EntryNotFound / RevisionNotFound
    # / Disabled) — those don't carry a Location to rewrite and
    # production's ``_propagate_upstream_response`` does the lighter
    # strip+source-tag inline; for simulate the curated subset is
    # already enough.
    if op == "resolve" and decision is FallbackDecision.BIND_AND_RESPOND:
        try:
            client_timeout = getattr(client, "timeout", 30.0)
            post_headers = await apply_resolve_head_postprocess(
                response,
                source,
                follow_timeout=client_timeout,
                follow_token=source.get("token"),
            )
            response_headers = {
                k.lower(): v
                for k, v in post_headers.items()
                if k.lower() in _RELEVANT_HEADERS
            }
        except Exception as e:  # pragma: no cover — defensive
            # Postprocess failure shouldn't blow up the simulate; fall
            # through to raw curated headers and surface the error in
            # the attempt so the operator can see what happened.
            logger.warning(
                f"resolve postprocess failed for {source.get('name')}: {e}"
            )

    return ProbeAttempt(
        source_name=source.get("name", source["url"]),
        source_url=source["url"],
        source_type=source.get("source_type", "huggingface"),
        method=method,
        upstream_path=kohaku_path,
        status_code=response.status_code,
        x_error_code=response.headers.get("x-error-code"),
        x_error_message=response.headers.get("x-error-message"),
        decision=decision_name,
        duration_ms=duration_ms,
        error=None,
        response_body_preview=_preview_body(response),
        response_headers=response_headers,
    )


async def probe_full_chain(
    op: ProbeOp,
    repo_type: str,
    namespace: str,
    name: str,
    sources: list[dict],
    *,
    revision: str = "main",
    file_path: str = "",
    paths: Optional[list[str]] = None,
    user=None,
    client_factory=None,
) -> "ProbeReport":
    """Local-first chain probe — the simulate endpoint's entry point (#78 v2).

    Runs ``probe_local`` (which calls the real local handler via
    ``__wrapped__``) to capture the local hop, then advances into the
    fallback chain only when the local decision is ``LOCAL_MISS``. This
    mirrors what ``with_repo_fallback`` does in production:

    - ``LOCAL_HIT``: local owns the repo → final outcome is ``LOCAL_HIT``,
      fallback chain is *not* walked.
    - ``LOCAL_FILTERED``: local owns the repo, entry/revision missing →
      final outcome is ``LOCAL_FILTERED``, fallback chain is *not* walked
      (this is the strict-consistency rule from PR #75/#77).
    - ``LOCAL_OTHER_ERROR``: local error (4xx/5xx that is not a 404) →
      surfaces as the final outcome, fallback chain is *not* walked.
    - ``LOCAL_MISS``: local doesn't have the repo → fallback chain runs
      via ``probe_chain`` against the supplied sources.

    The returned ``ProbeReport.attempts`` is the concatenation of the
    local hop and any fallback hops that ran. ``final_outcome`` is the
    binding outcome from whichever stage ended the probe.
    """
    # Local imported here to avoid a probe_local→core import cycle
    # (probe_local itself imports ``ProbeAttempt`` and ``_build_kohaku_path``
    # from this module).
    from kohakuhub.api.fallback.probe_local import probe_local

    overall_started = time.perf_counter()
    local_attempt = await probe_local(
        op,
        repo_type,
        namespace,
        name,
        revision=revision,
        file_path=file_path,
        paths=paths,
        user=user,
    )

    # The local hop short-circuits the chain unless decision is MISS.
    if local_attempt.decision != "LOCAL_MISS":
        bound = (
            {"name": "local", "url": "", "source_type": "local"}
            if local_attempt.decision in ("LOCAL_HIT", "LOCAL_FILTERED")
            else None
        )
        final_response = {
            "status_code": local_attempt.status_code,
            "headers": dict(local_attempt.response_headers or {}),
            "body_preview": local_attempt.response_body_preview,
        }
        duration_ms = int((time.perf_counter() - overall_started) * 1000)
        return ProbeReport(
            op=op,
            repo_id=f"{namespace}/{name}",
            revision=revision if op != "info" else None,
            file_path=file_path or None,
            attempts=[local_attempt],
            final_outcome=local_attempt.decision,
            bound_source=bound,
            duration_ms=duration_ms,
            final_response=final_response,
        )

    # LOCAL_MISS → walk the fallback chain.
    fallback_report = await probe_chain(
        op,
        repo_type,
        namespace,
        name,
        sources,
        revision=revision,
        file_path=file_path,
        paths=paths,
        client_factory=client_factory,
    )
    duration_ms = int((time.perf_counter() - overall_started) * 1000)

    # Production parity for CHAIN_EXHAUSTED. ``probe_chain`` on its own
    # leaves ``final_response=None`` for the exhaust case (it can't
    # know the right aggregate-scope semantics in isolation), but
    # production *always* hands the client a concrete response — and
    # the simulate is supposed to mirror that byte-for-byte. Two
    # subcases, matching what ``with_repo_fallback`` does:
    #
    #   1. Sources were walked, all fell through →
    #      ``try_fallback_*`` returns a truthy
    #      ``build_aggregate_failure_response`` JSON
    #      (``{error, detail, sources: [...]}``); ``with_repo_fallback``
    #      forwards that under ``if result:``. Reconstruct it here
    #      from the chain attempts so the simulate's ``final_response``
    #      matches the body the hf_hub client would actually parse.
    #   2. No sources were walked (``sources=[]`` passed in, or every
    #      source was filtered upstream) → ``try_fallback_*`` returns
    #      ``None`` and ``with_repo_fallback`` falls into the else
    #      branch, returning the local 404 verbatim. Mirror the local
    #      hop's status/headers/body in that case.
    if (
        fallback_report.final_outcome == "CHAIN_EXHAUSTED"
        and fallback_report.final_response is None
    ):
        if fallback_report.attempts:
            fallback_report.final_response = _build_chain_exhausted_aggregate(
                op, fallback_report.attempts,
            )
        else:
            fallback_report.final_response = {
                "status_code": local_attempt.status_code,
                "headers": dict(local_attempt.response_headers or {}),
                "body_preview": local_attempt.response_body_preview,
            }

    fallback_report.attempts = [local_attempt, *fallback_report.attempts]
    fallback_report.duration_ms = duration_ms
    return fallback_report


def _build_chain_exhausted_aggregate(
    op: ProbeOp, chain_attempts: list["ProbeAttempt"]
) -> dict:
    """Reconstruct production's chain-exhausted aggregate response shape.

    Production runs ``build_aggregate_failure_response`` over per-source
    attempt dicts assembled inside ``operations._run_cached_then_chain``
    via ``build_fallback_attempt``. The simulate's ``probe_chain``
    builds richer ``ProbeAttempt`` records instead, so this adapter
    converts each one into the dict shape ``build_aggregate_failure_response``
    expects (``name, url, status, error_code, category, message``)
    using the same ``_categorize_status`` rules as the production path.
    Aggregate scope: ``"repo"`` for repo-wide ops (info/tree),
    ``"file"`` for per-file ops (resolve/paths_info) — same split as
    operations.py's call sites.
    """
    # Local imports to avoid a top-level utils → core cycle and to keep
    # ``core``'s import graph minimal for callers that don't need
    # aggregate reconstruction (probe_chain alone, which leaves
    # ``final_response=None`` and lets callers — including this helper —
    # decide what to do).
    from kohakuhub.api.fallback.utils import (
        CATEGORY_NETWORK,
        CATEGORY_OTHER,
        CATEGORY_TIMEOUT,
        MAX_ATTEMPT_MESSAGE_LEN,
        _categorize_status,
        build_aggregate_failure_response,
    )

    aggregate_attempts: list[dict] = []
    for a in chain_attempts:
        if a.status_code is not None:
            category = _categorize_status(
                a.status_code, a.x_error_code, a.x_error_message,
            )
            status = a.status_code
            # Production's ``build_fallback_attempt`` calls
            # ``extract_error_message(response)`` — JSON body's
            # error/message/detail/msg field, or the raw body text,
            # or ``HTTP {status}`` if neither yields anything. Mirror
            # that here using ``response_body_preview`` so the
            # ``sources[*].message`` field matches what the hf_hub
            # client actually parses out of the production response.
            message = (
                _extract_message_from_preview(a.response_body_preview)
                or f"HTTP {a.status_code}"
            )
        elif a.decision == "TIMEOUT":
            category = CATEGORY_TIMEOUT
            status = None
            message = a.error or "request timed out"
        elif a.decision == "NETWORK_ERROR":
            category = CATEGORY_NETWORK
            status = None
            message = a.error or "network error"
        else:  # pragma: no cover — defensive
            category = CATEGORY_OTHER
            status = None
            message = a.error or ""
        aggregate_attempts.append({
            "name": a.source_name,
            "url": a.source_url,
            "status": status,
            "category": category,
            "error_code": a.x_error_code,
            "message": message[:MAX_ATTEMPT_MESSAGE_LEN],
        })

    scope = "file" if op in ("resolve", "paths_info") else "repo"
    response = build_aggregate_failure_response(
        aggregate_attempts, scope=scope,
    )
    body_bytes = response.body or b""
    try:
        body_preview = body_bytes.decode("utf-8")
    except UnicodeDecodeError:  # pragma: no cover — JSON is always utf-8
        body_preview = f"[binary, {len(body_bytes)} bytes]"

    headers = {
        k.lower(): v
        for k, v in response.headers.items()
        if k.lower() in _RELEVANT_HEADERS
    }
    return {
        "status_code": response.status_code,
        "headers": headers,
        "body_preview": body_preview,
    }


async def probe_chain(
    op: ProbeOp,
    repo_type: str,
    namespace: str,
    name: str,
    sources: list[dict],
    *,
    revision: str = "main",
    file_path: str = "",
    paths: Optional[list[str]] = None,
    client_factory=None,
) -> ProbeReport:
    """Walk the chain. Stop at first BIND_*. Return a ProbeReport.

    Args:
        op: One of ``"resolve" / "info" / "tree" / "paths_info"``.
        repo_type: ``"model" / "dataset" / "space"``.
        namespace: Repository namespace.
        name: Repository name.
        sources: Priority-ordered list of source dicts. Each must have
            ``url``; may have ``name`` (default = url), ``source_type``
            (default ``huggingface``), ``token`` (default ``None``).
        revision: Branch / commit, only used for resolve / tree / paths_info.
        file_path: File path (resolve, tree).
        paths: List of paths (paths_info).
        client_factory: For tests — defaults to ``FallbackClient``.

    Returns:
        ``ProbeReport`` with one ``ProbeAttempt`` per source consulted
        (chain stops at the first BIND_* outcome). ``final_outcome`` is
        ``BIND_AND_RESPOND`` / ``BIND_AND_PROPAGATE`` / ``CHAIN_EXHAUSTED``.
    """
    if op not in SUPPORTED_OPS:
        raise ValueError(
            f"Unsupported probe op: {op!r}. Expected one of {SUPPORTED_OPS}."
        )

    factory = client_factory or FallbackClient
    kohaku_path = _build_kohaku_path(
        op, repo_type, namespace, name, revision, file_path
    )

    started = time.perf_counter()
    attempts: list[ProbeAttempt] = []
    bound_source: Optional[dict] = None
    final_outcome = "CHAIN_EXHAUSTED"

    for source in sources:
        attempt = await _probe_one_source(
            source=source,
            op=op,
            repo_type=repo_type,
            kohaku_path=kohaku_path,
            paths=paths,
            client_factory=factory,
        )
        attempts.append(attempt)

        if attempt.decision == FallbackDecision.BIND_AND_RESPOND.name:
            bound_source = source
            final_outcome = "BIND_AND_RESPOND"
            break
        if attempt.decision == FallbackDecision.BIND_AND_PROPAGATE.name:
            bound_source = source
            final_outcome = "BIND_AND_PROPAGATE"
            break
        # TRY_NEXT_SOURCE / TIMEOUT / NETWORK_ERROR — keep walking.

    duration_ms = int((time.perf_counter() - started) * 1000)

    # Final response: for BIND_* outcomes, replay the bound attempt's
    # response shape — that's literally what a production caller would
    # see (modulo the production path's HEAD-then-GET commit for
    # ``resolve``, which the tester intentionally simplifies). For
    # CHAIN_EXHAUSTED we leave ``final_response = None`` here because
    # the aggregate envelope production builds depends on
    # ``aggregate_scope`` semantics this low-level primitive
    # deliberately doesn't impose. ``probe_full_chain`` (which knows
    # the op and therefore the right scope) fills it in via
    # ``_build_chain_exhausted_aggregate`` so the simulate endpoint
    # surfaces the production-faithful response.
    final_response: Optional[dict] = None
    if final_outcome != "CHAIN_EXHAUSTED" and attempts:
        last = attempts[-1]
        final_response = {
            "status_code": last.status_code,
            "headers": dict(last.response_headers),
            "body_preview": last.response_body_preview,
        }

    report = ProbeReport(
        op=op,
        repo_id=f"{namespace}/{name}",
        revision=revision if op != "info" else None,
        file_path=file_path or None,
        attempts=attempts,
        final_outcome=final_outcome,
        bound_source=bound_source,
        duration_ms=duration_ms,
        final_response=final_response,
    )
    logger.info(
        f"probe_chain {op} {repo_type}/{namespace}/{name}: "
        f"{len(attempts)} attempt(s), final={final_outcome}, "
        f"bound={bound_source.get('name') if bound_source else None}, "
        f"{duration_ms}ms"
    )
    return report
