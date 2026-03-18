---
title: HuggingFace-Compatible API
description: Endpoints compatible with huggingface_hub library
icon: i-carbon-cloud-app
---

# HuggingFace-Compatible API

KohakuHub implements HuggingFace Hub API for compatibility with `huggingface_hub`, `transformers`, and `diffusers` libraries.

---

## Repository Info

### Get Repository Information

**Pattern:** `GET /api/{repo_type}s/{namespace}/{name}`

**Examples:**
- `GET /api/models/username/bert-base`
- `GET /api/datasets/org/imagenet`
- `GET /api/spaces/user/gradio-app`

**Query Parameters:**
- None

**Request Headers:**
- `Authorization: Bearer {token}` (optional, for private repos)

**Response:**
```json
{
  "id": "username/bert-base",
  "author": "username",
  "sha": "abc123def456...",
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
      "size": 5000000000,
      "lfs": {
        "sha256": "def789...",
        "size": 5000000000,
        "pointerSize": 134
      }
    }
  ],
  "storage": {
    "quota_bytes": 107374182400,
    "used_bytes": 5000001024,
    "available_bytes": 102374181376,
    "percentage_used": 4.66,
    "effective_quota_bytes": 107374182400,
    "is_inheriting": false
  }
}
```

**Notes:**
- `siblings`: Full file list with LFS info
- `storage`: Only included for authenticated users
- `sha`: Latest 40-character commit hash on main branch
- Compatible with `transformers.from_pretrained()` and `diffusers`

---

## Repository Listing

### List Repositories by Type

**Pattern:** `GET /api/{repo_type}s`

**Examples:**
- `GET /api/models?author=username&limit=50&sort=recent`
- `GET /api/datasets?sort=trending&limit=20`

**Query Parameters:**
- `author` (optional): Filter by namespace
- `limit` (optional): Max results (default: 50, max: 100000)
- `sort` (optional): `recent` | `likes` | `downloads` | `trending` (default: recent)
- `fallback` (optional): Enable external sources (default: true)

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

---

### List User Repositories

**Pattern:** `GET /api/users/{username}/repos`

**Query Parameters:**
- `limit` (optional): Max per type (default: 100)
- `sort` (optional): `recent` | `likes` | `downloads`

**Response:**
```json
{
  "models": [...],
  "datasets": [...],
  "spaces": [...]
}
```

---

## File Operations

### File Download/Resolution

**Pattern:** `GET /{repo_type}s/{namespace}/{name}/resolve/{revision}/{path}`

**Examples:**
- `GET /models/username/bert/resolve/main/config.json`
- `GET /datasets/org/data/resolve/v1.0/train.csv`

**Path Parameters:**
- `repo_type`: models | datasets | spaces
- `namespace`: User or organization name
- `name`: Repository name
- `revision`: Branch name or commit hash
- `path`: File path in repository

**Response:**
- `302 Found` - Redirect to S3 presigned URL
- `404 Not Found` - File doesn't exist

**Response Headers:**
- `X-Repo-Commit`: 40-character hexadecimal commit hash
- `X-Linked-Etag`: File SHA256
- `X-Linked-Size`: File size in bytes
- `ETag`: File SHA256
- `Content-Disposition`: attachment; filename="..."

**Notes:**
- No body returned (302 redirect only)
- Client follows redirect to download from S3
- Presigned URL valid for 24 hours
- Download tracking happens in background

---

### File Metadata (HEAD)

**Pattern:** `HEAD /{repo_type}s/{namespace}/{name}/resolve/{revision}/{path}`

**Response:**
- `200 OK` with headers (no body)
- Same headers as GET endpoint

**Use case:** Check file exists and get metadata without downloading

---

### Preupload Check

**Pattern:** `POST /{repo_type}s/{namespace}/{name}/preupload/{revision}`

**Purpose:** Check if files should use LFS and if they already exist (deduplication)

**Request Body:**
```json
{
  "files": [
    {
      "path": "model.safetensors",
      "size": 5000000000,
      "sha256": "abc123...",  // Optional but recommended
      "sample": "base64..."    // Optional, for small files
    },
    {
      "path": "config.json",
      "size": 512
    }
  ]
}
```

**Response:**
```json
{
  "files": [
    {
      "path": "model.safetensors",
      "uploadMode": "lfs",
      "shouldIgnore": false
    },
    {
      "path": "config.json",
      "uploadMode": "regular",
      "shouldIgnore": true
    }
  ]
}
```

**Field Explanations:**
- `uploadMode`:
  - `lfs`: Use Git LFS (file > threshold OR matches suffix rules)
  - `regular`: Use regular commit (small file)
- `shouldIgnore`:
  - `true`: File with same content already exists (skip upload)
  - `false`: File is new or changed (upload required)

**Deduplication Logic:**
1. If `sha256` provided: Compare with existing file SHA256
2. If `sample` provided (small files): Compare sample content
3. If neither: Always upload

---

## Revision Info

### Get Revision Details

**Pattern:** `GET /{repo_type}s/{namespace}/{name}/revision/{revision}`

**Examples:**
- `GET /models/user/bert/revision/main`
- `GET /datasets/org/data/revision/v2.0`

**Query Parameters:**
- `expand` (optional): Fields to expand (reserved for future use)

**Response:**
```json
{
  "id": "username/repo",
  "author": "username",
  "sha": "40_char_commit_hash",
  "lastModified": "2025-01-20T10:15:32Z",
  "createdAt": "2025-01-15T08:00:00Z",
  "private": false,
  "downloads": 150,
  "likes": 25,
  "gated": false,
  "files": [],
  "type": "model",
  "revision": "main",
  "commit": {
    "oid": "commit_hash",
    "date": 1737360932
  },
  "xetEnabled": false
}
```

**Notes:**
- `files` is empty (use `/tree` endpoint for file list)
- `commit.date`: Unix timestamp

---

## Privacy & Permissions

### Access Control Rules

**Public repositories:**
- ✅ Anyone can read
- ⚠️ Only owner/org members can write

**Private repositories:**
- ⚠️ Only owner/org members can read
- ⚠️ Only owner/org members can write

**Anonymous requests:**
- ✅ Can access public repositories
- ❌ Cannot access private repositories

**Authentication:**
- Session cookie: `session_id` (for browser)
- Bearer token: `Authorization: Bearer {token}` (for API/CLI)

---

## Error Responses

**Standard HuggingFace error format:**

```json
{
  "error": "Error message",
  "requestId": "optional-request-id"
}
```

**Common status codes:**
- `200 OK` - Success
- `302 Found` - Redirect to S3 (download endpoints)
- `400 Bad Request` - Invalid input
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - No permission
- `404 Not Found` - Repository or file not found
- `413 Payload Too Large` - Quota exceeded
- `500 Internal Server Error` - Server error

**Custom headers:**
- `X-Error-Code`: Machine-readable error code
- `X-Request-Id`: Request tracking ID (if available)

---

## Compatibility Notes

### Works with these libraries:

✅ **`huggingface_hub`**
```python
from huggingface_hub import hf_hub_download

file = hf_hub_download(
    repo_id="username/model",
    filename="config.json",
    repo_type="model",
    endpoint="http://localhost:28080"
)
```

✅ **`transformers`**
```python
from transformers import AutoModel

model = AutoModel.from_pretrained(
    "username/bert-base",
    trust_remote_code=True,
    use_auth_token="your_token"
)
# Set HF_ENDPOINT=http://localhost:28080
```

HF-compatible responses expose 40-character commit hashes even when the backing
LakeFS commit IDs are longer. Internal and non-HF APIs may still use the raw
LakeFS commit ID.

✅ **`diffusers`**
```python
from diffusers import StableDiffusionPipeline

pipe = StableDiffusionPipeline.from_pretrained(
    "username/sd-model",
    use_auth_token="your_token"
)
# Set HF_ENDPOINT=http://localhost:28080
```

### Differences from HuggingFace Hub:

**Not implemented:**
- Gated repositories (always `gated: false`)
- Pipeline tags (always `null`)
- Model cards parsing (use README.md YAML frontmatter)
- Discussions/Community features

**Extensions:**
- Storage quota information (KohakuHub-specific)
- Organization support with roles
- Per-repository LFS settings
- Fallback to external sources

---

## Rate Limiting

**No rate limits on HuggingFace-compatible endpoints** (by design for library compatibility)

**Recommended:** Use reverse proxy (nginx) for rate limiting in production

---

## Next: See other API docs

- [Authentication API](./authentication.md)
- [Admin API](./admin.md)
- [Git/LFS API](./git-lfs.md) (TODO)
- [File Upload API](./file-upload.md) (TODO)
