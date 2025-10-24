---
title: Commits API
description: View commit history and file diffs
icon: i-carbon-commit
---

# Commits API

View commit history, commit details, and file-level diffs.

---

## List Commits

**Pattern:** `GET /api/{repo_type}s/{namespace}/{name}/commits/{branch}`

**Authentication:** Optional (required for private repos)

**Query Parameters:**
- `limit`: Max commits to return (default: 20, max: 100)
- `after`: Pagination cursor (commit ID to start after)

**Response:**
```json
{
  "commits": [
    {
      "id": "abc123def456...",
      "oid": "abc123def456...",
      "title": "Update model weights",
      "message": "Update model weights\n\nImproved accuracy by 2%",
      "date": 1737360932,
      "author": "alice",
      "email": "alice@example.com",
      "parents": ["parent_commit_id"]
    }
  ],
  "hasMore": true,
  "nextCursor": "next_commit_id"
}
```

**Fields:**
- `id` / `oid`: Commit hash
- `title`: First line of commit message
- `message`: Full commit message
- `date`: Unix timestamp
- `author`: Username from database (fallback to LakeFS committer)
- `parents`: Array of parent commit IDs

**Example:**
```bash
GET /api/models/username/my-model/commits/main?limit=50
```

---

## Get Commit Details

**Pattern:** `GET /api/{repo_type}s/{namespace}/{name}/commit/{commit_id}`

**Authentication:** Optional (required for private repos)

**Response:**
```json
{
  "id": "abc123def456...",
  "oid": "abc123def456...",
  "title": "Update model weights",
  "message": "Update model weights\n\nImproved accuracy",
  "date": 1737360932,
  "parents": ["parent_commit_id"],
  "metadata": {
    "description": "Detailed description",
    "email": "alice@example.com"
  },
  "author": "alice",
  "user_id": 1,
  "description": "Improved accuracy by 2%",
  "committed_at": "2025-01-20T10:15:32Z"
}
```

**Additional Fields (from database):**
- `user_id`: Database user ID
- `description`: Commit description
- `committed_at`: Timestamp in ISO format

---

## Get Commit Diff

**Pattern:** `GET /api/{repo_type}s/{namespace}/{name}/commit/{commit_id}/diff`

**Authentication:** Optional (required for private repos)

**Purpose:** Show what changed in this commit compared to parent

**Response:**
```json
{
  "commit_id": "abc123def456...",
  "parent_commit": "parent_commit_id",
  "message": "Update model weights",
  "author": "alice",
  "date": 1737360932,
  "files": [
    {
      "path": "model.safetensors",
      "type": "changed",
      "path_type": "object",
      "size_bytes": 5368709120,
      "is_lfs": true,
      "sha256": "new_sha256",
      "previous_sha256": "old_sha256",
      "previous_size": 5368000000,
      "diff": null
    },
    {
      "path": "config.json",
      "type": "changed",
      "path_type": "object",
      "size_bytes": 512,
      "is_lfs": false,
      "sha256": "abc123",
      "previous_sha256": "def456",
      "diff": "@@ -1,3 +1,3 @@\n {\n-  \"model\": \"bert\"\n+  \"model\": \"bert-v2\"\n }"
    },
    {
      "path": "new_file.txt",
      "type": "added",
      "path_type": "object",
      "size_bytes": 100,
      "is_lfs": false,
      "sha256": "xyz789",
      "diff": "@@ -0,0 +1,1 @@\n+Hello world"
    },
    {
      "path": "old_file.txt",
      "type": "removed",
      "path_type": "object",
      "size_bytes": 50,
      "is_lfs": false,
      "previous_sha256": "old_hash",
      "diff": "@@ -1,1 +0,0 @@\n-Goodbye"
    }
  ]
}
```

**File Change Types:**
- `"added"`: New file in this commit
- `"removed"`: File deleted in this commit
- `"changed"`: File modified in this commit

**Diff Field:**
- `null`: For LFS files or files >1MB
- Unified diff format: For text files <1MB

**LFS Detection:**
- Checked from database File record
- Falls back to repo-specific LFS rules

**Example:**
```bash
GET /api/models/username/my-model/commit/abc123/diff
```

**Processing:**
- All file changes fetched from LakeFS diff API
- Diff generated in parallel for all non-LFS files
- SHA256 values from database (accurate for LFS files)
- Size limits: Skip diff for files >1MB

---

## Usage Examples

### View Recent Commits

```python
import requests

API_BASE = "http://localhost:28080/api"
TOKEN = "your_token"

# Get last 50 commits
response = requests.get(
    f"{API_BASE}/models/username/my-model/commits/main?limit=50",
    headers={"Authorization": f"Bearer {TOKEN}"}
)

commits = response.json()["commits"]

for commit in commits:
    print(f"{commit['id'][:8]} - {commit['title']} by {commit['author']}")
```

---

### Paginate Through All Commits

```python
all_commits = []
after = None

while True:
    url = f"{API_BASE}/models/username/my-model/commits/main?limit=100"
    if after:
        url += f"&after={after}"

    response = requests.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
    data = response.json()

    all_commits.extend(data["commits"])

    if not data["hasMore"]:
        break

    after = data["nextCursor"]

print(f"Total commits: {len(all_commits)}")
```

---

### View Commit Details with Diff

```python
commit_id = "abc123def456"

# Get commit info
commit_resp = requests.get(
    f"{API_BASE}/models/username/my-model/commit/{commit_id}",
    headers={"Authorization": f"Bearer {TOKEN}"}
)
commit = commit_resp.json()

# Get diff
diff_resp = requests.get(
    f"{API_BASE}/models/username/my-model/commit/{commit_id}/diff",
    headers={"Authorization": f"Bearer {TOKEN}"}
)
diff = diff_resp.json()

print(f"Commit: {commit['title']}")
print(f"Author: {commit['author']}")
print(f"Date: {commit['date']}")
print(f"\nFiles changed: {len(diff['files'])}")

for file in diff['files']:
    print(f"\n{file['type'].upper()}: {file['path']}")
    if file.get('diff'):
        print(file['diff'])
    elif file.get('is_lfs'):
        print(f"  LFS file: {file['size_bytes']:,} bytes")
```

---

### Find Commits by Author

```python
# Get all commits
response = requests.get(
    f"{API_BASE}/models/username/my-model/commits/main?limit=100",
    headers={"Authorization": f"Bearer {TOKEN}"}
)

commits = response.json()["commits"]

# Filter by author
alice_commits = [c for c in commits if c["author"] == "alice"]

print(f"Alice made {len(alice_commits)} commits")
```

---

### Compare Two Commits

```python
def get_files_in_commit(commit_id):
    """Get list of files changed in commit"""
    diff_resp = requests.get(
        f"{API_BASE}/models/username/my-model/commit/{commit_id}/diff",
        headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return {f["path"]: f for f in diff_resp.json()["files"]}

# Compare two commits
commit1_files = get_files_in_commit("commit1_id")
commit2_files = get_files_in_commit("commit2_id")

# Find common files
common_files = set(commit1_files.keys()) & set(commit2_files.keys())

print(f"Both commits changed {len(common_files)} files:")
for path in common_files:
    print(f"  {path}")
```

---

## Response Details

### Commit Object

From database (when available):
- `author`: Actual username from User table
- `user_id`: Database user ID
- `description`: Commit description field
- `committed_at`: ISO timestamp

From LakeFS (fallback):
- `author`: Committer field from LakeFS
- `metadata`: LakeFS metadata
- `date`: Unix timestamp

### Diff Format

**Unified Diff (for text files):**
```
@@ -1,3 +1,3 @@
 {
-  "model": "bert"
+  "model": "bert-v2"
 }
```

**Format:**
- `@@` header: line numbers
- `-` lines: Removed
- `+` lines: Added
- ` ` lines: Context (unchanged)

**LFS Files:**
- No diff generated (too large)
- `diff: null`
- SHA256 hashes provided instead

---

## Performance Notes

**Commit List:**
- Fast (index-based pagination)
- Database join for author info
- Default limit: 20 commits

**Commit Diff:**
- Parallel file processing
- Diff generation for non-LFS files only
- Size limit: Skip diff for files >1MB
- Memory efficient (streaming)

**Best Practices:**
- Use pagination for large histories
- Cache commit lists client-side
- Limit diff viewing to recent commits
- Use commit IDs (not branch names) for stable refs

---

## Next Steps

- [Branches API](./branches.md) - Create/merge/revert
- [File Upload API](./file-upload.md) - Create commits
- [Tree API](./tree.md) - Browse files
