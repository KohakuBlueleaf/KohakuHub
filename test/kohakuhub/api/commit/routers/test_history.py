"""API tests for commit history routes."""

from urllib.parse import parse_qs, urlparse

from test.kohakuhub.api.helpers import encode_ndjson, file_op


async def test_list_commit_history_and_diff(owner_client):
    payload = encode_ndjson(
        [
            {
                "key": "header",
                "value": {"summary": "Add changelog", "description": "history test"},
            },
            file_op("changelog.md", b"# Changelog\n\n- added from history test\n"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/demo-model/commit/main",
        content=payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    assert commit_response.status_code == 200
    commit_id = commit_response.json()["commitOid"]

    list_response = await owner_client.get("/api/models/owner/demo-model/commits/main")
    assert list_response.status_code == 200
    commits = list_response.json()
    assert any(commit["id"] == commit_id for commit in commits)

    detail_response = await owner_client.get(
        f"/api/models/owner/demo-model/commit/{commit_id}"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["author"] == "owner"
    assert detail["message"] == "Add changelog"

    diff_response = await owner_client.get(
        f"/api/models/owner/demo-model/commit/{commit_id}/diff"
    )
    assert diff_response.status_code == 200
    diff_payload = diff_response.json()
    files = {item["path"]: item for item in diff_payload["files"]}
    assert files["changelog.md"]["type"] == "added"
    assert "+++ b/changelog.md" in files["changelog.md"]["diff"]


async def _seed_commits(owner_client, repo_name: str, count: int) -> None:
    create_response = await owner_client.post(
        "/api/repos/create",
        json={"type": "model", "name": repo_name, "private": False},
    )
    create_response.raise_for_status()
    for i in range(count):
        payload = encode_ndjson(
            [
                {"key": "header", "value": {"summary": f"commit {i}"}},
                file_op("doc.txt", f"rev {i}\n".encode()),
            ]
        )
        response = await owner_client.post(
            f"/api/models/owner/{repo_name}/commit/main",
            content=payload,
            headers={"Content-Type": "application/x-ndjson"},
        )
        response.raise_for_status()


async def test_commits_endpoint_returns_hf_shape_with_link_pagination(owner_client):
    """Both ``huggingface_hub.HfApi.list_repo_commits`` and the kohaku-hub-ui
    frontend (``repoAPI.listCommits`` → ``normalizeCommitListResponse`` in
    ``src/kohaku-hub-ui/src/utils/api.js``) expect the HF wire shape:
    a top-level JSON array of commits plus a ``Link: rel="next"`` header
    carrying the ``after=<cursor>`` pagination query for the next page."""
    await _seed_commits(owner_client, "commits-link-pagination", count=3)
    response = await owner_client.get(
        "/api/models/owner/commits-link-pagination/commits/main",
        params={"limit": 2},
    )
    response.raise_for_status()
    commits = response.json()
    assert isinstance(commits, list)
    assert len(commits) == 2
    for commit in commits:
        assert "id" in commit
        assert "title" in commit
        assert "authors" in commit

    link_header = response.headers.get("link", "")
    assert 'rel="next"' in link_header, f"missing next link, got {link_header!r}"


def _normalize_commit_list(response_json, link_header):
    """Parity reimplementation of ``normalizeCommitListResponse`` in
    kohaku-hub-ui/src/utils/api.js:57. Keep it in sync with the UI helper
    when the server-side shape changes."""
    assert isinstance(response_json, list)

    next_cursor = None
    if link_header:
        for part in link_header.split(","):
            piece = part.strip()
            if 'rel="next"' not in piece:
                continue
            url_start = piece.find("<") + 1
            url_end = piece.find(">")
            if url_start <= 0 or url_end <= url_start:
                continue
            query = parse_qs(urlparse(piece[url_start:url_end]).query)
            if "after" in query:
                next_cursor = query["after"][0]
                break

    return {
        "commits": [
            {
                "id": commit["id"],
                "oid": commit.get("oid") or commit["id"],
                "title": commit.get("title", ""),
                "message": commit.get("message", ""),
                "date": commit.get("date"),
                "author": (
                    commit.get("author")
                    or (
                        commit["authors"][0].get("user")
                        if commit.get("authors")
                        and isinstance(commit["authors"][0], dict)
                        else (
                            commit["authors"][0]
                            if commit.get("authors")
                            else "unknown"
                        )
                    )
                ),
                "email": commit.get("email", ""),
                "parents": commit.get("parents", []),
            }
            for commit in response_json
        ],
        "hasMore": bool(next_cursor),
        "nextCursor": next_cursor,
    }


async def test_commit_list_frontend_normalizer_round_trips(owner_client):
    """Run the UI's ``normalizeCommitListResponse`` helper against a real
    server response and verify every field the UI depends on is populated.
    If the server response ever drops a required field, this test fails."""
    await _seed_commits(owner_client, "commits-normalize-fixture", count=3)
    response = await owner_client.get(
        "/api/models/owner/commits-normalize-fixture/commits/main",
        params={"limit": 2},
    )
    response.raise_for_status()
    normalized = _normalize_commit_list(response.json(), response.headers.get("link"))
    assert normalized["hasMore"] is True
    assert normalized["nextCursor"], "pagination cursor must be extractable"
    assert len(normalized["commits"]) == 2
    for commit in normalized["commits"]:
        assert commit["id"]
        assert commit["oid"]
        assert commit["author"] == "owner"
