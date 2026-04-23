"""API tests for repository tree routes."""

from test.kohakuhub.api.helpers import encode_ndjson, file_op


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


async def test_paths_info_accepts_form_encoded_body(owner_client):
    """Both `huggingface_hub.HfApi.get_paths_info` and the kohaku-hub-ui
    frontend (`repoAPI.getPathsInfo` in src/kohaku-hub-ui/src/utils/api.js)
    send this endpoint `application/x-www-form-urlencoded`. FastAPI's
    ``Form(...)`` params expect that exact content-type — pin the wire
    shape here so the frontend integration never silently regresses."""
    response = await owner_client.post(
        "/api/models/owner/demo-model/paths-info/main",
        data={
            "paths": ["README.md", "weights/model.safetensors"],
            "expand": "false",
        },
    )
    response.raise_for_status()
    payload = {entry["path"]: entry for entry in response.json()}
    assert payload["README.md"]["type"] == "file"
    assert payload["README.md"]["size"] > 0

    weights = payload["weights/model.safetensors"]
    assert weights["type"] == "file"
    assert weights["lfs"]["size"] == len(b"safe tensor payload")


async def test_tree_endpoint_expand_true_includes_commit_info(owner_client):
    """`repoAPI.listTree(..., { expand: true })` asks the tree endpoint
    for per-entry last-commit metadata so the UI tree view can render the
    commit column."""
    create_response = await owner_client.post(
        "/api/repos/create",
        json={"type": "model", "name": "tree-expand-fixture", "private": False},
    )
    create_response.raise_for_status()

    commit_payload = encode_ndjson(
        [
            {"key": "header", "value": {"summary": "seed readme"}},
            file_op("README.md", b"# Fixture\n"),
        ]
    )
    commit_response = await owner_client.post(
        "/api/models/owner/tree-expand-fixture/commit/main",
        content=commit_payload,
        headers={"Content-Type": "application/x-ndjson"},
    )
    commit_response.raise_for_status()

    tree_response = await owner_client.get(
        "/api/models/owner/tree-expand-fixture/tree/main",
        params={"expand": "true", "recursive": "false"},
    )
    tree_response.raise_for_status()
    entries = tree_response.json()
    readme = next(e for e in entries if e.get("path") == "README.md")
    last_commit = readme.get("last_commit") or readme.get("lastCommit")
    assert last_commit, f"expand=true should attach commit info, got {readme!r}"
    commit_msg = (
        last_commit.get("title")
        or last_commit.get("message")
        or last_commit.get("summary")
    )
    assert commit_msg and "seed" in commit_msg.lower()


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        piece = part.strip()
        if 'rel="next"' not in piece:
            continue
        url_start = piece.find("<") + 1
        url_end = piece.find(">")
        if url_start > 0 and url_end > url_start:
            return piece[url_start:url_end]
    return None


async def test_tree_pagination_follows_link_next_header(owner_client):
    """Replay the `repoAPI.listTreeAll` walker in kohaku-hub-ui — start at
    the tree endpoint, then follow `Link: <...>; rel="next"` until
    exhausted. Asserts the server's pagination contract end-to-end."""
    create_response = await owner_client.post(
        "/api/repos/create",
        json={"type": "model", "name": "tree-walker-fixture", "private": False},
    )
    create_response.raise_for_status()

    bulk_lines = [{"key": "header", "value": {"summary": "55 files for walker"}}]
    for i in range(55):
        bulk_lines.append(file_op(f"bulk/entry_{i:02d}.txt", f"e{i}\n".encode()))
    commit_response = await owner_client.post(
        "/api/models/owner/tree-walker-fixture/commit/main",
        content=encode_ndjson(bulk_lines),
        headers={"Content-Type": "application/x-ndjson"},
    )
    commit_response.raise_for_status()

    entries: list[dict] = []
    current_url: str | None = "/api/models/owner/tree-walker-fixture/tree/main/bulk"
    pages = 0
    while current_url:
        pages += 1
        response = await owner_client.get(current_url)
        response.raise_for_status()
        entries.extend(response.json())
        current_url = _parse_next_link(response.headers.get("link"))
        # Safety: never loop more than a dozen pages for 55 files.
        assert pages < 10, f"tree walker iterated {pages}x — suspected server bug"

    paths = {entry["path"] for entry in entries if entry.get("type") == "file"}
    assert len(paths) == 55
    assert "bulk/entry_00.txt" in paths
    assert "bulk/entry_54.txt" in paths
