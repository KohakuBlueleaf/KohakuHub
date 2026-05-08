"""Tests for the bulk-replace + simulate admin endpoints (#78 v2).

Two endpoints under test here:

- ``PUT  /admin/api/fallback/sources-bulk-replace``: drives the chain
  tester's "Push to system" button. Atomic transactional replace of
  every ``FallbackSource`` row plus a ``cache.clear()`` (bumps
  ``global_gen``).

- ``POST /admin/api/fallback/test/simulate``: drives the tester's
  "Run simulate" button. Single unified endpoint — accepts the full
  input set (op + params, draft sources, user identity, per-URL token
  overlay) and returns a ``ProbeReport`` with a ``local`` hop first
  (real handler via ``__wrapped__``) plus any fallback hops the chain
  walked. Pure read; never writes the production cache or holds the
  binding lock.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# bulk-replace
# ---------------------------------------------------------------------------


async def test_bulk_replace_swaps_entire_source_list(
    admin_client, backend_test_state
):
    """Add three rows in one shot, then swap to a different two rows.

    Confirms ``before`` / ``after`` counts and that intermediate state
    is not visible (atomic transactional swap).
    """
    cache = backend_test_state.modules.fallback_cache_module.get_cache()
    initial_global_gen = cache.global_gen

    # First swap: 3 rows.
    r1 = await admin_client.put(
        "/admin/api/fallback/sources-bulk-replace",
        json={
            "sources": [
                {
                    "namespace": "", "url": "https://a.example/",
                    "token": None, "priority": 10, "name": "A",
                    "source_type": "huggingface", "enabled": True,
                },
                {
                    "namespace": "", "url": "https://b.example/",
                    "token": None, "priority": 20, "name": "B",
                    "source_type": "huggingface", "enabled": True,
                },
                {
                    "namespace": "ns1", "url": "https://c.example/",
                    "token": None, "priority": 30, "name": "C",
                    "source_type": "kohakuhub", "enabled": False,
                },
            ],
        },
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["success"] is True
    assert body1["after"] == 3
    # Cache cleared, global_gen bumped.
    assert cache.global_gen == initial_global_gen + 1

    list_r = await admin_client.get("/admin/api/fallback-sources")
    assert {s["name"] for s in list_r.json()} == {"A", "B", "C"}

    # Second swap: 2 entirely-different rows.
    r2 = await admin_client.put(
        "/admin/api/fallback/sources-bulk-replace",
        json={
            "sources": [
                {
                    "namespace": "", "url": "https://d.example",
                    "priority": 5, "name": "D",
                    "source_type": "huggingface", "enabled": True,
                },
                {
                    "namespace": "", "url": "https://e.example",
                    "priority": 6, "name": "E",
                    "source_type": "huggingface", "enabled": True,
                },
            ],
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["before"] == 3
    assert body2["after"] == 2
    assert cache.global_gen == initial_global_gen + 2

    final_list = await admin_client.get("/admin/api/fallback-sources")
    assert {s["name"] for s in final_list.json()} == {"D", "E"}


async def test_bulk_replace_with_empty_list_clears_table(
    admin_client, backend_test_state
):
    # Seed one row first.
    seed = await admin_client.post(
        "/admin/api/fallback-sources",
        json={
            "namespace": "", "url": "https://seed.example",
            "name": "Seed", "source_type": "huggingface",
            "priority": 1, "enabled": True,
        },
    )
    assert seed.status_code == 200

    response = await admin_client.put(
        "/admin/api/fallback/sources-bulk-replace",
        json={"sources": []},
    )
    assert response.status_code == 200
    assert response.json()["after"] == 0


async def test_bulk_replace_rejects_invalid_source_type(admin_client):
    response = await admin_client.put(
        "/admin/api/fallback/sources-bulk-replace",
        json={
            "sources": [
                {
                    "namespace": "", "url": "https://x.example",
                    "name": "X", "source_type": "totally-bogus",
                    "priority": 1, "enabled": True,
                },
            ],
        },
    )
    assert response.status_code == 400
    assert "Invalid source_type" in response.json()["detail"]["error"]


async def test_bulk_replace_requires_admin_token(client):
    response = await client.put(
        "/admin/api/fallback/sources-bulk-replace",
        json={"sources": []},
    )
    assert response.status_code in (401, 403)


async def test_bulk_replace_500_path(
    admin_client, monkeypatch, backend_test_state
):
    """Force the inner FallbackSource.create to raise so we hit the 500
    catch-all that builds a friendly error envelope."""
    from kohakuhub.db import FallbackSource

    def boom(*_a, **_k):
        raise RuntimeError("synthetic db error")

    monkeypatch.setattr(FallbackSource, "create", boom)

    response = await admin_client.put(
        "/admin/api/fallback/sources-bulk-replace",
        json={
            "sources": [
                {
                    "namespace": "", "url": "https://x.example",
                    "name": "X", "source_type": "huggingface",
                    "priority": 1, "enabled": True,
                },
            ],
        },
    )
    assert response.status_code == 500
    assert "Bulk-replace failed" in response.json()["detail"]["error"]


# ---------------------------------------------------------------------------
# /admin/api/fallback/test/simulate
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_probe_chain(monkeypatch):
    """Replace the fallback-chain probe with a deterministic stub.

    Tests targeting the routing layer (op validation, payload
    plumbing, identity / token resolution) shouldn't bring real HTTP
    traffic into the test runner — the chain probe is exercised
    against real upstreams in ``test_real_hf_hub_end_to_end.py``.
    """
    calls: list[tuple] = []

    async def _stub(*args, **kwargs):
        calls.append((args, kwargs))
        from kohakuhub.api.fallback.core import ProbeAttempt, ProbeReport

        return ProbeReport(
            op=kwargs.get("op", "info"),
            repo_id=f"{kwargs.get('namespace', 'x')}/{kwargs.get('name', 'y')}",
            revision=kwargs.get("revision"),
            file_path=kwargs.get("file_path") or None,
            attempts=[
                ProbeAttempt(
                    source_name="A", source_url="https://a.local",
                    source_type="huggingface", method="GET",
                    upstream_path="/api/models/x/y",
                    status_code=200, x_error_code=None, x_error_message=None,
                    decision="BIND_AND_RESPOND", duration_ms=12, error=None,
                    response_body_preview="{}", response_headers={},
                ),
            ],
            final_outcome="BIND_AND_RESPOND",
            bound_source={"name": "A", "url": "https://a.local"},
            duration_ms=15,
            final_response={"status_code": 200, "headers": {}, "body_preview": "{}"},
        )

    monkeypatch.setattr(
        "kohakuhub.api.admin.routers.fallback.probe_full_chain",
        _stub,
    )
    return calls


async def test_simulate_runs_local_first_then_fallback_on_miss(
    admin_client, stub_probe_chain
):
    """The endpoint hands a fully-resolved (sources, user, op) tuple
    through to ``probe_full_chain``. Verify the routing-layer
    plumbing — namespace, sources list, header-token overlay — is
    forwarded correctly. The deterministic stub above lets us
    assert the wiring without depending on upstream HTTP."""
    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info",
            "repo_type": "model",
            "namespace": "owner",
            "name": "demo",
            "revision": "main",
            "sources": [
                {
                    "name": "A", "url": "https://a.example",
                    "source_type": "huggingface",
                    "token": "admin-A", "priority": 10,
                },
                {
                    "name": "B", "url": "https://b.example",
                    "source_type": "huggingface",
                    "token": None, "priority": 20,
                },
            ],
            "header_tokens": {"https://a.example": "user-overrides-A"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["final_outcome"] == "BIND_AND_RESPOND"

    assert len(stub_probe_chain) == 1
    _args, kwargs = stub_probe_chain[0]
    assert kwargs["op"] == "info"
    sources = kwargs["sources"]
    # Header token wins over admin-configured token for source A.
    a = next(s for s in sources if s["url"] == "https://a.example")
    assert a["token"] == "user-overrides-A"
    assert a.get("token_source") == "user"
    # Source B untouched.
    b = next(s for s in sources if s["url"] == "https://b.example")
    assert b.get("token") is None


async def test_simulate_resolves_username_to_real_user(
    admin_client, stub_probe_chain, backend_test_state
):
    """``as_username`` must resolve through ``get_user_by_username``
    and be threaded into ``probe_full_chain`` as the ``user`` arg —
    that's what enables the real handler's permission check to run
    as that identity."""
    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info",
            "repo_type": "model",
            "namespace": "owner",
            "name": "demo",
            "sources": [],
            "as_username": "owner",
        },
    )
    assert response.status_code == 200
    _args, kwargs = stub_probe_chain[0]
    user = kwargs["user"]
    # The seeded ``owner`` user from the conftest baseline.
    assert user is not None
    assert user.username == "owner"


async def test_simulate_anonymous_when_username_unknown(
    admin_client, stub_probe_chain
):
    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info",
            "repo_type": "model",
            "namespace": "owner",
            "name": "demo",
            "sources": [],
            "as_username": "this-user-does-not-exist",
        },
    )
    assert response.status_code == 200
    _args, kwargs = stub_probe_chain[0]
    assert kwargs["user"] is None


async def test_simulate_user_id_resolution_path(
    admin_client, stub_probe_chain, backend_test_state
):
    """Same as the username path but via ``as_user_id`` —
    impersonates by integer PK rather than by name."""
    from kohakuhub.db_operations import get_user_by_username

    owner = get_user_by_username("owner")
    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info",
            "repo_type": "model",
            "namespace": "owner",
            "name": "demo",
            "sources": [],
            "as_user_id": owner.id,
        },
    )
    assert response.status_code == 200
    _args, kwargs = stub_probe_chain[0]
    assert kwargs["user"] is not None
    assert kwargs["user"].id == owner.id


async def test_simulate_response_annotates_attempt_kind(
    admin_client, stub_probe_chain
):
    """The endpoint annotates each attempt with ``kind`` so the UI
    can distinguish local vs fallback hops on the same timeline.
    The stub returns a fallback-shaped attempt (``source_type="huggingface"``);
    the endpoint should tag it ``kind="fallback"``."""
    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info",
            "repo_type": "model",
            "namespace": "owner",
            "name": "demo",
            "sources": [],
        },
    )
    body = response.json()
    assert all("kind" in a for a in body["attempts"])
    assert body["attempts"][0]["kind"] == "fallback"


async def test_simulate_rejects_unsupported_op(admin_client, stub_probe_chain):
    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "totally-bogus", "repo_type": "model",
            "namespace": "owner", "name": "demo",
            "sources": [],
        },
    )
    assert response.status_code == 400
    assert "Unsupported op" in response.json()["detail"]["error"]
    assert stub_probe_chain == []


async def test_simulate_500_when_probe_chain_raises(admin_client, monkeypatch):
    async def explode(*_a, **_k):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(
        "kohakuhub.api.admin.routers.fallback.probe_full_chain", explode
    )

    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info", "repo_type": "model",
            "namespace": "owner", "name": "demo",
            "sources": [],
        },
    )
    assert response.status_code == 500
    assert "Simulate probe failed" in response.json()["detail"]["error"]


async def test_simulate_400_on_value_error_from_probe(admin_client, monkeypatch):
    async def value_err(*_a, **_k):
        raise ValueError("bad inputs")

    monkeypatch.setattr(
        "kohakuhub.api.admin.routers.fallback.probe_full_chain", value_err
    )

    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info", "repo_type": "model",
            "namespace": "owner", "name": "demo",
            "sources": [],
        },
    )
    assert response.status_code == 400
    assert "bad inputs" in response.json()["detail"]["error"]


async def test_simulate_requires_admin_token(client):
    response = await client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info", "repo_type": "model",
            "namespace": "owner", "name": "demo",
            "sources": [],
        },
    )
    assert response.status_code in (401, 403)


async def test_simulate_full_local_hit_path_no_stub(
    admin_client, owner_client, backend_test_state
):
    """End-to-end: NO probe_chain stub. The simulate runs the *real*
    ``probe_full_chain`` against an empty source list, which means
    we exercise the real ``probe_local`` (calls the real info
    handler) → expects ``LOCAL_HIT`` for an existing public repo."""
    create = await owner_client.post(
        "/api/repos/create",
        json={"name": "sim-e2e-public", "type": "model", "private": False},
    )
    assert create.status_code == 200

    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "info",
            "repo_type": "model",
            "namespace": "owner",
            "name": "sim-e2e-public",
            "sources": [],  # no fallback — local-only
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["final_outcome"] == "LOCAL_HIT"
    assert body["bound_source"]["name"] == "local"
    # Exactly one hop — the local one. No fallback walked since
    # local hit short-circuits the chain.
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["kind"] == "local"
    assert body["attempts"][0]["decision"] == "LOCAL_HIT"


async def test_simulate_local_filtered_skips_fallback(
    admin_client, owner_client, backend_test_state
):
    """Local repo exists, requested revision doesn't → ``LOCAL_FILTERED``,
    fallback chain MUST NOT walk (strict-consistency rule from PR
    #75/#77). Use a non-empty draft source list to prove the chain
    is skipped — if it ran we'd see a second attempt."""
    create = await owner_client.post(
        "/api/repos/create",
        json={"name": "sim-e2e-tree", "type": "model", "private": False},
    )
    assert create.status_code == 200

    response = await admin_client.post(
        "/admin/api/fallback/test/simulate",
        json={
            "op": "tree",
            "repo_type": "model",
            "namespace": "owner",
            "name": "sim-e2e-tree",
            "revision": "no-such-revision",
            "sources": [
                {
                    "name": "HF", "url": "https://hf.example",
                    "source_type": "huggingface", "priority": 1,
                },
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["final_outcome"] == "LOCAL_FILTERED"
    # Exactly one hop — the local one. The fallback was supplied
    # but the chain MUST NOT walk past LOCAL_FILTERED.
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["kind"] == "local"
    assert body["attempts"][0]["x_error_code"] == "RevisionNotFound"
