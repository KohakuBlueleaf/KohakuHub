"""API tests for repository tree routes."""


async def test_list_repo_tree_returns_files_and_directories(client):
    response = await client.get("/api/models/owner/demo-model/tree/main")

    assert response.status_code == 200
    paths = {item["path"] for item in response.json()}
    assert "README.md" in paths
    assert "config.json" in paths
    assert "weights" in paths


async def test_paths_info_returns_file_and_directory_entries(client):
    response = await client.post(
        "/api/models/owner/demo-model/paths-info/main",
        files=[("paths", (None, "README.md")), ("paths", (None, "weights"))],
    )

    assert response.status_code == 200
    payload = {item["path"]: item for item in response.json()}
    assert payload["README.md"]["type"] == "file"
    assert payload["weights"]["type"] == "directory"
