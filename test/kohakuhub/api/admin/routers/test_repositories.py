"""API tests for admin repository routes."""


async def test_admin_can_inspect_repositories_and_files(admin_client):
    list_response = await admin_client.get(
        "/admin/api/repositories",
        params={"search": "demo-model"},
    )
    assert list_response.status_code == 200
    repositories = list_response.json()["repositories"]
    assert any(repo["full_id"] == "owner/demo-model" for repo in repositories)

    detail_response = await admin_client.get(
        "/admin/api/repositories/model/owner/demo-model"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["full_id"] == "owner/demo-model"
    assert detail["file_count"] >= 3

    files_response = await admin_client.get(
        "/admin/api/repositories/model/owner/demo-model/files"
    )
    assert files_response.status_code == 200
    paths = {item["path"] for item in files_response.json()["files"]}
    assert {"README.md", "config.json", "weights/model.safetensors"} <= paths

    breakdown_response = await admin_client.get(
        "/admin/api/repositories/model/owner/demo-model/storage-breakdown"
    )
    assert breakdown_response.status_code == 200
    breakdown = breakdown_response.json()
    assert breakdown["total_size"] >= breakdown["lfs_files_size"]
