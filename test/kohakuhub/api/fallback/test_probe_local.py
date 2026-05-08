"""Coverage for ``probe_local`` — the chain-tester's local-hop probe (#78 v2).

``probe_local`` runs the *real* local handler (via ``__wrapped__`` on
the ``with_repo_fallback`` decorator) and classifies the response into
the same four decisions ``with_repo_fallback`` itself emits:

- ``LOCAL_HIT`` — 2xx/3xx success
- ``LOCAL_FILTERED`` — 404 + ``EntryNotFound`` / ``RevisionNotFound``
  (local owns the repo, only the entry/revision is missing —
  production stops here, no fallback)
- ``LOCAL_MISS`` — 404 with no error code (or ``RepoNotFound``) —
  local doesn't have the repo, fallback chain may run
- ``LOCAL_OTHER_ERROR`` — any other 4xx/5xx, e.g. permission denied,
  surfaces as the final outcome with no fallback in production

Behavioural fidelity matters here: the simulate endpoint hands the
report straight to the operator, so a wrong classification would
mislead them. We exercise each decision against the real handler.
"""
from __future__ import annotations

import pytest

from kohakuhub.api.fallback.probe_local import probe_local


# ---------------------------------------------------------------------------
# LOCAL_MISS — repo absent
# ---------------------------------------------------------------------------


async def test_probe_local_info_missing_repo_returns_local_miss(
    backend_test_state,
):
    """A repo that doesn't exist anywhere → ``LOCAL_MISS`` (404 +
    ``RepoNotFound``). This is the only decision that lets the
    simulate endpoint walk into the fallback chain."""
    attempt = await probe_local(
        "info", "model", "no-such-namespace", "no-such-repo"
    )
    assert attempt.decision == "LOCAL_MISS"
    assert attempt.status_code == 404
    assert attempt.x_error_code == "RepoNotFound"
    assert attempt.source_name == "local"
    assert attempt.source_type == "local"
    # The synthetic upstream_path mirrors the path the chain probe
    # would hit upstream — same shape as ``operations.try_fallback_info``.
    assert attempt.upstream_path == "/api/models/no-such-namespace/no-such-repo"
    assert attempt.method == "GET"
    assert attempt.duration_ms >= 0


async def test_probe_local_resolve_missing_repo_uses_head_method(
    backend_test_state,
):
    """Resolve op probes via HEAD just like the production path — the
    ``method`` field must reflect that so the timeline shows what
    method the chain actually issues."""
    attempt = await probe_local(
        "resolve", "model", "no-ns", "no-repo",
        revision="main", file_path="config.json",
    )
    assert attempt.decision == "LOCAL_MISS"
    assert attempt.method == "HEAD"
    assert attempt.upstream_path == "/models/no-ns/no-repo/resolve/main/config.json"


async def test_probe_local_paths_info_uses_post_method(backend_test_state):
    attempt = await probe_local(
        "paths_info", "model", "no-ns", "no-repo",
        revision="main", paths=["foo.bin", "README.md"],
    )
    assert attempt.decision == "LOCAL_MISS"
    assert attempt.method == "POST"


async def test_probe_local_tree_uses_get_with_constructed_path(
    backend_test_state,
):
    attempt = await probe_local(
        "tree", "model", "no-ns", "no-repo",
        revision="main", file_path="docs",
    )
    assert attempt.decision == "LOCAL_MISS"
    assert attempt.method == "GET"
    assert attempt.upstream_path == "/api/models/no-ns/no-repo/tree/main/docs"


# ---------------------------------------------------------------------------
# LOCAL_HIT — public repo, anonymous caller
# ---------------------------------------------------------------------------


async def test_probe_local_info_hits_public_repo(owner_client, backend_test_state):
    """Public repo created via the real /api/repos/create endpoint
    → ``probe_local('info')`` returns ``LOCAL_HIT`` (200) for an
    anonymous caller. Mirrors what production does for the same repo."""
    response = await owner_client.post(
        "/api/repos/create",
        json={"name": "probe-local-public", "type": "model", "private": False},
    )
    assert response.status_code == 200, response.text

    attempt = await probe_local(
        "info", "model", "owner", "probe-local-public", user=None
    )
    assert attempt.decision == "LOCAL_HIT"
    assert attempt.status_code == 200
    assert attempt.x_error_code is None
    # body_preview is non-empty for a HIT (the dict gets JSON-encoded).
    assert attempt.response_body_preview
    assert "probe-local-public" in attempt.response_body_preview


async def test_probe_local_info_hits_with_authed_user(
    owner_client, backend_test_state
):
    """Authenticated owner sees their own repo's full body preview
    (including the storage block which is owner-only). The point
    here is to verify we forward the impersonated User to the
    handler — production gates ``storage`` on ``user is not None``."""
    response = await owner_client.post(
        "/api/repos/create",
        json={"name": "probe-local-authed", "type": "model", "private": False},
    )
    assert response.status_code == 200

    # Construct the User row the same way production resolves it via
    # the auth dep — directly from the DB.
    from kohakuhub.db_operations import get_user_by_username

    owner = get_user_by_username("owner")
    assert owner is not None
    attempt = await probe_local(
        "info", "model", "owner", "probe-local-authed", user=owner
    )
    assert attempt.decision == "LOCAL_HIT"
    assert attempt.status_code == 200
    # ``storage`` is the owner-visible field added by the info handler
    # for authed users.
    assert "storage" in (attempt.response_body_preview or "")


# ---------------------------------------------------------------------------
# LOCAL_MISS — private repo hidden from anonymous caller
# ---------------------------------------------------------------------------


async def test_probe_local_info_private_repo_anonymous_returns_local_miss(
    owner_client, backend_test_state
):
    """Private repo + anonymous caller → the local handler returns
    a 404 + ``RepoNotFound`` (HF anti-enumeration shape). Despite
    the repo *existing* locally we classify as ``LOCAL_MISS`` so
    production would walk the chain — that's exactly what
    ``with_repo_fallback`` does, and the simulate must agree."""
    response = await owner_client.post(
        "/api/repos/create",
        json={"name": "probe-local-private", "type": "model", "private": True},
    )
    assert response.status_code == 200

    attempt = await probe_local(
        "info", "model", "owner", "probe-local-private", user=None
    )
    assert attempt.decision == "LOCAL_MISS"
    assert attempt.status_code == 404
    assert attempt.x_error_code == "RepoNotFound"


async def test_probe_local_info_private_repo_owner_returns_local_hit(
    owner_client, backend_test_state
):
    """Same private repo, authed owner → ``LOCAL_HIT`` (200). The
    ``user=`` parameter is the only difference; this is the
    impersonation primitive the chain tester uses."""
    response = await owner_client.post(
        "/api/repos/create",
        json={"name": "probe-local-private-2", "type": "model", "private": True},
    )
    assert response.status_code == 200

    from kohakuhub.db_operations import get_user_by_username

    owner = get_user_by_username("owner")
    attempt = await probe_local(
        "info", "model", "owner", "probe-local-private-2", user=owner
    )
    assert attempt.decision == "LOCAL_HIT"
    assert attempt.status_code == 200


# ---------------------------------------------------------------------------
# LOCAL_FILTERED — local repo exists, the entry/revision doesn't
# ---------------------------------------------------------------------------


async def test_probe_local_tree_missing_revision_returns_local_filtered(
    owner_client, backend_test_state
):
    """Repo exists locally, revision doesn't → 404 +
    ``RevisionNotFound``. Production stops the chain here (#75
    strict-consistency rule), so probe_local must classify as
    ``LOCAL_FILTERED`` — the simulate must NOT walk the chain.
    """
    r = await owner_client.post(
        "/api/repos/create",
        json={"name": "probe-local-tree", "type": "model", "private": False},
    )
    assert r.status_code == 200

    attempt = await probe_local(
        "tree", "model", "owner", "probe-local-tree",
        revision="this-branch-does-not-exist",
    )
    # Local owns the repo, just no such revision → FILTERED
    assert attempt.decision == "LOCAL_FILTERED"
    assert attempt.status_code == 404
    assert attempt.x_error_code == "RevisionNotFound"


# ---------------------------------------------------------------------------
# Op validation
# ---------------------------------------------------------------------------


async def test_probe_local_unsupported_op_yields_local_other_error(
    backend_test_state,
):
    """``probe_local('frob')`` → returns a synthetic
    ``LOCAL_OTHER_ERROR`` rather than raising; the simulate
    endpoint then renders the error in the timeline."""
    attempt = await probe_local("frob-the-foo", "model", "owner", "demo")
    assert attempt.decision == "LOCAL_OTHER_ERROR"
    assert attempt.status_code == 500
    assert "Unsupported probe op" in (attempt.x_error_message or "")


# ---------------------------------------------------------------------------
# Contract: ``_build_kwargs`` must hand a parameter set the inner
# handler accepts, no matter how the handler signatures evolve.
#
# Without this, an optional-kwarg rename (e.g. ``repo_name`` → ``name``)
# could silently break simulate at runtime — the e2e tests above only
# catch the failure mode if the rename happens to involve a parameter
# they exercise. The contract test below uses ``inspect.signature`` so
# every kwarg ``_build_kwargs`` produces is verified to land somewhere
# the handler accepts, and every required parameter the handler
# declares is verified to be in the kwargs we pass.
# ---------------------------------------------------------------------------


import inspect

from kohakuhub.api.fallback.core import SUPPORTED_OPS
from kohakuhub.api.fallback.probe_local import (
    _build_kwargs,
    _build_synthetic_request,
    _resolve_inner,
)


@pytest.mark.parametrize("op", SUPPORTED_OPS)
def test_build_kwargs_matches_inner_handler_signature(op, backend_test_state):
    """For every supported op, ``_build_kwargs`` must produce kwargs
    that the inner handler accepts (no extra unknown args) and supply
    every required parameter the handler declares (no missing ones).
    """
    inner = _resolve_inner(op)
    sig = inspect.signature(inner)
    request = _build_synthetic_request(method="GET", upstream_path="/foo")
    kwargs = _build_kwargs(
        op=op,
        repo_type="model",
        namespace="ns",
        name="n",
        revision="main",
        file_path="",
        paths=None,
        request=request,
        user=None,
    )

    handler_params = sig.parameters
    # No unknown kwargs (would TypeError at call time).
    unknown = [k for k in kwargs if k not in handler_params]
    assert not unknown, (
        f"_build_kwargs for op={op!r} produces kwarg(s) the handler "
        f"doesn't accept: {unknown}"
    )
    # Every required handler parameter must be present in kwargs.
    missing = [
        name
        for name, p in handler_params.items()
        if p.default is inspect.Parameter.empty
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
        and name not in kwargs
    ]
    assert not missing, (
        f"_build_kwargs for op={op!r} is missing required "
        f"handler parameter(s): {missing}"
    )
