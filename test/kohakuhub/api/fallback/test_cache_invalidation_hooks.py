"""API-level coverage of every cache invalidation hook (#79).

Each test seeds the fallback cache with a known entry, calls the
corresponding HTTP endpoint via FastAPI's ASGI transport, and asserts
the cache was evicted (and the relevant generation counter was bumped).

Hooks covered:

- POST   /api/users/{username}/external-tokens          → clear_user
- DELETE /api/users/{username}/external-tokens/{url}    → clear_user
- PUT    /api/users/{username}/external-tokens/bulk     → clear_user
- POST   /api/repos/create                              → invalidate_repo
- DELETE /api/repos/delete                              → invalidate_repo
- POST   /api/repos/move                                → invalidate_repo (both ids)
- PUT    /api/{repo_type}s/{ns}/{name}/settings (private flip) → invalidate_repo
- POST   /admin/api/fallback-sources                    → cache.clear()
- DELETE /admin/api/fallback-sources/cache/repo/...     → invalidate_repo
- DELETE /admin/api/fallback-sources/cache/user/{uid}   → clear_user
"""
from __future__ import annotations

from urllib.parse import quote

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(cache, *, user_id, tokens_hash="", repo_type="model",
          ns="owner", name="demo", url="https://x.local"):
    cache.set(
        user_id, tokens_hash,
        repo_type, ns, name,
        url, "X", "huggingface",
    )


def _get_cache(backend_test_state):
    return backend_test_state.modules.fallback_cache_module.get_cache()


def _get_owner_id(backend_test_state):
    User = backend_test_state.modules.db_module.User
    return User.get(User.username == "owner").id


# ---------------------------------------------------------------------------
# External token endpoints → clear_user
# ---------------------------------------------------------------------------


async def test_external_token_post_clears_user_cache(owner_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    _seed(cache, user_id=owner_id, name="r1")
    _seed(cache, user_id=owner_id, name="r2")
    _seed(cache, user_id=owner_id + 9999, name="r1")  # different user, must survive
    initial_user_gen = cache.user_gens.get(owner_id, 0)

    response = await owner_client.post(
        "/api/users/owner/external-tokens",
        json={"url": "https://post-evict.example", "token": "hf_abc"},
    )
    assert response.status_code == 200

    assert cache.get(owner_id, "", "model", "owner", "r1") is None
    assert cache.get(owner_id, "", "model", "owner", "r2") is None
    assert cache.get(owner_id + 9999, "", "model", "owner", "r1") is not None
    assert cache.user_gens[owner_id] == initial_user_gen + 1


async def test_external_token_delete_clears_user_cache(owner_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    # Need a token to delete first.
    add_response = await owner_client.post(
        "/api/users/owner/external-tokens",
        json={"url": "https://delete-evict.example", "token": "hf_abc"},
    )
    assert add_response.status_code == 200

    _seed(cache, user_id=owner_id, name="r1")
    initial_user_gen = cache.user_gens.get(owner_id, 0)

    delete_response = await owner_client.delete(
        "/api/users/owner/external-tokens/"
        + quote("https://delete-evict.example", safe="")
    )
    assert delete_response.status_code == 200

    assert cache.get(owner_id, "", "model", "owner", "r1") is None
    assert cache.user_gens[owner_id] == initial_user_gen + 1


async def test_external_token_bulk_clears_user_cache(owner_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    _seed(cache, user_id=owner_id, name="r1")
    _seed(cache, user_id=owner_id, name="r2")
    initial_user_gen = cache.user_gens.get(owner_id, 0)

    response = await owner_client.put(
        "/api/users/owner/external-tokens/bulk",
        json={
            "tokens": [
                {"url": "https://bulk-evict.example", "token": "hf_a"},
                {"url": "https://bulk-evict-2.example", "token": "hf_b"},
            ]
        },
    )
    assert response.status_code == 200

    assert cache.get(owner_id, "", "model", "owner", "r1") is None
    assert cache.get(owner_id, "", "model", "owner", "r2") is None
    assert cache.user_gens[owner_id] == initial_user_gen + 1


# ---------------------------------------------------------------------------
# Repo CRUD endpoints → invalidate_repo
# ---------------------------------------------------------------------------


async def test_repo_create_invalidates_repo_cache(owner_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    _seed(cache, user_id=owner_id, name="ghost-create")
    _seed(cache, user_id=None, name="ghost-create")
    _seed(cache, user_id=owner_id, name="other-repo")  # different repo, must survive
    initial_repo_gen = cache.repo_gens.get(("model", "owner", "ghost-create"), 0)

    response = await owner_client.post(
        "/api/repos/create",
        json={"name": "ghost-create", "type": "model", "private": False},
    )
    assert response.status_code == 200, response.text

    assert cache.get(owner_id, "", "model", "owner", "ghost-create") is None
    assert cache.get(None, "", "model", "owner", "ghost-create") is None
    # Different repo — untouched.
    assert cache.get(owner_id, "", "model", "owner", "other-repo") is not None
    assert cache.repo_gens[("model", "owner", "ghost-create")] == initial_repo_gen + 1

    # Cleanup the repo we created.
    await owner_client.request(
        "DELETE",
        "/api/repos/delete",
        json={"name": "ghost-create", "type": "model"},
    )


async def test_repo_delete_invalidates_repo_cache(owner_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    # Create a repo to delete.
    create_response = await owner_client.post(
        "/api/repos/create",
        json={"name": "ghost-delete", "type": "model", "private": False},
    )
    assert create_response.status_code == 200, create_response.text

    # Seed cache AFTER create (create itself bumps the gen).
    _seed(cache, user_id=owner_id, name="ghost-delete")
    _seed(cache, user_id=owner_id + 9999, name="ghost-delete")
    initial_repo_gen = cache.repo_gens.get(("model", "owner", "ghost-delete"), 0)

    delete_response = await owner_client.request(
        "DELETE",
        "/api/repos/delete",
        json={"name": "ghost-delete", "type": "model"},
    )
    assert delete_response.status_code == 200, delete_response.text

    assert cache.get(owner_id, "", "model", "owner", "ghost-delete") is None
    assert cache.get(owner_id + 9999, "", "model", "owner", "ghost-delete") is None
    assert cache.repo_gens[("model", "owner", "ghost-delete")] == initial_repo_gen + 1


async def test_repo_move_invalidates_both_old_and_new_repo_caches(
    owner_client, backend_test_state
):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    create_response = await owner_client.post(
        "/api/repos/create",
        json={"name": "rename-source", "type": "model", "private": False},
    )
    assert create_response.status_code == 200, create_response.text

    # Seed both ids (the move endpoint should evict both).
    _seed(cache, user_id=owner_id, name="rename-source")
    _seed(cache, user_id=owner_id, name="rename-dest")
    initial_src_gen = cache.repo_gens.get(("model", "owner", "rename-source"), 0)
    initial_dst_gen = cache.repo_gens.get(("model", "owner", "rename-dest"), 0)

    move_response = await owner_client.post(
        "/api/repos/move",
        json={
            "fromRepo": "owner/rename-source",
            "toRepo": "owner/rename-dest",
            "type": "model",
        },
    )
    assert move_response.status_code == 200, move_response.text

    assert cache.get(owner_id, "", "model", "owner", "rename-source") is None
    assert cache.get(owner_id, "", "model", "owner", "rename-dest") is None
    assert cache.repo_gens[("model", "owner", "rename-source")] == initial_src_gen + 1
    assert cache.repo_gens[("model", "owner", "rename-dest")] == initial_dst_gen + 1

    # Cleanup
    await owner_client.request(
        "DELETE",
        "/api/repos/delete",
        json={"name": "rename-dest", "type": "model"},
    )


async def test_repo_visibility_toggle_invalidates_repo_cache(
    owner_client, backend_test_state
):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    create_response = await owner_client.post(
        "/api/repos/create",
        json={"name": "visflip", "type": "model", "private": False},
    )
    assert create_response.status_code == 200, create_response.text

    _seed(cache, user_id=owner_id, name="visflip")
    _seed(cache, user_id=None, name="visflip")  # anonymous bucket too
    initial_repo_gen = cache.repo_gens.get(("model", "owner", "visflip"), 0)

    settings_response = await owner_client.put(
        "/api/models/owner/visflip/settings",
        json={"private": True},
    )
    assert settings_response.status_code == 200, settings_response.text

    assert cache.get(owner_id, "", "model", "owner", "visflip") is None
    assert cache.get(None, "", "model", "owner", "visflip") is None
    assert cache.repo_gens[("model", "owner", "visflip")] == initial_repo_gen + 1

    # Cleanup
    await owner_client.request(
        "DELETE",
        "/api/repos/delete",
        json={"name": "visflip", "type": "model"},
    )


async def test_repo_settings_non_visibility_does_not_invalidate(
    owner_client, backend_test_state
):
    """LFS settings, quota, etc. don't affect binding. They must NOT
    fire an invalidate_repo (cache stays warm)."""
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    create_response = await owner_client.post(
        "/api/repos/create",
        json={"name": "lfsonly", "type": "model", "private": False},
    )
    assert create_response.status_code == 200, create_response.text

    _seed(cache, user_id=owner_id, name="lfsonly")
    initial_repo_gen = cache.repo_gens.get(("model", "owner", "lfsonly"), 0)

    # Update only an LFS setting — visibility unchanged.
    settings_response = await owner_client.put(
        "/api/models/owner/lfsonly/settings",
        json={"lfs_threshold_bytes": 50_000_000},
    )
    assert settings_response.status_code == 200, settings_response.text

    # Cache entry survives; no gen bump.
    assert cache.get(owner_id, "", "model", "owner", "lfsonly") is not None
    assert cache.repo_gens[("model", "owner", "lfsonly")] == initial_repo_gen

    # Cleanup
    await owner_client.request(
        "DELETE",
        "/api/repos/delete",
        json={"name": "lfsonly", "type": "model"},
    )


# ---------------------------------------------------------------------------
# Admin source CREATE → cache.clear() (parallels existing UPDATE/DELETE)
# ---------------------------------------------------------------------------


async def test_admin_source_create_clears_global_cache(
    admin_client, backend_test_state
):
    cache = _get_cache(backend_test_state)
    cache.clear()  # baseline
    _seed(cache, user_id=1, name="r1")
    _seed(cache, user_id=2, name="r2")
    initial_global_gen = cache.global_gen

    create_response = await admin_client.post(
        "/admin/api/fallback-sources",
        json={
            "namespace": "",
            "url": "https://create-clear.example",
            "name": "CreateClear",
            "source_type": "huggingface",
            "priority": 50,
            "enabled": True,
        },
    )
    assert create_response.status_code == 200, create_response.text

    # Cache wiped; global_gen bumped.
    assert cache.stats()["size"] == 0
    assert cache.global_gen == initial_global_gen + 1

    # Cleanup the source we created.
    created_id = create_response.json()["id"]
    delete_response = await admin_client.delete(
        f"/admin/api/fallback-sources/{created_id}"
    )
    assert delete_response.status_code == 200


# ---------------------------------------------------------------------------
# New admin endpoints: per-repo + per-user
# ---------------------------------------------------------------------------


async def test_admin_invalidate_repo_endpoint(admin_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    _seed(cache, user_id=1, name="target")
    _seed(cache, user_id=2, name="target")
    _seed(cache, user_id=1, name="other")
    initial_target_gen = cache.repo_gens.get(("model", "owner", "target"), 0)

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/repo/model/owner/target"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["evicted"] == 2

    assert cache.get(1, "", "model", "owner", "target") is None
    assert cache.get(2, "", "model", "owner", "target") is None
    assert cache.get(1, "", "model", "owner", "other") is not None
    assert cache.repo_gens[("model", "owner", "target")] == initial_target_gen + 1


async def test_admin_invalidate_user_endpoint(admin_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    _seed(cache, user_id=42, name="r1")
    _seed(cache, user_id=42, name="r2")
    _seed(cache, user_id=99, name="r1")
    initial_user_gen = cache.user_gens.get(42, 0)

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/user/42"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["evicted"] == 2

    assert cache.get(42, "", "model", "owner", "r1") is None
    assert cache.get(42, "", "model", "owner", "r2") is None
    assert cache.get(99, "", "model", "owner", "r1") is not None
    assert cache.user_gens[42] == initial_user_gen + 1


async def test_admin_invalidate_endpoints_require_admin_token(client):
    """Anonymous calls to admin endpoints should be rejected."""
    repo_response = await client.delete(
        "/admin/api/fallback-sources/cache/repo/model/owner/target"
    )
    assert repo_response.status_code in (401, 403)

    user_response = await client.delete(
        "/admin/api/fallback-sources/cache/user/42"
    )
    assert user_response.status_code in (401, 403)


async def test_admin_invalidate_repo_with_no_entries(admin_client, backend_test_state):
    cache = _get_cache(backend_test_state)
    cache.clear()
    initial_gen = cache.repo_gens.get(("model", "ghost", "ghost"), 0)

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/repo/model/ghost/ghost"
    )
    assert response.status_code == 200
    assert response.json()["evicted"] == 0
    # Gen still bumps (race protection).
    assert cache.repo_gens[("model", "ghost", "ghost")] == initial_gen + 1


# ---------------------------------------------------------------------------
# Exception path coverage on the two new admin endpoints.
# ---------------------------------------------------------------------------


async def test_admin_invalidate_repo_endpoint_500_on_internal_error(
    admin_client, backend_test_state, monkeypatch
):
    """Inject a failure into ``invalidate_repo`` to exercise the
    500-handler path on the new admin endpoint."""
    cache = _get_cache(backend_test_state)

    def boom(*_a, **_k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(cache, "invalidate_repo", boom)

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/repo/model/owner/exception-path"
    )
    assert response.status_code == 500
    assert "Failed to invalidate repo cache" in response.json()["detail"]["error"]


async def test_admin_invalidate_user_endpoint_500_on_internal_error(
    admin_client, backend_test_state, monkeypatch
):
    cache = _get_cache(backend_test_state)

    def boom(*_a, **_k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(cache, "clear_user", boom)

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/user/12345"
    )
    assert response.status_code == 500
    assert "Failed to invalidate user cache" in response.json()["detail"]["error"]


# ---------------------------------------------------------------------------
# Username-based eviction endpoint (admin-frontend convenience path)
# ---------------------------------------------------------------------------


async def test_admin_invalidate_username_endpoint_success(
    admin_client, backend_test_state
):
    cache = _get_cache(backend_test_state)
    cache.clear()
    owner_id = _get_owner_id(backend_test_state)

    _seed(cache, user_id=owner_id, name="r1")
    _seed(cache, user_id=owner_id, name="r2")
    _seed(cache, user_id=owner_id + 9999, name="r1")  # different user, must survive

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/username/owner"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["evicted"] == 2
    assert body["username"] == "owner"
    assert body["user_id"] == owner_id

    # owner buckets gone, other user's bucket survives.
    assert cache.get(owner_id, "", "model", "owner", "r1") is None
    assert cache.get(owner_id, "", "model", "owner", "r2") is None
    assert cache.get(owner_id + 9999, "", "model", "owner", "r1") is not None


async def test_admin_invalidate_username_endpoint_404_unknown_user(
    admin_client,
):
    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/username/no-such-user-zzz"
    )
    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]["error"]


async def test_admin_invalidate_username_endpoint_requires_admin_token(client):
    response = await client.delete(
        "/admin/api/fallback-sources/cache/username/owner"
    )
    assert response.status_code in (401, 403)


async def test_admin_invalidate_username_endpoint_500_on_internal_error(
    admin_client, backend_test_state, monkeypatch
):
    """Inject a failure into ``clear_user`` to exercise the 500 handler
    on the username-based path (separate from the user_id-based one)."""
    cache = _get_cache(backend_test_state)

    def boom(*_a, **_k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(cache, "clear_user", boom)

    response = await admin_client.delete(
        "/admin/api/fallback-sources/cache/username/owner"
    )
    assert response.status_code == 500
    assert (
        "Failed to invalidate user cache by username"
        in response.json()["detail"]["error"]
    )
