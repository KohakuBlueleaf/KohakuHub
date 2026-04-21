"""API tests for repository statistics routes."""


async def test_repository_stats_and_recent_history_for_public_repo(client):
    stats_response = await client.get("/api/models/owner/demo-model/stats")
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert stats_payload["downloads"] >= 0
    assert stats_payload["likes"] >= 1

    recent_response = await client.get(
        "/api/models/owner/demo-model/stats/recent",
        params={"days": 7},
    )
    assert recent_response.status_code == 200
    recent_payload = recent_response.json()
    assert recent_payload["period"]["days"] == 7
    assert recent_payload["period"]["start"] <= recent_payload["period"]["end"]
    assert isinstance(recent_payload["stats"], list)


async def test_repository_stats_not_found_returns_hf_compatible_error(client):
    response = await client.get("/api/models/owner/missing-repo/stats")

    assert response.status_code == 404
    assert response.headers["x-error-code"] == "RepoNotFound"


async def test_trending_repositories_returns_visible_results(client):
    response = await client.get(
        "/api/trending",
        params={"repo_type": "model", "days": 30, "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"]["days"] == 30
    assert isinstance(payload["trending"], list)
    if payload["trending"]:
        first_item = payload["trending"][0]
        assert {"id", "type", "downloads", "likes", "recent_downloads", "private"} <= set(
            first_item.keys()
        )
