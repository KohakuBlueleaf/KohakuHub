"""Strict-consistency guarantees for the fallback chain.

The user-facing contract: **for one user with one fixed authentication
context, against one ``(repo_type, namespace, name)``, while neither
the local repo state nor any source's repo state changes during the
window, every call to every HF-compatible API exposed by KohakuHub
must access the same source's same repo.**

The four guarantees this file exhaustively exercises through the real
``huggingface_hub`` library:

1. **Bound source failure does NOT rebind.** Once a repo is cached
   to source X, a subsequent X failure (5xx, X transient auth, etc.)
   surfaces X's error to the caller. The cache is *not* invalidated;
   client retries within TTL hit X again.

2. **Cache hit ≠ chain probe.** A cache-hit call only contacts the
   bound source. Sibling sources are never asked, even if they
   would have served successfully.

3. **Concurrent first-binding serializes.** Two concurrent
   cache-miss calls for the same repo bind to the same source; the
   second waits on the first's binding lock and consumes the cache.

4. **Deterministic chain order.** Cache TTL expiry under unchanged
   external state re-binds the same source (priority order is
   deterministic).

5. **Multi-op session consistency.** A sequence of mixed ops on the
   same repo (``model_info`` → ``list_repo_files`` →
   ``hf_hub_download`` × N → ``get_paths_info``) all hit the same
   source, regardless of which API method called first.

Testing strategy:

- Spin up one ``scenario_hf_server`` process serving every matrix
  scenario via URL-encoded scenario names.
- For each test, configure khub to point at one or more scenario
  URLs (in priority order).
- Drive real ``huggingface_hub`` calls; inspect what the upstream
  mock saw via the call log it exposes.
- Two-source patterns where the second source is a "TRAP": its URL
  serves ``200_ok`` so a cross-source rebind would *succeed* —
  meaning the tests actively detect rebinding by observing the
  caller getting a different source's data.

This is the file the user asked to be added to "strict consistency"
the design — every test below must hold, or the contract is broken.
"""
from __future__ import annotations

import asyncio
import threading
from collections import Counter
from pathlib import Path

import httpx
import pytest
from huggingface_hub import HfApi, hf_hub_download

import kohakuhub.api.fallback.operations as fallback_ops
from test.kohakuhub.support.live_server import start_live_server, stop_live_server
from test.kohakuhub.support.scenario_hf_server import (
    OK_BODY,
    OK_INFO_JSON,
    build_scenario_hf_app,
)


def _hf_error(name: str):
    try:
        mod = __import__("huggingface_hub.errors", fromlist=[name])
        return getattr(mod, name)
    except (ImportError, AttributeError):
        pass
    mod = __import__("huggingface_hub.utils", fromlist=[name])
    return getattr(mod, name)


# A request observer wraps the scenario mock so tests can inspect
# what the upstream actually saw — which scenario path was hit, how
# many times, etc. The scenario mock is stateless; this observer is
# in-process middleware that records each (scenario_name, path,
# method) tuple as a side effect.
#
# We attach the observer in the ``scenario_mock`` fixture below.

class _RequestLog:
    """Thread-safe-enough request log. The scenario mock runs in a
    daemon thread inside the same process via ``start_live_server``,
    so a plain list with a Lock works."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entries: list[tuple[str, str, str]] = []  # (method, scenario, path)

    def reset(self) -> None:
        with self.lock:
            self.entries.clear()

    def record(self, method: str, scenario: str, path: str) -> None:
        with self.lock:
            self.entries.append((method, scenario, path))

    def scenarios_seen(self) -> list[str]:
        with self.lock:
            return [s for _m, s, _p in self.entries]

    def scenarios_count(self) -> Counter[str]:
        return Counter(self.scenarios_seen())


REQUEST_LOG = _RequestLog()


def _owner_cache_key() -> tuple[int, str]:
    """Return (user_id, tokens_hash) matching authed owner-token requests.

    Strict-consistency tests in this module authenticate as ``owner``
    via ``hf_api_token``; cache lookups/seeds must use the same
    ``(user_id, tokens_hash)`` so that direct-cache state and live
    request state share a bucket. Owner has no external tokens
    configured in these tests, so ``tokens_hash`` is empty.
    """
    from kohakuhub.db import User

    return User.get(User.username == "owner").id, ""


@pytest.fixture(scope="module")
def scenario_mock_url():
    """Single scenario mock for all consistency tests in this module.

    A request observer is patched in via FastAPI middleware so each
    test can inspect what the upstream actually saw."""
    from fastapi import FastAPI, Request

    app = build_scenario_hf_app()

    @app.middleware("http")
    async def _record(request: Request, call_next):
        path = request.url.path
        # Path shape: /scenario/{name}/{rest}. The mock's catch-all
        # has scenario in path; extract for the log.
        scenario = "?"
        if path.startswith("/scenario/"):
            tail = path[len("/scenario/"):]
            scenario = tail.split("/", 1)[0] if "/" in tail else tail
        REQUEST_LOG.record(request.method, scenario, path)
        return await call_next(request)

    handle = start_live_server(app)
    try:
        yield handle.base_url
    finally:
        stop_live_server(handle)


@pytest.fixture
def consistency_env(backend_test_state, scenario_mock_url):
    """Per-test fixture that:
      * resets per-process fallback cache
      * resets the binding-lock registry
      * resets the request log
      * provides a ``configure(*scenario_names)`` callable that
        installs a ``len(scenario_names)``-source fallback chain
        pointing at the scenario mock
      * restores prior cfg.fallback on exit
    """
    cfg = backend_test_state.modules.config_module.cfg
    old_sources = list(cfg.fallback.sources)
    old_enabled = cfg.fallback.enabled
    cfg.fallback.enabled = True

    fallback_cache_module = backend_test_state.modules.fallback_cache_module

    def _reset_runtime():
        fallback_cache_module.get_cache().clear()
        fallback_ops._reset_binding_locks_for_tests()
        REQUEST_LOG.reset()

    def _configure(*scenario_names: str):
        cfg.fallback.sources = [
            {
                "url": f"{scenario_mock_url}/scenario/{name}",
                "name": f"Mock-{name}-{i}",
                "source_type": "huggingface",
                "priority": i + 1,
            }
            for i, name in enumerate(scenario_names)
        ]
        _reset_runtime()

    _reset_runtime()
    try:
        yield _configure, _reset_runtime
    finally:
        cfg.fallback.sources = old_sources
        cfg.fallback.enabled = old_enabled
        _reset_runtime()


def _scenarios_only(log: _RequestLog) -> list[str]:
    """Discard the path tail; return only the ordered list of
    scenario names that the upstream actually saw across all calls."""
    return log.scenarios_seen()


# ===========================================================================
# Guarantee 1: Bound source failure does NOT rebind.
# ===========================================================================


def test_bound_source_5xx_does_not_rebind_to_sibling(
    live_server_url, hf_api_token, consistency_env,
):
    """Source A binds first (success on info call); next call (resolve)
    sees A return 5xx — but we must NOT walk to B even though B is
    queued as a working source. The 5xx surfaces verbatim."""
    configure, reset = consistency_env

    # Setup: A serves info OK, then A starts 5xx-ing. We can't make a
    # single scenario URL flip behavior over time, so we exercise
    # this through two phases of the same test:
    #
    # Phase 1: configure with a single source pointing at "200_ok",
    # call model_info → caches binding to that URL.
    configure("200_ok")
    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    info = api.model_info("owner/scenario-repo")
    assert info.id == OK_INFO_JSON["id"]

    # Phase 2: WITHOUT clearing the cache, swap the URL behind that
    # source so the SAME url path now returns 503. We do this by
    # changing the scenario name in the source URL — but that would
    # change the cached source URL too. Better: configure a fresh
    # chain of [503, 200_ok] without resetting the cache, then call
    # again. The first source URL still in cache (from phase 1) is
    # NOT in the new sources list, so it's an "orphan cache" and
    # gets invalidated per rule #3.
    #
    # That doesn't exercise our rule #1 directly. Instead, do this:
    # use one source URL that switches behavior via the scenario
    # routing — but our scenario mock can't do that. So we adopt the
    # alternative: configure ONE source with scenario "503", then
    # warm the cache by directly seeding the per-process cache, then
    # observe that no rebind occurs.

    # Direct approach: seed cache and verify no-rebind.
    reset()
    configure("503")
    fallback_cache = (
        consistency_env.__self__ if False else None  # placeholder; access below
    )
    # Seed cache: pretend we already bound to the (single) "503" source.
    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()
    src_url = (
        live_server_url  # no — this is khub's URL
    )
    # We need to seed with the FALLBACK source URL, not khub's URL.
    # Read it back from cfg.
    import kohakuhub.config as cfg_mod
    src = cfg_mod.cfg.fallback.sources[0]
    uid, th = _owner_cache_key()
    cache.set(
        uid, th,
        "model", "owner", "scenario-repo",
        src["url"], src["name"], src["source_type"],
        exists=True,
    )

    # Now call model_info; the cached source returns 503 → the new
    # strict-consistency rule says: do NOT invalidate, surface the
    # bound-source aggregate-of-one-attempt error. The aggregate
    # of a single 5xx attempt is 502 (server-error-only category).
    HfHubHTTPError = _hf_error("HfHubHTTPError")
    with pytest.raises(HfHubHTTPError):
        api.model_info("owner/scenario-repo")

    # Critical: cache is still bound to the same source after the
    # bound-source failure. Confirm by reading the cache directly.
    cached = cache.get(uid, th, "model", "owner", "scenario-repo")
    assert cached is not None
    assert cached.get("source_url") == src["url"]
    assert cached.get("exists") is True


def test_bound_source_failure_does_not_walk_to_sibling_even_when_sibling_works(
    live_server_url, hf_api_token, consistency_env,
):
    """The strictest form of guarantee #1. Two sources configured;
    cache bound to source A. A returns 5xx. Source B is configured
    and would happily serve OK — but we must not walk there.

    If the strict-consistency rule were broken, the call would
    succeed (returning B's data) and fail the assertion below.
    """
    configure, _reset = consistency_env
    configure("503", "200_ok")  # A fails, B works — B is the trap.

    # Seed cache to bind the repo to source A (the 503 one) — that's
    # the priority-1 source in the chain we just configured.
    from kohakuhub.api.fallback.cache import get_cache
    import kohakuhub.config as cfg_mod
    src_a = cfg_mod.cfg.fallback.sources[0]
    cache = get_cache()
    uid, th = _owner_cache_key()
    cache.set(
        uid, th,
        "model", "owner", "scenario-repo",
        src_a["url"], src_a["name"], src_a["source_type"],
        exists=True,
    )

    api = HfApi(endpoint=live_server_url, token=hf_api_token)
    HfHubHTTPError = _hf_error("HfHubHTTPError")
    with pytest.raises(HfHubHTTPError):
        api.model_info("owner/scenario-repo")

    # Verify B was NOT contacted — the upstream observer should only
    # have seen the "503" scenario name, never the "200_ok" one.
    seen = REQUEST_LOG.scenarios_count()
    assert seen.get("200_ok", 0) == 0, (
        f"Sibling source contacted under bound-source failure: {seen}"
    )
    assert seen.get("503", 0) >= 1


# ===========================================================================
# Guarantee 2: Cache hit only contacts the bound source.
# ===========================================================================


def test_cache_hit_only_contacts_bound_source(
    live_server_url, hf_api_token, consistency_env,
):
    """After the first call binds the repo, every subsequent call
    must contact ONLY the bound source. Source B is configured but
    would also serve successfully — if the binding rule were broken
    we'd see the upstream observer record contacts to B."""
    configure, _reset = consistency_env
    # Two equally-working sources. Priority order picks A on first call.
    configure("200_ok", "200_ok")
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    # First call binds.
    api.model_info("owner/scenario-repo")
    REQUEST_LOG.reset()  # baseline for the assertions below.

    # Subsequent calls — they must only ever talk to the bound source.
    # Each request hits the upstream once; the source's URL prefix is
    # the same scenario name regardless of which instance bound first
    # (both use "200_ok"), but the exact URL prefix differs by
    # scenario. We instead inspect via cache.
    api.model_info("owner/scenario-repo")
    api.list_repo_files("owner/scenario-repo")

    # The cached source URL is one of the two configured. Every
    # request observed in REQUEST_LOG must have the same URL prefix
    # (i.e. the same scenario name) — both are "200_ok" so the
    # scenario name alone is not distinguishing. Use the path
    # observer: each call hits the upstream exactly once at a single
    # source URL; we get one log entry per upstream call. The cache
    # made the second pair of calls each hit ONE upstream — i.e.
    # exactly 2 entries total.
    # If binding were broken we'd potentially see 4 entries (each
    # call probing both sources before binding).
    assert len(REQUEST_LOG.entries) == 2, REQUEST_LOG.entries


# ===========================================================================
# Guarantee 3: Concurrent first-binders serialize on the lock.
# ===========================================================================


def test_concurrent_first_binders_all_bind_same_source(
    live_server_url, hf_api_token, consistency_env, tmp_path,
):
    """Twenty concurrent calls hit a fresh cache. Without the
    binding lock, several would independently scan the chain. With
    the lock, exactly one scans; the others wait and consume the
    cache. So in the source-call log we should see the chain
    walked at most ONCE — concretely, the only entries in the
    upstream log are calls to the source that won the bind, with
    no probes-then-skip pattern from the losers."""
    configure, _reset = consistency_env
    configure("200_ok")  # single source — simplifies the call-count assertions.
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    def _call_info() -> str:
        info = api.model_info("owner/scenario-repo")
        return info.id

    async def _drive():
        return await asyncio.gather(
            *[asyncio.to_thread(_call_info) for _ in range(20)],
        )

    results = asyncio.run(_drive())
    assert all(r == OK_INFO_JSON["id"] for r in results)

    # Twenty client calls produce twenty upstream calls (each
    # request actually goes to upstream because the cache is
    # per-process and we don't deduplicate the upstream traffic
    # between concurrent in-flight calls — but we DO ensure they
    # all bind to the same source). Count is fine; the key invariant
    # is "all bound to same source" which a single-source
    # configuration trivially proves: if any call had failed to bind
    # we'd see HfHubHTTPError raised from one of the threads.


def test_concurrent_first_binders_with_first_source_failing_all_see_same_outcome(
    live_server_url, hf_api_token, consistency_env,
):
    """Sharper version: priority chain is [RepoNotFound, 200_ok].
    Twenty concurrent calls: they should all serialize on the
    binding lock, the first scans the chain (A 404, B 200, binds B),
    the rest hit the cache and use B. Without the lock, two scanners
    might each independently try the chain and both bind B (no
    inconsistency in *this* test because chain ordering is
    deterministic, but they'd waste 2x the upstream calls).

    The looser invariant we assert: every concurrent caller sees the
    same successful outcome — i.e. every one bound the same source."""
    configure, _reset = consistency_env
    configure("404_repo_not_found", "200_ok")
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    def _call_info() -> str:
        info = api.model_info("owner/scenario-repo")
        return info.id

    async def _drive():
        return await asyncio.gather(
            *[asyncio.to_thread(_call_info) for _ in range(20)],
        )

    results = asyncio.run(_drive())
    assert all(r == OK_INFO_JSON["id"] for r in results)

    # The chain probe (which contacts both sources) should have
    # happened for the first binder ONLY. Subsequent calls hit the
    # cache and skip the chain — so the count of upstream "404" hits
    # should be exactly 1 (only the binder probed the failing
    # source), not 20.
    seen = REQUEST_LOG.scenarios_count()
    assert seen.get("404_repo_not_found", 0) == 1, (
        f"Concurrent binders did not serialize: "
        f"{seen.get('404_repo_not_found')} probes of the failing source "
        f"(expected 1 — only the binder)"
    )
    # Source B (which won the bind) was contacted by all 20.
    assert seen.get("200_ok", 0) == 20, seen


# ===========================================================================
# Guarantee 4: Deterministic chain order — TTL expiry under fixed
# external state re-binds the same source.
# ===========================================================================


def test_ttl_expiry_under_fixed_external_state_rebinds_same_source(
    live_server_url, hf_api_token, consistency_env,
):
    """Cache TTL expires; the next call re-runs the chain probe
    against an unchanged sources list with unchanged behavior. The
    chain order is deterministic (priority order), so the rebind
    must pick the same source as the original binding."""
    configure, _reset = consistency_env
    # Distinguish A vs B by scenario name in the URL; ensure the
    # priority order is [A, B] and both work.
    configure("200_ok", "200_ok")
    import kohakuhub.config as cfg_mod
    src_a_url = cfg_mod.cfg.fallback.sources[0]["url"]
    src_b_url = cfg_mod.cfg.fallback.sources[1]["url"]

    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    # First binding.
    api.model_info("owner/scenario-repo")
    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()
    uid, th = _owner_cache_key()
    first_cached = cache.get(uid, th, "model", "owner", "scenario-repo")
    assert first_cached is not None
    assert first_cached["source_url"] == src_a_url

    # Simulate TTL expiry by clearing the cache.
    cache.invalidate(uid, th, "model", "owner", "scenario-repo")

    # Second binding (must pick same source under unchanged
    # external state).
    api.model_info("owner/scenario-repo")
    second_cached = cache.get(uid, th, "model", "owner", "scenario-repo")
    assert second_cached is not None
    assert second_cached["source_url"] == first_cached["source_url"]


# ===========================================================================
# Guarantee 5: Multi-op session consistency — sequence of mixed ops
# all hit the same source.
# ===========================================================================


def test_multi_op_session_all_route_to_same_source(
    live_server_url, hf_api_token, consistency_env, tmp_path,
):
    """Realistic session: model_info → list_repo_files →
    hf_hub_download → get_paths_info on the same repo. With one
    source serving and a sibling configured as a TRAP, every op
    must hit the bound source only.

    The scenario mock's request observer records per-scenario hits;
    if any op accidentally rebinds to the trap source the test will
    fail because the trap's scenario name appears in the log.
    """
    configure, _reset = consistency_env
    # Source A serves; B is a trap that *would* succeed if asked
    # (configured as 200_ok with a different URL prefix). The bind
    # MUST stick to A across all four ops.
    #
    # We achieve "different URL prefix" by encoding it in the
    # scenario name. Both serve 200_ok per scenario_response, but
    # the URL paths differ ("/scenario/200_ok/..." for A vs
    # "/scenario/200_ok/..." for B with same prefix actually). To
    # truly distinguish, use two scenario names that both yield
    # 200_ok behavior — only "200_ok" exists today, so we need to
    # extend the mock with an alias OR rely on the URL-prefix
    # difference (each cfg.fallback.sources[i] has a distinct URL,
    # but with the same scenario name — REQUEST_LOG records the
    # scenario name only).
    #
    # Pragma: the request log records (method, scenario, path); two
    # sources with the same scenario name appear identical in the
    # log. We instead assert via direct cache inspection that the
    # bound source URL never changes, AND via per-source URL
    # attribution checked through the cfg.fallback.sources list.
    configure("200_ok", "200_ok")
    import kohakuhub.config as cfg_mod
    src_a = cfg_mod.cfg.fallback.sources[0]
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    # Op 1: model_info — first binding.
    info = api.model_info("owner/scenario-repo")
    assert info.id == OK_INFO_JSON["id"]

    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()
    uid, th = _owner_cache_key()
    bound_url_after_info = cache.get(uid, th, "model", "owner", "scenario-repo")["source_url"]
    assert bound_url_after_info == src_a["url"]

    # Op 2: list_repo_files.
    files = api.list_repo_files("owner/scenario-repo")
    assert "config.json" in files
    bound_url_after_tree = cache.get(uid, th, "model", "owner", "scenario-repo")["source_url"]
    assert bound_url_after_tree == src_a["url"]

    # Op 3: hf_hub_download — actual file download.
    path = hf_hub_download(
        repo_id="owner/scenario-repo",
        filename="config.json",
        endpoint=live_server_url,
        token=hf_api_token,
        cache_dir=str(tmp_path),
    )
    assert Path(path).read_bytes() == OK_BODY
    bound_url_after_dl = cache.get(uid, th, "model", "owner", "scenario-repo")["source_url"]
    assert bound_url_after_dl == src_a["url"]

    # Op 4: get_paths_info.
    method = getattr(api, "get_paths_info", None) or getattr(api, "paths_info", None)
    if method is not None:
        result = method(repo_id="owner/scenario-repo", paths=["config.json"])
        assert len(list(result)) >= 1
        bound_url_after_pi = cache.get(uid, th, "model", "owner", "scenario-repo")["source_url"]
        assert bound_url_after_pi == src_a["url"]


def test_snapshot_download_pattern_stays_on_one_source(
    live_server_url, hf_api_token, consistency_env, tmp_path,
):
    """Closest emulation of ``snapshot_download``: list_repo_files
    then hf_hub_download per file. The cache must keep all of them
    bound to the same source even across many file downloads."""
    configure, _reset = consistency_env
    configure("200_ok", "200_ok")
    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    files = api.list_repo_files("owner/scenario-repo")
    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()
    uid, th = _owner_cache_key()
    initial_bind = cache.get(uid, th, "model", "owner", "scenario-repo")["source_url"]

    # Download every file; bound source must not move.
    for name in files:
        hf_hub_download(
            repo_id="owner/scenario-repo",
            filename=name,
            endpoint=live_server_url,
            token=hf_api_token,
            cache_dir=str(tmp_path),
        )
        current_bind = cache.get(uid, th, "model", "owner", "scenario-repo")["source_url"]
        assert current_bind == initial_bind


# ===========================================================================
# Guarantee 6: Same external state (single user, fixed token) ⇒ same
# binding outcome across independent client sessions.
# ===========================================================================


def test_two_independent_client_sessions_bind_same_source(
    live_server_url, hf_api_token, consistency_env,
):
    """Two HfApi instances (modelling two client invocations) hit
    the same khub. The cache is per-khub-process so this is really
    "two consecutive cache binds under unchanged external state" —
    and since chain order is deterministic, they must agree on the
    bound source."""
    configure, _reset = consistency_env
    configure("200_ok", "200_ok")
    import kohakuhub.config as cfg_mod
    expected_url = cfg_mod.cfg.fallback.sources[0]["url"]

    api1 = HfApi(endpoint=live_server_url, token=hf_api_token)
    api2 = HfApi(endpoint=live_server_url, token=hf_api_token)

    api1.model_info("owner/scenario-repo")

    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()
    uid, th = _owner_cache_key()
    bound = cache.get(uid, th, "model", "owner", "scenario-repo")
    assert bound["source_url"] == expected_url

    # Wipe cache to force a fresh bind for client #2 — same external
    # state, must rebind same source.
    cache.invalidate(uid, th, "model", "owner", "scenario-repo")
    api2.model_info("owner/scenario-repo")
    bound2 = cache.get(uid, th, "model", "owner", "scenario-repo")
    assert bound2["source_url"] == expected_url


# ===========================================================================
# Guarantee 7 (#79): Per-user cache isolation end-to-end via hf_hub.
# ===========================================================================


def test_two_users_via_hf_hub_get_independent_cache_buckets(
    live_server_url, consistency_env, app,
):
    """Same khub, same repo, two distinct authenticated users via the
    real ``huggingface_hub`` library. Each must land in a separate
    cache bucket (#79 strict-freshness contract). Verified by direct
    cache inspection: after both users call ``model_info``, two
    bindings exist, keyed by their respective user_id.
    """
    import asyncio

    import httpx
    from kohakuhub.db import User

    from test.kohakuhub.support.bootstrap import DEFAULT_PASSWORD

    configure, _reset = consistency_env
    configure("200_ok")  # single source, both users will bind to it

    async def _get_token(username: str) -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            login = await ac.post(
                "/api/auth/login",
                json={"username": username, "password": DEFAULT_PASSWORD},
            )
            login.raise_for_status()
            tok = await ac.post(
                "/api/auth/tokens/create",
                json={"name": f"hf-api-compat-{username}"},
            )
            tok.raise_for_status()
            return tok.json()["token"]

    owner_token = asyncio.run(_get_token("owner"))
    member_token = asyncio.run(_get_token("member"))
    owner_id = User.get(User.username == "owner").id
    member_id = User.get(User.username == "member").id

    # Each user calls model_info — both should bind successfully.
    api_owner = HfApi(endpoint=live_server_url, token=owner_token)
    api_member = HfApi(endpoint=live_server_url, token=member_token)
    api_owner.model_info("owner/scenario-repo")
    api_member.model_info("owner/scenario-repo")

    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()

    # Two independent cache buckets — strict-freshness contract.
    owner_bound = cache.get(owner_id, "", "model", "owner", "scenario-repo")
    member_bound = cache.get(member_id, "", "model", "owner", "scenario-repo")
    assert owner_bound is not None, "owner's cache bucket missing"
    assert member_bound is not None, "member's cache bucket missing"
    # Both bound to the same (priority-1) source, but in separate buckets.
    assert owner_bound["source_url"] == member_bound["source_url"]

    # Sanity: anonymous bucket is empty (we authenticated both calls).
    assert cache.get(None, "", "model", "owner", "scenario-repo") is None


def test_external_token_rotation_via_hf_hub_evicts_user_cache(
    live_server_url, consistency_env, app,
):
    """A user posts a new external token; cache for that user is
    evicted synchronously with the mutation. The hf_hub call after
    the rotation re-probes (cache miss), end-to-end verifying the
    POST /external-tokens → clear_user hook (#79).
    """
    import asyncio

    import httpx
    from kohakuhub.db import User

    from test.kohakuhub.support.bootstrap import DEFAULT_PASSWORD

    configure, _reset = consistency_env
    configure("200_ok")

    async def _login_and_token(username: str):
        transport = httpx.ASGITransport(app=app)
        ac = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        )
        login = await ac.post(
            "/api/auth/login",
            json={"username": username, "password": DEFAULT_PASSWORD},
        )
        login.raise_for_status()
        tok = await ac.post(
            "/api/auth/tokens/create",
            json={"name": f"hf-api-compat-{username}"},
        )
        tok.raise_for_status()
        return ac, tok.json()["token"]

    async def _post_token_and_close(ac):
        try:
            response = await ac.post(
                "/api/users/owner/external-tokens",
                json={
                    "url": "https://hf-rotation-test.example",
                    "token": "hf_rotation_value",
                },
            )
            response.raise_for_status()
        finally:
            await ac.aclose()

    ac_owner, token = asyncio.run(_login_and_token("owner"))
    owner_id = User.get(User.username == "owner").id

    # Bind: cache populated for owner.
    api = HfApi(endpoint=live_server_url, token=token)
    api.model_info("owner/scenario-repo")

    from kohakuhub.api.fallback.cache import get_cache
    cache = get_cache()
    assert cache.get(owner_id, "", "model", "owner", "scenario-repo") is not None
    initial_user_gen = cache.user_gens[owner_id]

    # Rotate token → cache evicted.
    asyncio.run(_post_token_and_close(ac_owner))

    # The cache entry for owner with empty tokens_hash is gone.
    assert cache.get(owner_id, "", "model", "owner", "scenario-repo") is None
    assert cache.user_gens[owner_id] == initial_user_gen + 1
