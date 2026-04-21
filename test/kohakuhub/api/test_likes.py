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
