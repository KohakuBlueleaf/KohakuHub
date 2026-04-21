"""API tests for admin statistics routes."""


async def test_admin_stats_overview_and_detailed_payloads(admin_client):
    overview_response = await admin_client.get("/admin/api/stats")
    assert overview_response.status_code == 200
    overview = overview_response.json()
    assert overview["users"] >= 4
    assert overview["organizations"] >= 1
    assert overview["repositories"]["total"] >= 2
    assert overview["repositories"]["public"] >= 1

    detailed_response = await admin_client.get("/admin/api/stats/detailed")
    assert detailed_response.status_code == 200
    detailed = detailed_response.json()
    assert detailed["users"]["total"] >= detailed["users"]["active"]
    assert detailed["users"]["inactive"] == (
        detailed["users"]["total"] - detailed["users"]["active"]
    )
    assert detailed["repositories"]["by_type"]["model"] >= 1
    assert detailed["repositories"]["by_type"]["dataset"] >= 1
    assert detailed["commits"]["total"] >= 1
    assert isinstance(detailed["commits"]["top_contributors"], list)


async def test_admin_stats_timeseries_and_top_repo_rankings(admin_client):
    timeseries_response = await admin_client.get("/admin/api/stats/timeseries", params={"days": 30})
    assert timeseries_response.status_code == 200
    timeseries = timeseries_response.json()
    assert "repositories_by_day" in timeseries
    assert "commits_by_day" in timeseries
    assert "users_by_day" in timeseries
    assert timeseries["repositories_by_day"]
    assert timeseries["commits_by_day"]
    assert timeseries["users_by_day"]

    top_commits_response = await admin_client.get(
        "/admin/api/stats/top-repos",
        params={"by": "commits", "limit": 5},
    )
    assert top_commits_response.status_code == 200
    top_commits = top_commits_response.json()
    assert top_commits["sorted_by"] == "commits"
    assert top_commits["top_repositories"]
    assert "commit_count" in top_commits["top_repositories"][0]

    top_size_response = await admin_client.get(
        "/admin/api/stats/top-repos",
        params={"by": "size", "limit": 5},
    )
    assert top_size_response.status_code == 200
    top_size = top_size_response.json()
    assert top_size["sorted_by"] == "size"
    assert top_size["top_repositories"]
    assert "total_size" in top_size["top_repositories"][0]
