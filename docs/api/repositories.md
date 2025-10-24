---
title: Repository API
description: CRUD operations for models, datasets, and spaces
icon: i-carbon-data-base
---

# Repository API

Create, read, update, delete, move, and squash repositories.

---

## Create Repository

**Pattern:** `POST /api/repos/create`

**Authentication:** Required

**Request Body:**
```json
{
  "type": "model",
  "name": "my-model",
  "organization": null,
  "private": false,
  "sdk": null
}
```

**Fields:**
- `type`: Repository type (`"model"`, `"dataset"`, or `"space"`)
- `name`: Repository name (required)
- `organization`: Organization name (optional, defaults to user namespace)
- `private`: Privacy setting (default: false)
- `sdk`: SDK hint (optional, reserved for future use)

**Response:**
```json
{
  "url": "http://localhost:28080/models/username/my-model",
  "repo_id": "username/my-model"
}
```

**Status Codes:**
- `200 OK` - Repository created
- `400 Bad Request` - Repository already exists or name conflicts

**Validation:**
- Name conflicts checked (case-insensitive)
- User must have permission to use namespace
- Normalized name conflicts prevented (e.g., "My-Model" vs "my_model")

---

## Delete Repository

**Pattern:** `DELETE /api/repos/delete`

**Authentication:** Required (user or admin token)

**Request Body:**
```json
{
  "type": "model",
  "name": "my-model",
  "organization": null,
  "sdk": null
}
```

**Fields:**
- `type`: Repository type
- `name`: Repository name
- `organization`: Organization name (optional for users, required for admin)

**Admin Usage:**
- Accepts `X-Admin-Token` header
- Admin must specify `organization` parameter

**Response:**
```json
{
  "message": "Repository 'username/my-model' of type 'model' deleted."
}
```

**Cleanup Process:**
1. Deletes S3 storage (repo objects + unused LFS objects)
2. Deletes LakeFS repository
3. Deletes database records (CASCADE: files, commits, LFS history)

**Status Codes:**
- `200 OK` - Repository deleted
- `403 Forbidden` - No delete permission
- `404 Not Found` - Repository not found

**WARNING:** This operation is **IRREVERSIBLE**

---

## Move/Rename Repository

**Pattern:** `POST /api/repos/move`

**Authentication:** Required (user or admin token)

**Request Body:**
```json
{
  "fromRepo": "alice/old-name",
  "toRepo": "alice/new-name",
  "type": "model"
}
```

**Use Cases:**
1. **Rename:** `alice/model-v1` → `alice/model-v2`
2. **Move to org:** `alice/model` → `my-org/model`
3. **Transfer:** `alice/model` → `bob/model`

**Process:**
1. Validates source repository exists
2. Checks permissions (source delete + target namespace)
3. Validates quota for namespace changes
4. Migrates LakeFS repository with LFS handling
5. Updates database records
6. Cleans up old storage

**LFS Handling:**
- LFS files: Linked to same global S3 address (no duplication)
- Regular files: Downloaded and re-uploaded to new repo folder

**Response:**
```json
{
  "success": true,
  "url": "http://localhost:28080/models/bob/model",
  "message": "Repository moved from alice/model to bob/model"
}
```

**Quota Check:**
- Only for namespace changes (user → org, or user → user)
- Based on repository privacy (public/private)
- Includes full repository size

**Status Codes:**
- `200 OK` - Repository moved
- `400 Bad Request` - Invalid IDs, destination exists, or quota exceeded
- `403 Forbidden` - No permission
- `404 Not Found` - Source repository not found

---

## Squash Repository

**Pattern:** `POST /api/repos/squash`

**Authentication:** Required (user or admin token)

**Purpose:** Clear all commit history, keep only current state

**Request Body:**
```json
{
  "repo": "username/my-model",
  "type": "model"
}
```

**Process:**
1. Move repo to temporary name
2. Move back to original name
3. Result: All history cleared, single commit remains

**Benefits:**
- Reduces storage (clears old versions)
- Faster clone/fetch (no history)
- Clean slate for large repos

**Response:**
```json
{
  "success": true,
  "message": "Repository username/my-model squashed successfully. All commit history has been cleared."
}
```

**Important Notes:**
- **IRREVERSIBLE** - All history deleted
- LFS objects garbage-collected automatically
- Repository quota preserved
- Recalculates storage after squashing

**Status Codes:**
- `200 OK` - Repository squashed
- `403 Forbidden` - No permission
- `404 Not Found` - Repository not found
- `500 Internal Server Error` - Operation failed (attempts recovery)

---

## Get Repository Info

**Pattern:** `GET /api/{repo_type}s/{namespace}/{name}`

**Examples:**
- `GET /api/models/username/bert-base`
- `GET /api/datasets/org/imagenet`
- `GET /api/spaces/user/gradio-app`

**Authentication:** Optional (required for private repos)

**Response:**
```json
{
  "_id": 1,
  "id": "username/bert-base",
  "modelId": "username/bert-base",
  "author": "username",
  "sha": "commit_hash",
  "lastModified": "2025-01-20T10:15:32.123Z",
  "createdAt": "2025-01-15T08:00:00.000Z",
  "private": false,
  "disabled": false,
  "gated": false,
  "downloads": 150,
  "likes": 25,
  "tags": [],
  "pipeline_tag": null,
  "library_name": null,
  "siblings": [
    {
      "rfilename": "config.json",
      "size": 1024
    },
    {
      "rfilename": "model.safetensors",
      "size": 5368709120,
      "lfs": {
        "sha256": "abc123...",
        "size": 5368709120,
        "pointerSize": 134
      }
    }
  ],
  "storage": {
    "quota_bytes": 10737418240,
    "used_bytes": 5368710144,
    "available_bytes": 5368708096,
    "percentage_used": 50.0,
    "effective_quota_bytes": 10737418240,
    "is_inheriting": false
  }
}
```

**Fields:**
- `siblings`: Complete file list with LFS metadata
- `storage`: Only included for authenticated users
- `sha`: Latest commit on main branch

**See:** [HuggingFace-Compatible API](./huggingface-compatible.md)

---

## List Repositories

**Pattern:** `GET /api/{repo_type}s?author={author}&limit=50&sort=recent`

**Examples:**
- `GET /api/models?author=username&limit=50`
- `GET /api/datasets?sort=trending`

**Query Parameters:**
- `author` (optional): Filter by namespace
- `limit` (default: 50, max: 100000): Max results
- `sort` (optional): Sort order
  - `"recent"` (default): Latest first
  - `"likes"`: Most liked
  - `"downloads"`: Most downloaded
  - `"trending"`: Trending algorithm (7-day activity)
- `fallback` (default: true): Enable external sources

**Response:**
```json
[
  {
    "id": "username/model-name",
    "author": "username",
    "private": false,
    "sha": "commit_hash",
    "lastModified": "2025-01-20T10:15:32Z",
    "createdAt": "2025-01-15T08:00:00Z",
    "downloads": 150,
    "likes": 25,
    "gated": false,
    "tags": []
  }
]
```

**Privacy Filtering:**
- **Public repos:** Visible to everyone
- **Private repos:** Only owner/org members
- **Anonymous:** Only public repos

---

## List User Repositories

**Pattern:** `GET /api/users/{username}/repos?limit=100&sort=recent`

**Query Parameters:**
- `limit` (default: 100, max: 100000): Max per type
- `sort` (optional): `recent`, `likes`, `downloads`
- `fallback` (default: true): Enable external sources

**Response:**
```json
{
  "models": [
    {
      "id": "username/model1",
      "author": "username",
      "private": false,
      "sha": "commit_hash",
      "lastModified": "2025-01-20T10:15:32Z",
      "createdAt": "2025-01-15T08:00:00Z",
      "downloads": 150,
      "likes": 25
    }
  ],
  "datasets": [...],
  "spaces": [...]
}
```

**Use Case:** User/org profile pages

---

## Usage Examples

### Create and Upload

```python
import requests

API_BASE = "http://localhost:28080/api"
TOKEN = "your_token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Create repository
create_resp = requests.post(
    f"{API_BASE}/repos/create",
    json={
        "type": "model",
        "name": "my-bert",
        "private": False
    },
    headers=HEADERS
)

repo_url = create_resp.json()["url"]
print(f"Created: {repo_url}")

# Upload files (see File Upload API for details)
```

---

### Move Repository to Organization

```python
# Transfer personal repo to organization
move_resp = requests.post(
    f"{API_BASE}/repos/move",
    json={
        "fromRepo": "alice/my-model",
        "toRepo": "my-org/my-model",
        "type": "model"
    },
    headers=HEADERS
)

result = move_resp.json()
print(f"Moved: {result['message']}")
print(f"New URL: {result['url']}")
```

---

### Squash Large Repository

```python
# Check current size
repo_resp = requests.get(
    f"{API_BASE}/models/username/large-model",
    headers=HEADERS
)
repo = repo_resp.json()
print(f"Current size: {repo['storage']['used_bytes']:,} bytes")
print(f"Commits: {repo.get('commit_count', 'N/A')}")

# Squash to reduce storage
squash_resp = requests.post(
    f"{API_BASE}/repos/squash",
    json={
        "repo": "username/large-model",
        "type": "model"
    },
    headers=HEADERS
)

result = squash_resp.json()
print(result["message"])

# Check new size
repo_resp = requests.get(
    f"{API_BASE}/models/username/large-model",
    headers=HEADERS
)
new_size = repo_resp.json()['storage']['used_bytes']
print(f"New size: {new_size:,} bytes")
```

---

### Delete Repository

```python
# Delete repository
delete_resp = requests.delete(
    f"{API_BASE}/repos/delete",
    json={
        "type": "dataset",
        "name": "old-dataset",
        "organization": None
    },
    headers=HEADERS
)

print(delete_resp.json()["message"])
```

---

## Best Practices

**Naming:**
- Use lowercase with hyphens: `my-model-v2`
- Avoid special characters
- Keep names descriptive and concise
- Check availability first

**Privacy:**
- Start public for community models
- Use private for work-in-progress
- Consider organization for team projects

**Organization:**
- Use organizations for multi-user projects
- Separate personal vs team repos
- Use consistent naming within orgs

**Maintenance:**
- Squash rarely-updated repos to save storage
- Move abandoned repos to archive org
- Delete truly unused repos
- Monitor storage usage

---

## Next Steps

- [File Upload API](./file-upload.md) - Upload files to repos
- [Branches API](./branches.md) - Branch management
- [Organizations API](./organizations.md) - Org management
- [Quota API](./quota.md) - Storage quotas
