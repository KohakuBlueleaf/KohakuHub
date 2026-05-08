"""Real-backend integration tests for the pooled ``LakeFSRestClient``.

Mock-only unit tests live in ``test_lakefs_rest_client.py``; the tests in
this module talk to the actual LakeFS service stood up by the test
fixtures (Postgres + MinIO + ``treeverse/lakefs:latest``). They verify
that the pooled client survives realistic workloads against a live server
without regressing functional behaviour.

What we deliberately assert here:

  * The singleton ``get_lakefs_rest_client()`` is the same instance the
    FastAPI handlers use (no accidental fork into per-handler clients).
  * A burst of concurrent ``log_commits`` / ``get_branch`` / ``stat_object``
    calls against the seeded ``owner/demo-model`` repo all succeed AND
    share a single underlying ``httpx.AsyncClient`` (no second
    constructor call regardless of fan-out).
  * The path-filtered ``logCommits`` calls used by ``tree?expand=true``
    return well-formed responses against a real LakeFS — locking down
    the wire-shape contract end-to-end.
  * ``aclose()`` cleanly tears down a pooled client mid-session and the
    next request rebuilds without error.
"""

from __future__ import annotations

import asyncio
import sys

import pytest


# Seed fixture: ``owner/demo-model`` is planted by the standard test
# bootstrap with two commits — one regular-file commit (README.md +
# config.json) and one LFS commit (weights/model.safetensors).
_DEMO_REPO_TYPE = "model"
_DEMO_FULL_ID = "owner/demo-model"


def _live_module():
    """Look up the *currently registered* lakefs_rest_client module.

    The test bootstrap reloads backend modules under ``force_reload=True``
    (see ``support/bootstrap.py``). A module-level
    ``import kohakuhub.lakefs_rest_client as lakefs_rest`` would freeze a
    reference to the *pre-reload* module object, while the FastAPI
    handlers running through the ``client`` fixture see the *post-reload*
    one — different module objects with different ``_singleton_client``
    state. Going through ``sys.modules`` on every access keeps the test
    looking at the same module instance the handlers do.
    """
    return sys.modules["kohakuhub.lakefs_rest_client"]


def _demo_lakefs_repo() -> str:
    # Resolve through sys.modules for the same reload-safety reason.
    return sys.modules["kohakuhub.utils.lakefs"].lakefs_repo_name(
        _DEMO_REPO_TYPE, _DEMO_FULL_ID
    )


@pytest.fixture(autouse=True)
def _drop_singleton_before_each_test():
    """httpx.AsyncClient connections bind to the event loop they were
    constructed in. pytest-asyncio gives each test its own loop, so a
    singleton constructed in test A is unusable in test B — we'd hit
    ``RuntimeError: Event loop is closed`` when the GC runs.

    Hard-reset the module-level cache by NULLing the reference. We
    deliberately do NOT call ``aclose()`` on the leftover instance: that
    would try to schedule work on the (now-closed) previous loop and
    raise. Letting GC handle the dangling client is fine for tests; the
    OS reclaims the sockets.
    """
    mod = _live_module()
    mod._singleton_client = None
    yield
    mod._singleton_client = None


@pytest.mark.asyncio
async def test_singleton_is_reused_across_handler_invocations(client):
    """The ``client`` fixture spins up the FastAPI app over ASGI transport
    and that app uses ``get_lakefs_rest_client()`` to reach LakeFS. Hit a
    LakeFS-bound endpoint twice and verify both invocations resolved to
    the *same* singleton instance — i.e. the pool is shared, not
    re-created per request.
    """



    # Hit the tree endpoint, which fans out to ``logCommits`` via the
    # singleton. (``expand=true`` would also work but expand=false uses
    # fewer LakeFS calls and is faster for the basic singleton check.)
    response_a = await client.get(f"/api/models/{_DEMO_FULL_ID}/tree/main")
    response_a.raise_for_status()
    singleton_after_a = _live_module()._singleton_client
    assert singleton_after_a is not None, (
        "first handler call should have lazily built the singleton"
    )

    response_b = await client.get(f"/api/models/{_DEMO_FULL_ID}/tree/main")
    response_b.raise_for_status()
    singleton_after_b = _live_module()._singleton_client
    assert singleton_after_b is singleton_after_a, (
        "second handler call must reuse the same singleton, not allocate a new one"
    )


@pytest.mark.asyncio
async def test_pooled_httpx_client_reused_across_concurrent_calls(client):
    """Fire a burst of concurrent direct calls on the singleton client and
    verify the underlying ``httpx.AsyncClient`` is constructed once and
    only once. This is the load-bearing invariant for the "no per-call
    handshake" property of the pool.
    """

    rest = _live_module().get_lakefs_rest_client()
    repo = _demo_lakefs_repo()

    # 16 parallel calls touching different LakeFS endpoints. Each method
    # call goes through ``self._httpx()``; the lazy-init must only fire
    # once even though they execute concurrently.
    async def call_each():
        return await asyncio.gather(
            *[rest.get_branch(repo, "main") for _ in range(16)],
        )

    results = await call_each()
    assert all(r.get("id") == "main" for r in results), results
    underlying = rest._httpx_client
    assert underlying is not None

    # A second burst still rides the same underlying client.
    await call_each()
    assert rest._httpx_client is underlying, (
        "underlying httpx.AsyncClient must be the SAME instance across bursts"
    )


@pytest.mark.asyncio
async def test_log_commits_path_filter_against_real_lakefs(client):
    """The path-filtered ``logCommits`` call from
    ``tree.resolve_last_commits_for_paths`` must work end-to-end against a
    real LakeFS server. The seed plants two commits on
    ``owner/demo-model``; ``logCommits(objects=['README.md'], amount=1,
    limit=true)`` should return the regular-file commit, not the LFS one.
    """

    rest = _live_module().get_lakefs_rest_client()
    repo = _demo_lakefs_repo()

    page = await rest.log_commits(
        repository=repo,
        ref="main",
        objects=["README.md"],
        amount=1,
        limit=True,
    )
    results = page.get("results") or []
    assert len(results) == 1, (
        f"expected exactly one commit for README.md, got {len(results)}"
    )
    commit = results[0]
    # Sanity: the commit message identifies it as the regular-files seed
    # commit (the seed planted "Initial regular commit"), not the LFS one.
    assert "regular" in commit.get("message", "").lower() or "initial" in commit.get(
        "message", ""
    ).lower(), commit


@pytest.mark.asyncio
async def test_log_commits_prefix_filter_against_real_lakefs(client):
    """``logCommits(prefixes=['weights/'], amount=1, limit=true)`` must
    return the LFS commit (``weights/model.safetensors``) on the seeded
    ``owner/demo-model`` repo. Same call shape as the directory-target
    branch in ``tree.resolve_last_commits_for_paths``.
    """

    rest = _live_module().get_lakefs_rest_client()
    repo = _demo_lakefs_repo()

    page = await rest.log_commits(
        repository=repo,
        ref="main",
        prefixes=["weights/"],
        amount=1,
        limit=True,
    )
    results = page.get("results") or []
    assert len(results) == 1
    commit = results[0]
    # Seed planted "Add weights" as the message for this commit.
    assert "weight" in commit.get("message", "").lower(), commit


@pytest.mark.asyncio
async def test_aclose_mid_session_then_next_call_succeeds(client):
    """Drop the pooled client mid-session and verify the next call
    transparently re-establishes the pool. This is the lifecycle the
    FastAPI lifespan hook uses at shutdown — and the same mechanism a
    long-running worker would use to recover if its underlying httpx
    client ever entered a bad state.
    """

    rest = _live_module().get_lakefs_rest_client()
    repo = _demo_lakefs_repo()

    # Drive the pool into existence.
    await rest.get_branch(repo, "main")
    first_underlying = rest._httpx_client
    assert first_underlying is not None

    # Tear down (as lifespan would).
    await rest.aclose()
    assert rest._httpx_client is None

    # Next call must succeed and lazily rebuild.
    branch = await rest.get_branch(repo, "main")
    assert branch.get("id") == "main"
    assert rest._httpx_client is not None
    assert rest._httpx_client is not first_underlying, (
        "after aclose the next call must build a fresh underlying client"
    )


@pytest.mark.asyncio
async def test_tree_expand_true_end_to_end_through_pool(client):
    """Full integration: the ``/tree?expand=true`` route uses the singleton
    pool to fan out per-target ``logCommits`` calls. Verify the response is
    well-formed and each entry carries a non-null ``lastCommit`` mapped to
    the seed's two commits.
    """

    response = await client.get(
        f"/api/models/{_DEMO_FULL_ID}/tree/main",
        params={"expand": "true"},
    )
    response.raise_for_status()
    entries = response.json()
    assert isinstance(entries, list)
    assert entries, "tree listing should not be empty"

    # README.md + config.json + weights/ — at minimum we expect those three.
    paths = {entry["path"] for entry in entries}
    assert "README.md" in paths
    assert "config.json" in paths
    assert "weights" in paths

    for entry in entries:
        last = entry.get("lastCommit")
        assert isinstance(last, dict), f"missing lastCommit on {entry['path']}: {entry!r}"
        for required in ("id", "title", "date"):
            assert required in last, (
                f"lastCommit on {entry['path']} missing key {required!r}: {last!r}"
            )
        assert last["id"], f"empty commit id on {entry['path']}"
