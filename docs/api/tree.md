---
title: File Tree API
description: Browse repository file trees and retrieve path information
icon: file-tree
---

# File Tree API

Browse repository file trees and retrieve detailed information about specific paths within repositories.

## Endpoints

### List Repository Tree

Get a flat list of files and folders in a repository.

**Endpoint:** `GET /{repo_type}s/{namespace}/{name}/tree/{revision}/{path}`

**Parameters:**

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `repo_type` | string | path | Yes | Repository type: `model`, `dataset`, or `space` |
| `namespace` | string | path | Yes | Repository namespace (username or organization) |
| `name` | string | path | Yes | Repository name |
| `revision` | string | path | No | Branch name or commit hash (default: `main`) |
| `path` | string | path | No | Path within repository (default: root `/`) |
| `recursive` | boolean | query | No | List files recursively (default: `false`) |
| `expand` | boolean | query | No | Include detailed metadata (default: `false`) |
| `fallback` | boolean | query | No | Enable fallback to external sources (default: `true`) |

**Authentication:** Optional (required for private repositories)

**Response:**

Returns a flat list of file and folder objects in HuggingFace-compatible format.

**File Object:**
```json
{
  "type": "file",
  "path": "config.json",
  "size": 1234,
  "oid": "abc123...",
  "lastModified": "2025-01-15T10:30:00.000Z",
  "lfs": {
    "oid": "sha256:def456...",
    "size": 1234,
    "pointerSize": 134
  }
}
```

**Directory Object:**
```json
{
  "type": "directory",
  "path": "models",
  "size": 5678900,
  "oid": "tree789...",
  "lastModified": "2025-01-15T10:30:00.000Z"
}
```

**Field Descriptions:**

- `type`: Object type (`file` or `directory`)
- `path`: Relative path from the repository root
- `size`: File size in bytes (for directories, sum of all contents)
- `oid`: Object identifier (SHA256 for LFS files, SHA1 for regular files, tree hash for directories)
- `lastModified`: ISO 8601 timestamp of last modification
- `lfs`: LFS metadata (only present for LFS files)
  - `oid`: SHA256 hash of file content
  - `size`: Original file size
  - `pointerSize`: Size of LFS pointer (typically 134 bytes)

**Example Request:**

```python
import requests

# List root directory
response = requests.get(
    "http://localhost:28080/models/myorg/mymodel/tree/main",
    headers={"Authorization": "Bearer YOUR_TOKEN"}
)
files = response.json()

# List specific folder recursively
response = requests.get(
    "http://localhost:28080/models/myorg/mymodel/tree/main/configs",
    params={"recursive": True}
)
files = response.json()
```

**Example Response:**

```json
[
  {
    "type": "file",
    "path": "README.md",
    "size": 2048,
    "oid": "a1b2c3d4...",
    "lastModified": "2025-01-15T10:30:00.000Z"
  },
  {
    "type": "file",
    "path": "model.safetensors",
    "size": 5368709120,
    "oid": "sha256:e5f6g7h8...",
    "lastModified": "2025-01-15T10:30:00.000Z",
    "lfs": {
      "oid": "sha256:e5f6g7h8...",
      "size": 5368709120,
      "pointerSize": 134
    }
  },
  {
    "type": "directory",
    "path": "configs",
    "size": 10240,
    "oid": "tree123...",
    "lastModified": "2025-01-15T10:30:00.000Z"
  }
]
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| 404 | Repository not found or revision not found |
| 403 | Private repository requires authentication |
| 500 | Internal server error |

---

### Get Paths Information

Get detailed information about specific paths in a repository.

**Endpoint:** `POST /{repo_type}s/{namespace}/{name}/paths-info/{revision}`

**Parameters:**

| Parameter | Type | Location | Required | Description |
|-----------|------|----------|----------|-------------|
| `repo_type` | string | path | Yes | Repository type: `model`, `dataset`, or `space` |
| `namespace` | string | path | Yes | Repository namespace (username or organization) |
| `name` | string | path | Yes | Repository name |
| `revision` | string | path | Yes | Branch name or commit hash |

**Request Body (Form Data):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `paths` | array[string] | Yes | List of paths to query |
| `expand` | boolean | No | Include extended metadata (default: `false`) |
| `fallback` | boolean | No | Enable fallback to external sources (default: `true`) |

**Authentication:** Optional (required for private repositories)

**Response:**

Returns a list of path information objects. Paths that don't exist are omitted from the response.

**File Info:**
```json
{
  "type": "file",
  "path": "config.json",
  "size": 1234,
  "oid": "abc123...",
  "lfs": {
    "oid": "sha256:def456...",
    "size": 1234,
    "pointerSize": 134
  },
  "last_commit": null,
  "security": null
}
```

**Directory Info:**
```json
{
  "type": "directory",
  "path": "models",
  "oid": "tree789...",
  "tree_id": "tree789...",
  "last_commit": null
}
```

**Example Request:**

```python
import requests

# Query multiple paths
response = requests.post(
    "http://localhost:28080/models/myorg/mymodel/paths-info/main",
    data={
        "paths": ["README.md", "config.json", "models/"],
        "expand": False
    },
    headers={"Authorization": "Bearer YOUR_TOKEN"}
)
paths_info = response.json()
```

**Example Response:**

```json
[
  {
    "type": "file",
    "path": "README.md",
    "size": 2048,
    "oid": "a1b2c3d4...",
    "lfs": null,
    "last_commit": null,
    "security": null
  },
  {
    "type": "file",
    "path": "config.json",
    "size": 512,
    "oid": "b2c3d4e5...",
    "lfs": null,
    "last_commit": null,
    "security": null
  },
  {
    "type": "directory",
    "path": "models",
    "oid": "tree456...",
    "tree_id": "tree456...",
    "last_commit": null
  }
]
```

**Notes:**

- Paths that don't exist are silently omitted from the response
- For directories, include trailing slash (`models/`) or omit it (`models`) - both work
- The `lfs` field is `null` for non-LFS files
- `last_commit` and `security` fields are reserved for future use

**Error Responses:**

| Status | Description |
|--------|-------------|
| 404 | Repository not found |
| 403 | Private repository requires authentication |
| 422 | Invalid request body |
| 500 | Internal server error |

---

## LFS Detection

Files are automatically marked as LFS based on repository-specific settings:

1. **Size threshold**: Files exceeding the threshold use LFS
2. **Suffix rules**: Files with specific extensions (e.g., `.safetensors`, `.bin`) always use LFS
3. **Server defaults**: 32 default suffix rules for ML models, archives, and media files

See the [Repository Settings API](./settings.md) for managing LFS configuration.

---

## Usage Examples

### Python with `requests`

```python
import requests

BASE_URL = "http://localhost:28080"
TOKEN = "YOUR_TOKEN"

headers = {"Authorization": f"Bearer {TOKEN}"}

# List all files in repository
response = requests.get(
    f"{BASE_URL}/models/myorg/mymodel/tree/main",
    headers=headers
)
all_files = response.json()

# List files in specific folder
response = requests.get(
    f"{BASE_URL}/models/myorg/mymodel/tree/main/configs",
    headers=headers
)
config_files = response.json()

# Recursive listing
response = requests.get(
    f"{BASE_URL}/models/myorg/mymodel/tree/main",
    params={"recursive": True},
    headers=headers
)
all_files_recursive = response.json()

# Check specific paths
response = requests.post(
    f"{BASE_URL}/models/myorg/mymodel/paths-info/main",
    data={"paths": ["README.md", "model.safetensors"]},
    headers=headers
)
paths = response.json()

# Filter for LFS files only
lfs_files = [f for f in all_files if f.get("lfs") is not None]
print(f"Found {len(lfs_files)} LFS files")
```

### Python with `huggingface_hub`

```python
from huggingface_hub import HfApi

api = HfApi(endpoint="http://localhost:28080", token="YOUR_TOKEN")

# List files (uses tree endpoint internally)
files = api.list_repo_files(
    repo_id="myorg/mymodel",
    repo_type="model",
    revision="main"
)

# Get file info
file_info = api.repo_info(
    repo_id="myorg/mymodel",
    repo_type="model",
    files_metadata=True
)
```

### JavaScript/TypeScript

```javascript
const BASE_URL = "http://localhost:28080";
const TOKEN = "YOUR_TOKEN";

// List repository tree
async function listFiles(repoId, path = "", recursive = false) {
  const response = await fetch(
    `${BASE_URL}/models/${repoId}/tree/main/${path}?recursive=${recursive}`,
    {
      headers: { Authorization: `Bearer ${TOKEN}` }
    }
  );
  return await response.json();
}

// Get specific paths info
async function getPathsInfo(repoId, paths) {
  const formData = new FormData();
  paths.forEach(path => formData.append("paths", path));

  const response = await fetch(
    `${BASE_URL}/models/${repoId}/paths-info/main`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${TOKEN}` },
      body: formData
    }
  );
  return await response.json();
}

// Usage
const files = await listFiles("myorg/mymodel");
const pathsInfo = await getPathsInfo("myorg/mymodel", [
  "README.md",
  "config.json"
]);
```

---

## Next Steps

- [Repository Info API](../API.md#repository-info) - Get repository metadata
- [File Operations API](../API.md#file-operations) - Upload and download files
- [Repository Settings API](./settings.md) - Configure LFS thresholds and rules
- [Commits API](../API.md#commits) - View commit history
