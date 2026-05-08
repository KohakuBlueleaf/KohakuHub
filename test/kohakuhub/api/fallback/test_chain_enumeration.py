"""Exhaustive chain enumeration for the #75 repo-grain binding rule.

Covers every combination of:
  * number of fallback sources: 1, 2, 3 (the cap)
  * per-source upstream response: every row of the matrix that
    affects classifier+aggregate decisions (12 rows; 307 redirects
    are excluded — they exercise the redirect-following machinery,
    not the chain-decision logic, and are covered exhaustively in
    test_e2e_matrix.py and test_hf_hub_interop.py)
  * fallback operation: resolve / info / tree / paths-info

Total cases per operation, computed by enumerating every prefix of
TRY_NEXT_SOURCE responses followed by any final response (since once
a source binds, sources past it are never contacted, so chain entries
beyond the binding source don't change the outcome — that property
itself is asserted via ``contacted_urls`` per case):

    1 source   : 12
    2 sources  : 9 × 12 = 108    (9 TRY_NEXT scenarios × any final)
    3 sources  : 9² × 12 = 972

Total: 1092 cases per op × 4 ops = 4368 cases.

The expected outcome of each chain is computed by the SAME helpers
the production code uses (``classify_upstream`` for binding decisions,
``build_aggregate_failure_response`` for the aggregate's status /
X-Error-Code). So the test is "production code agrees with itself
across the chain" rather than a duplicated implementation that could
drift out of sync.

Implementation note — why a single test runs the whole loop instead of
``pytest.parametrize``: parametrizing 7294 cases × 4 ops materializes
~29 000 test items at collection time. Even at "trivial" ~50 ms per
parametrized item (fixture wiring + asyncio.run + Starlette response
construction × pytest hooks), that's ~24 minutes per op. Inlining the
loop in one test reuses a single event loop and one set of patches
across thousands of cases, cutting the cost to seconds. The trade-off
is a less granular ``-k`` filter, which doesn't matter here because
the failure mode "X cases out of N failed" is the actionable signal
either way; the failures list shows the offending chains so the
diagnosis is the same.
"""

from __future__ import annotations

import itertools
from typing import Any, Iterable

import httpx
import pytest

import kohakuhub.api.fallback.operations as fallback_ops
from kohakuhub.api.fallback.utils import (
    build_aggregate_failure_response,
    build_fallback_attempt,
)

from test.kohakuhub.api.fallback.test_operations import (
    DummyCache,
    FakeFallbackClient,
    _content_response,
)


# ---------------------------------------------------------------------------
# Scenario catalogue
# ---------------------------------------------------------------------------


def _resp(status: int, headers: dict | None = None, body: bytes = b"") -> httpx.Response:
    return _content_response(status, body, headers=headers)


def _json_resp(status: int, payload, headers: dict | None = None) -> httpx.Response:
    h = {"content-type": "application/json"}
    if headers:
        h.update(headers)
    return httpx.Response(
        status,
        json=payload,
        headers=h,
        request=httpx.Request("GET", "https://chain.local/x"),
    )


def _scenario_response(name: str, op: str) -> httpx.Response:
    """Build the fake-client response for a given scenario × op.

    Resolve does HEAD-then-GET; info/tree do GET; paths_info does POST.
    The classifier output is the same regardless of method/op, so for
    error scenarios we emit the same response shape; for ``200_ok`` we
    branch by op so info/tree/paths_info get JSON and resolve gets a
    HEAD-shaped 200 (the GET response is queued separately by
    ``_queue_chain``)."""
    if name == "200_ok":
        if op == "resolve":
            return _resp(
                200,
                headers={
                    "content-length": "8",
                    "etag": '"chain-etag"',
                    "x-repo-commit": "chain-commit",
                },
            )
        if op == "info":
            return _json_resp(200, {"id": "owner/chain-repo"})
        if op == "tree":
            return _json_resp(200, [{"path": "f", "type": "file", "size": 4}])
        if op == "paths_info":
            return _json_resp(200, [{"path": "f", "type": "file", "size": 4}])

    if name == "307_canonical_redirect":
        return _resp(
            307,
            headers={"location": "/follow/me", "content-type": "text/plain"},
        )

    if name == "307_resolve_cache":
        return _resp(
            307,
            headers={
                "location": "https://cdn.example/blob",
                "content-length": "278",
                "x-linked-size": "8",
                "x-linked-etag": '"chain-etag"',
                "x-repo-commit": "chain-commit",
            },
        )

    if name == "404_entry_not_found":
        return _resp(
            404,
            headers={
                "x-error-code": "EntryNotFound",
                "x-error-message": "Entry not found",
            },
        )

    if name == "404_revision_not_found":
        return _resp(
            404,
            headers={
                "x-error-code": "RevisionNotFound",
                "x-error-message": "Invalid rev id: refs",
            },
        )

    if name == "404_repo_not_found":
        return _resp(
            404,
            headers={
                "x-error-code": "RepoNotFound",
                "x-error-message": "Repository not found",
            },
        )

    if name == "401_gated":
        return _resp(
            401,
            headers={
                "x-error-code": "GatedRepo",
                "x-error-message": "Access to model X is restricted...",
            },
        )

    if name == "403_gated":
        return _resp(
            403,
            headers={
                "x-error-code": "GatedRepo",
                "x-error-message": "...not in the authorized list...",
            },
        )

    if name == "401_bare_anti_enum":
        return _resp(
            401,
            headers={"x-error-message": "Invalid username or password."},
        )

    if name == "401_bare_invalid_creds":
        return _resp(
            401,
            headers={
                "x-error-message": "Invalid credentials in Authorization header"
            },
        )

    if name == "403_bare":
        return _resp(403)

    if name == "404_bare":
        return _resp(404)

    if name == "503":
        return _resp(503)

    if name == "disabled":
        return _resp(
            403,
            headers={"x-error-message": "Access to this resource is disabled."},
        )

    raise ValueError(f"unknown scenario: {name!r}")


# 307 scenarios excluded from chain enum: they exercise httpx's
# redirect-following (resolve does an extra HEAD, info/tree/paths-info
# rely on httpx's auto-follow on GET) which goes through the *real*
# httpx client even with FakeFallbackClient patched in. Chain
# enumeration is about the loop's classifier+aggregate decision
# table, so we exclude them here and let test_e2e_matrix.py /
# test_hf_hub_interop.py cover the redirect machinery.
BIND_RESPOND_SCENARIOS = ("200_ok",)
BIND_PROPAGATE_SCENARIOS = (
    "404_entry_not_found",
    "404_revision_not_found",
)
# ``disabled`` joined this group in the policy update: a moderation
# takedown on one source doesn't bind the chain — sibling mirrors
# may still serve. The aggregate layer preserves the disabled marker
# so an all-disabled chain still raises DisabledRepoError on the
# hf_hub client. See test_utils.py for the per-classifier proof.
TRY_NEXT_SCENARIOS = (
    "404_repo_not_found",
    "401_gated",
    "403_gated",
    "401_bare_anti_enum",
    "401_bare_invalid_creds",
    "403_bare",
    "404_bare",
    "503",
    "disabled",
)
ALL_SCENARIOS = BIND_RESPOND_SCENARIOS + BIND_PROPAGATE_SCENARIOS + TRY_NEXT_SCENARIOS

assert len(ALL_SCENARIOS) == 12, ALL_SCENARIOS


def _is_bind_and_respond(scenario: str) -> bool:
    return scenario in BIND_RESPOND_SCENARIOS


def _is_bind_and_propagate(scenario: str) -> bool:
    return scenario in BIND_PROPAGATE_SCENARIOS


def _bind_index(chain: tuple[str, ...]) -> int | None:
    for i, sc in enumerate(chain):
        if sc in BIND_RESPOND_SCENARIOS or sc in BIND_PROPAGATE_SCENARIOS:
            return i
    return None


def _expected_aggregate(chain: tuple[str, ...], scope: str):
    """Compute the aggregate response by feeding the production helper."""
    attempts = []
    for sc in chain:
        attempts.append(
            build_fallback_attempt(
                {"name": "X", "url": "https://x.local"},
                response=_scenario_response(sc, "info"),
            )
        )
    return build_aggregate_failure_response(attempts, scope=scope)


def _enumerate_chains(n: int) -> Iterable[tuple[str, ...]]:
    if n == 1:
        for sc in ALL_SCENARIOS:
            yield (sc,)
        return
    for prefix in itertools.product(TRY_NEXT_SCENARIOS, repeat=n - 1):
        for last in ALL_SCENARIOS:
            yield prefix + (last,)


def _all_chain_cases() -> list[tuple[str, ...]]:
    cases: list[tuple[str, ...]] = []
    for n in (1, 2, 3):
        cases.extend(_enumerate_chains(n))
    return cases


ALL_CHAIN_CASES = _all_chain_cases()


# ---------------------------------------------------------------------------
# Per-op test setup
# ---------------------------------------------------------------------------


def _make_sources(chain: tuple[str, ...]) -> list[dict]:
    return [
        {
            "url": f"https://s{i}.local",
            "name": f"S{i}",
            "source_type": "huggingface",
        }
        for i in range(len(chain))
    ]


def _queue_chain(chain: tuple[str, ...], op: str, path: str) -> None:
    """Pre-load FakeFallbackClient with the per-source responses for
    this chain. ``op`` selects HEAD/GET/POST queue keys; the ``path``
    must match what the corresponding ``try_fallback_*`` builds."""
    for i, sc in enumerate(chain):
        url = f"https://s{i}.local"
        if op == "resolve":
            FakeFallbackClient.queue(url, "HEAD", path, _scenario_response(sc, op))
            if _is_bind_and_respond(sc):
                FakeFallbackClient.queue(
                    url, "GET", path,
                    _resp(
                        200,
                        headers={
                            "content-type": "application/octet-stream",
                            "content-length": "8",
                        },
                        body=b"chainok!",
                    ),
                )
        elif op == "paths_info":
            FakeFallbackClient.queue(url, "POST", path, _scenario_response(sc, op))
        else:
            FakeFallbackClient.queue(url, "GET", path, _scenario_response(sc, op))


def _assert_outcome(result, chain: tuple[str, ...], scope: str, op: str) -> None:
    """Assert the production output matches the expected outcome for
    this chain. Throws ``AssertionError`` with the chain as context
    so the loop-driver can collect + report all failing chains."""
    bind_idx = _bind_index(chain)

    if bind_idx is None:
        # All-skip → aggregate. Status + X-Error-Code must match what
        # the production helper produces given the same attempt list.
        expected = _expected_aggregate(chain, scope)
        assert result.status_code == expected.status_code, (
            chain, "status", result.status_code, expected.status_code
        )
        rh = dict(result.headers)
        eh = dict(expected.headers)
        assert rh.get("x-error-code") == eh.get("x-error-code"), (
            chain, "x-error-code", rh.get("x-error-code"), eh.get("x-error-code")
        )
        return

    bind_sc = chain[bind_idx]

    if _is_bind_and_respond(bind_sc):
        # Per-op success shape:
        # - resolve HEAD: Response (200 / 307 from upstream).
        # - info: dict tagged with _source.
        # - tree: Response (200 / 307).
        # - paths_info: list (parsed JSON).
        if op == "info":
            assert isinstance(result, dict), (chain, type(result))
            assert result.get("_source") == f"S{bind_idx}", (chain, result)
        elif op == "paths_info":
            assert isinstance(result, list), (chain, type(result))
        else:
            assert hasattr(result, "status_code"), (chain, type(result))
            assert result.status_code in (200, 307), (chain, result.status_code)
        return

    # BIND_AND_PROPAGATE — forward upstream verbatim.
    upstream = _scenario_response(bind_sc, "info")
    assert hasattr(result, "status_code"), (chain, type(result))
    assert result.status_code == upstream.status_code, (
        chain, "propagate-status", result.status_code, upstream.status_code
    )
    rh = dict(result.headers)
    uh = dict(upstream.headers)
    assert rh.get("x-error-code") == uh.get("x-error-code"), (
        chain, "propagate-x-error-code", rh.get("x-error-code"), uh.get("x-error-code")
    )


def _assert_contacted(chain: tuple[str, ...]) -> None:
    """Verify that sources past the binding index were never contacted.
    For all-skip chains every source must be contacted."""
    bind_idx = _bind_index(chain)
    contacted = {c[0] for c in FakeFallbackClient.calls}
    if bind_idx is None:
        expected = {f"https://s{i}.local" for i in range(len(chain))}
    else:
        expected = {f"https://s{i}.local" for i in range(bind_idx + 1)}
    assert contacted == expected, (chain, contacted, expected)


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setattr(fallback_ops.cfg.fallback, "enabled", True)
    monkeypatch.setattr(fallback_ops, "FallbackClient", FakeFallbackClient)


def _run_chain_op_loop(monkeypatch, op: str, path: str, scope: str, runner):
    """Drive every chain in ALL_CHAIN_CASES through ``runner`` and
    collect failures. ``runner`` is an async callable that takes the
    chain length (used to size the sources fixture) and dispatches the
    fallback op."""
    failures: list[tuple[Any, ...]] = []
    cache = DummyCache()
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)

    # The sources fixture is rebuilt per-chain (length varies), but
    # we can hold one fresh DummyCache across all cases of a single
    # op-loop because we reset between iterations.

    for chain in ALL_CHAIN_CASES:
        try:
            FakeFallbackClient.reset()
            cache.set_calls.clear()
            cache.invalidate_calls.clear()
            cache.cached = None
            sources = _make_sources(chain)
            monkeypatch.setattr(
                fallback_ops, "get_enabled_sources",
                lambda namespace, user_tokens=None, _s=sources: _s,
            )
            _queue_chain(chain, op, path)

            result = runner()

            _assert_outcome(result, chain, scope, op)
            _assert_contacted(chain)
        except AssertionError as e:
            failures.append((chain, str(e)))

    if failures:
        msg_lines = [
            f"{len(failures)}/{len(ALL_CHAIN_CASES)} chain(s) failed for op={op}:",
        ]
        # Cap displayed failures so a structural bug doesn't dump 7K
        # lines into the test output. The first 20 are usually enough
        # to recognize the pattern.
        for chain, msg in failures[:20]:
            msg_lines.append(f"  chain={'+'.join(chain)} — {msg}")
        if len(failures) > 20:
            msg_lines.append(f"  ... {len(failures) - 20} more")
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# Per-op test entry points. One pytest test per op runs ALL chain
# cases via the shared driver above.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_resolve_all(monkeypatch):
    """Resolve (HEAD method) — every chain in ALL_CHAIN_CASES."""
    path = "/models/owner/chain-repo/resolve/main/file.bin"

    def run():
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            fallback_ops.try_fallback_resolve(
                "model", "owner", "chain-repo", "main", "file.bin",
                method="HEAD",
            )
        )

    # We're already inside ``@pytest.mark.asyncio`` — so just await.
    failures: list[tuple[Any, ...]] = []
    cache = DummyCache()
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    for chain in ALL_CHAIN_CASES:
        try:
            FakeFallbackClient.reset()
            cache.set_calls.clear()
            cache.invalidate_calls.clear()
            cache.cached = None
            sources = _make_sources(chain)
            monkeypatch.setattr(
                fallback_ops, "get_enabled_sources",
                lambda namespace, user_tokens=None, _s=sources: _s,
            )
            _queue_chain(chain, "resolve", path)
            result = await fallback_ops.try_fallback_resolve(
                "model", "owner", "chain-repo", "main", "file.bin",
                method="HEAD",
            )
            _assert_outcome(result, chain, scope="file", op="resolve")
            _assert_contacted(chain)
        except AssertionError as e:
            failures.append((chain, str(e)))
    if failures:
        msg = [f"{len(failures)}/{len(ALL_CHAIN_CASES)} resolve chains failed:"]
        for chain, m in failures[:20]:
            msg.append(f"  {'+'.join(chain)} — {m}")
        if len(failures) > 20:
            msg.append(f"  ... {len(failures) - 20} more")
        pytest.fail("\n".join(msg))


@pytest.mark.asyncio
async def test_chain_info_all(monkeypatch):
    """Info — every chain in ALL_CHAIN_CASES, scope=repo."""
    path = "/api/models/owner/chain-repo"
    failures: list[tuple[Any, ...]] = []
    cache = DummyCache()
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    for chain in ALL_CHAIN_CASES:
        try:
            FakeFallbackClient.reset()
            cache.set_calls.clear()
            cache.invalidate_calls.clear()
            cache.cached = None
            sources = _make_sources(chain)
            monkeypatch.setattr(
                fallback_ops, "get_enabled_sources",
                lambda namespace, user_tokens=None, _s=sources: _s,
            )
            _queue_chain(chain, "info", path)
            result = await fallback_ops.try_fallback_info(
                "model", "owner", "chain-repo"
            )
            _assert_outcome(result, chain, scope="repo", op="info")
            _assert_contacted(chain)
        except AssertionError as e:
            failures.append((chain, str(e)))
    if failures:
        msg = [f"{len(failures)}/{len(ALL_CHAIN_CASES)} info chains failed:"]
        for chain, m in failures[:20]:
            msg.append(f"  {'+'.join(chain)} — {m}")
        if len(failures) > 20:
            msg.append(f"  ... {len(failures) - 20} more")
        pytest.fail("\n".join(msg))


@pytest.mark.asyncio
async def test_chain_tree_all(monkeypatch):
    """Tree — every chain in ALL_CHAIN_CASES, scope=repo."""
    path = "/api/models/owner/chain-repo/tree/main/"
    failures: list[tuple[Any, ...]] = []
    cache = DummyCache()
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    for chain in ALL_CHAIN_CASES:
        try:
            FakeFallbackClient.reset()
            cache.set_calls.clear()
            cache.invalidate_calls.clear()
            cache.cached = None
            sources = _make_sources(chain)
            monkeypatch.setattr(
                fallback_ops, "get_enabled_sources",
                lambda namespace, user_tokens=None, _s=sources: _s,
            )
            _queue_chain(chain, "tree", path)
            result = await fallback_ops.try_fallback_tree(
                "model", "owner", "chain-repo", "main"
            )
            _assert_outcome(result, chain, scope="repo", op="tree")
            _assert_contacted(chain)
        except AssertionError as e:
            failures.append((chain, str(e)))
    if failures:
        msg = [f"{len(failures)}/{len(ALL_CHAIN_CASES)} tree chains failed:"]
        for chain, m in failures[:20]:
            msg.append(f"  {'+'.join(chain)} — {m}")
        if len(failures) > 20:
            msg.append(f"  ... {len(failures) - 20} more")
        pytest.fail("\n".join(msg))


@pytest.mark.asyncio
async def test_chain_paths_info_all(monkeypatch):
    """paths-info — every chain in ALL_CHAIN_CASES, scope=file."""
    path = "/api/models/owner/chain-repo/paths-info/main"
    failures: list[tuple[Any, ...]] = []
    cache = DummyCache()
    monkeypatch.setattr(fallback_ops, "get_cache", lambda: cache)
    for chain in ALL_CHAIN_CASES:
        try:
            FakeFallbackClient.reset()
            cache.set_calls.clear()
            cache.invalidate_calls.clear()
            cache.cached = None
            sources = _make_sources(chain)
            monkeypatch.setattr(
                fallback_ops, "get_enabled_sources",
                lambda namespace, user_tokens=None, _s=sources: _s,
            )
            _queue_chain(chain, "paths_info", path)
            result = await fallback_ops.try_fallback_paths_info(
                "model", "owner", "chain-repo", "main", ["f"]
            )
            _assert_outcome(result, chain, scope="file", op="paths_info")
            _assert_contacted(chain)
        except AssertionError as e:
            failures.append((chain, str(e)))
    if failures:
        msg = [f"{len(failures)}/{len(ALL_CHAIN_CASES)} paths-info chains failed:"]
        for chain, m in failures[:20]:
            msg.append(f"  {'+'.join(chain)} — {m}")
        if len(failures) > 20:
            msg.append(f"  ... {len(failures) - 20} more")
        pytest.fail("\n".join(msg))
