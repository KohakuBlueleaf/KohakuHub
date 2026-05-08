"""Tests covering the ``repo_count`` field on ``GET /org/{org_name}``.

Background (issue #63): the ``/organizations`` grid page used to call
``listRepos(limit=1000)`` × 3 types per org card just to read ``.length``,
triggering hundreds of LakeFS round-trips per card on the backend. The
fix returns ``repo_count`` directly from the org info endpoint via a
single ``GROUP BY repo_type`` SQL.

This module pins:

1. the SQL aggregate counts each repo type correctly
2. counts are isolated per namespace
3. counts grow when new repos are created
4. ``repo_count.total`` round-trips equal to the union of
   ``HfApi.list_models / list_datasets / list_spaces(author=org)``,
   so a future drift in either side surfaces immediately

Reference: issue #63 (perf umbrella #69).
"""

from __future__ import annotations

import asyncio


async def test_org_repo_count_zero_for_empty_org(owner_client):
    """Freshly-created org with no repos must report all zeros."""
    create_resp = await owner_client.post(
        "/org/create",
        json={"name": "empty-team", "description": "no repos here"},
    )
    create_resp.raise_for_status()

    info_resp = await owner_client.get("/org/empty-team")
    info_resp.raise_for_status()
    counts = info_resp.json()["repo_count"]
    assert counts == {"model": 0, "dataset": 0, "space": 0, "total": 0}


async def test_org_repo_count_isolated_per_namespace(owner_client):
    """``repo_count`` for acme-labs must not include owner's repos.

    The seed plants ``owner/demo-model`` in the ``owner`` namespace — that
    must not bleed into the acme-labs count."""
    info_resp = await owner_client.get("/org/acme-labs")
    info_resp.raise_for_status()
    counts = info_resp.json()["repo_count"]
    # Seed: acme-labs has exactly one (private) dataset.
    assert counts == {"model": 0, "dataset": 1, "space": 0, "total": 1}


async def test_org_repo_count_grows_after_creating_a_new_repo(owner_client):
    """Creating a model under acme-labs must bump model + total by 1
    while leaving dataset / space unchanged."""
    before = (await owner_client.get("/org/acme-labs")).json()["repo_count"]
    assert before["model"] == 0
    assert before["dataset"] == 1
    assert before["total"] == before["model"] + before["dataset"] + before["space"]

    create_resp = await owner_client.post(
        "/api/repos/create",
        json={
            "type": "model",
            "name": "labs-bench",
            "private": False,
            "organization": "acme-labs",
        },
    )
    create_resp.raise_for_status()

    after = (await owner_client.get("/org/acme-labs")).json()["repo_count"]
    assert after["model"] == before["model"] + 1
    assert after["dataset"] == before["dataset"]
    assert after["space"] == before["space"]
    assert after["total"] == before["total"] + 1


async def test_org_repo_count_counts_each_type_independently(owner_client):
    """Plant one repo of each type under a fresh org and verify the
    GROUP BY SQL reports them per-type rather than collapsing to a single
    bucket."""
    # New org so we don't have to worry about pre-seeded entries.
    create_org = await owner_client.post(
        "/org/create",
        json={"name": "trio-team", "description": "one of each"},
    )
    create_org.raise_for_status()

    for repo_type, name in (
        ("model", "trio-model"),
        ("dataset", "trio-dataset"),
        ("space", "trio-space"),
    ):
        payload = {
            "type": repo_type,
            "name": name,
            "private": False,
            "organization": "trio-team",
        }
        if repo_type == "space":
            # space create requires sdk
            payload["space_sdk"] = "static"
        resp = await owner_client.post("/api/repos/create", json=payload)
        resp.raise_for_status()

    counts = (await owner_client.get("/org/trio-team")).json()["repo_count"]
    assert counts == {"model": 1, "dataset": 1, "space": 1, "total": 3}


# --- huggingface_hub upstream cross-check ----------------------------------
#
# The KohakuHub /org/{name} endpoint is *not* a HuggingFace API surface (HF
# uses /api/users/{name}/overview for both users and orgs). But the orgs
# grid page used to derive its count from HF-compatible list endpoints, so
# the migration from "listRepos.length" to "repo_count" must keep both
# numbers aligned: ``repo_count.total`` from /org/{name} must equal the
# total surfaced by ``HfApi.list_models/list_datasets/list_spaces``. If
# they ever drift, either the SQL aggregate or one of the list privacy
# filters has a bug.
#
# Uses the live HTTP server (HfApi can't talk to ASGITransport).


async def test_org_repo_count_matches_hf_list_apis(
    live_server_url, hf_api_token, owner_client
):
    """``repo_count.total`` from /org/{name} must equal what
    ``huggingface_hub.HfApi`` would compute from its own list endpoints.

    This protects against the SQL aggregate diverging from the privacy-
    filtered list endpoints (e.g. if a future change adds a ``deleted``
    flag to ``Repository`` and one query forgets to honor it).
    """
    from huggingface_hub import HfApi

    api = HfApi(endpoint=live_server_url, token=hf_api_token)

    # Plant a known mix under a fresh org so we don't conflate with seed
    # data that other tests might have mutated in-session.
    create_org = await owner_client.post(
        "/org/create",
        json={"name": "hfapi-cross-team", "description": "cross-check"},
    )
    create_org.raise_for_status()

    plants = [
        ("model", "alpha"),
        ("model", "beta"),
        ("dataset", "wiki-shard"),
        ("space", "demo"),
    ]
    for repo_type, name in plants:
        payload = {
            "type": repo_type,
            "name": name,
            "private": False,
            "organization": "hfapi-cross-team",
        }
        if repo_type == "space":
            payload["space_sdk"] = "static"
        resp = await owner_client.post("/api/repos/create", json=payload)
        resp.raise_for_status()

    info_resp = await owner_client.get("/org/hfapi-cross-team")
    info_resp.raise_for_status()
    counts = info_resp.json()["repo_count"]

    # Pull each list type via HfApi (the upstream client) and compare.
    models = await asyncio.to_thread(
        lambda: list(api.list_models(author="hfapi-cross-team", limit=1000))
    )
    datasets = await asyncio.to_thread(
        lambda: list(api.list_datasets(author="hfapi-cross-team", limit=1000))
    )
    spaces = await asyncio.to_thread(
        lambda: list(api.list_spaces(author="hfapi-cross-team", limit=1000))
    )

    assert counts["model"] == len(models), (
        f"repo_count.model={counts['model']} but HfApi.list_models returned "
        f"{len(models)} (ids: {[m.id for m in models]})"
    )
    assert counts["dataset"] == len(datasets), (
        f"repo_count.dataset={counts['dataset']} but HfApi.list_datasets returned "
        f"{len(datasets)} (ids: {[d.id for d in datasets]})"
    )
    assert counts["space"] == len(spaces), (
        f"repo_count.space={counts['space']} but HfApi.list_spaces returned "
        f"{len(spaces)} (ids: {[s.id for s in spaces]})"
    )
    assert counts["total"] == len(models) + len(datasets) + len(spaces)

    # Sanity: the planted shape we expect.
    assert counts == {"model": 2, "dataset": 1, "space": 1, "total": 4}


async def test_org_info_payload_shape_unchanged_except_for_repo_count(owner_client):
    """The other fields on the org info payload — ``name``, ``description``,
    ``created_at``, ``_source`` — must be untouched. Adding ``repo_count``
    is purely additive."""
    resp = await owner_client.get("/org/acme-labs")
    resp.raise_for_status()
    payload = resp.json()
    assert payload["name"] == "acme-labs"
    assert "description" in payload
    assert "created_at" in payload
    assert payload["_source"] == "local"
    # The new field, asserted separately.
    assert isinstance(payload["repo_count"], dict)
    assert set(payload["repo_count"].keys()) == {"model", "dataset", "space", "total"}
