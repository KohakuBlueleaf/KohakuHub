"""API tests for repository likes."""


async def test_like_check_and_unlike_repository(member_client):
    check_before = await member_client.get("/api/models/owner/demo-model/like")
    assert check_before.status_code == 200
    assert check_before.json()["liked"] is False

    like_response = await member_client.post("/api/models/owner/demo-model/like")
    assert like_response.status_code == 200
    assert like_response.json()["likes_count"] == 2

    likers_response = await member_client.get("/api/models/owner/demo-model/likers")
    assert likers_response.status_code == 200
    likers = likers_response.json()
    assert len(likers) == 2
    assert {item["user"] for item in likers} == {"owner", "member"}

    check_after = await member_client.get("/api/models/owner/demo-model/like")
    assert check_after.json()["liked"] is True

    unlike_response = await member_client.delete("/api/models/owner/demo-model/like")
    assert unlike_response.status_code == 200
    assert unlike_response.json()["likes_count"] == 1


async def test_list_user_likes_hides_private_repos_without_access(member_client, outsider_client):
    like_response = await member_client.post("/api/datasets/acme-labs/private-dataset/like")
    assert like_response.status_code == 200

    visible_response = await member_client.get("/api/users/member/likes")
    assert visible_response.status_code == 200
    assert any(
        item["repo"]["name"] == "acme-labs/private-dataset"
        for item in visible_response.json()
    )

    hidden_response = await outsider_client.get("/api/users/member/likes")
    assert hidden_response.status_code == 200
    assert all(
        item["repo"]["name"] != "acme-labs/private-dataset"
        for item in hidden_response.json()
    )


async def test_likers_endpoint_returns_hf_top_level_list_shape(owner_client):
    """Both ``huggingface_hub.HfApi.list_repo_likers`` and the kohaku-hub-ui
    frontend (``likesAPI.getLikers`` → ``normalizeLikersResponse`` in
    ``src/kohaku-hub-ui/src/utils/api.js``) expect a top-level JSON array
    of ``{user, fullname}`` entries. This pins the wire shape explicitly
    so a refactor that wraps the response in ``{likers: [...]}`` would be
    caught here, not only in hf_hub compat."""
    response = await owner_client.get(
        "/api/models/owner/demo-model/likers",
        params={"limit": 10},
    )
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, list), f"likers must be a top-level list, got {payload!r}"
    assert payload, "baseline seed has at least one liker (owner)"
    for entry in payload:
        assert entry.get("user") or entry.get("username")


def _normalize_likers(response_json):
    """Parity reimplementation of ``normalizeLikersResponse`` in
    kohaku-hub-ui/src/utils/api.js:88. Keep in sync with the UI helper
    when the server-side shape changes."""
    assert isinstance(response_json, list)
    return {
        "likers": [
            {
                "username": entry.get("user") or entry.get("username"),
                "full_name": (
                    entry.get("fullname")
                    or entry.get("full_name")
                    or entry.get("user")
                    or entry.get("username")
                ),
            }
            for entry in response_json
        ],
        "total": len(response_json),
    }


async def test_frontend_likers_normalizer_round_trips(owner_client):
    """Run the UI's ``normalizeLikersResponse`` against a live response to
    confirm every field the likers view depends on is populated."""
    response = await owner_client.get("/api/models/owner/demo-model/likers")
    response.raise_for_status()
    normalized = _normalize_likers(response.json())
    assert normalized["total"] >= 1
    assert normalized["likers"][0]["username"]
    assert normalized["likers"][0]["full_name"]
