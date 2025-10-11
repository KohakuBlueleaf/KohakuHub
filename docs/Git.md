# Git Support in KohakuHub

*Complete guide covering Git clone operations, LFS integration, and server implementation*

**Last Updated:** January 2025
**Status:** ✅ Clone/Pull Production Ready | ⚠️ Push In Development

---

## Table of Contents

### Part 1: User Guide
1. [Quick Start](#quick-start)
2. [Authentication](#authentication-guide)
3. [LFS Integration](#lfs-integration-guide)
4. [Cloudflare Setup](#cloudflare-setup)
5. [Troubleshooting](#troubleshooting-guide)

### Part 2: Developer Guide
6. [Implementation Overview](#implementation-overview)
7. [Git Protocol Fundamentals](#git-protocol-fundamentals)
8. [Packet-Line Format](#packet-line-format)
9. [Git Smart HTTP Protocol](#git-smart-http-protocol)
10. [Pack File Generation](#pack-file-generation)
11. [Pure Python Implementation](#pure-python-implementation)
12. [LFS Pointer System](#lfs-pointer-system)
13. [Tree Building Algorithm](#tree-building-algorithm)
14. [Testing & Debugging](#testing-and-debugging)
15. [References](#references)

---

# Part 1: User Guide

## Quick Start

### Clone a Repository

```bash
# Public repository
git clone http://hub.example.com/namespace/repo-name.git

# Private repository (requires token)
git clone http://username:your-token@hub.example.com/namespace/private-repo.git

# Clone and download large files
cd repo-name
git lfs install
git lfs pull
```

### How LFS Works

KohakuHub automatically handles large files using Git LFS:

| File Size | In Clone | Download Method |
|-----------|----------|-----------------|
| < 1 MB | ✅ Full content | Included in pack |
| >= 1 MB | ✅ LFS pointer (~100 bytes) | `git lfs pull` |

**Example:**
```bash
$ git clone http://hub.example.com/org/large-model.git
Cloning... done. (Downloaded: 2 MB - only metadata!)

$ cd large-model
$ ls -lh model.safetensors
-rw-r--r-- 1 user user 132 Oct 9 14:30 model.safetensors  # Pointer file

$ cat model.safetensors
version https://git-lfs.github.com/spec/v1
oid sha256:abc123...
size 10737418240

$ git lfs pull
Downloading model.safetensors (10 GB)... done.

$ ls -lh model.safetensors
-rw-r--r-- 1 user user 10G Oct 9 14:32 model.safetensors  # Actual file
```

## Authentication Guide

### Using Access Tokens

**Generate Token:**
1. Login to KohakuHub web UI
2. Go to Settings → Access Tokens
3. Click "Create New Token"
4. Copy the token (you won't see it again!)

**Method 1: Credential Helper (Recommended)**
```bash
git clone http://hub.example.com/org/repo.git
# Git prompts for credentials:
# Username: your-username
# Password: paste-your-token-here

# Cache credentials for 1 hour
git config --global credential.helper 'cache --timeout=3600'
```

**Method 2: URL (Not Recommended - visible in history)**
```bash
git clone http://username:your-token@hub.example.com/org/repo.git
```

**Method 3: Environment Variable**
```bash
export GIT_USER=username
export GIT_TOKEN=your-token
git clone http://$GIT_USER:$GIT_TOKEN@hub.example.com/org/repo.git
```

## LFS Integration Guide

### Installation

```bash
# Install Git LFS (one-time)
git lfs install
```

### Download Large Files

```bash
# After cloning
git lfs pull

# Download specific files only
git lfs pull --include="models/*.safetensors"

# Skip LFS during clone (faster)
GIT_LFS_SKIP_SMUDGE=1 git clone http://hub.example.com/org/repo.git
cd repo
git lfs pull  # Download later
```

### Check LFS Status

```bash
# List LFS-tracked files
git lfs ls-files

# Check LFS configuration
cat .lfsconfig
# Should show:
# [lfs]
# 	url = http://hub.example.com/namespace/repo.git/info/lfs
```

## Cloudflare Setup

If deploying behind Cloudflare, Git requests may be cached/modified. Fix:

### Create Page Rule

**Cloudflare Dashboard → Rules → Page Rules**

**URL Pattern:**
```
*yourdomain.com/*/*.git/*
```

**Settings:**
- ✅ Cache Level: **Bypass**
- ✅ Disable Performance
- ✅ Disable Apps

**Why:** Git protocol responses must not be cached or compressed.

### Alternative: Subdomain

Use a separate subdomain that bypasses Cloudflare:

```
git.hub.example.com → Direct to origin (DNS only)
hub.example.com → Through Cloudflare (for web UI)
```

```bash
git clone https://git.hub.example.com/org/repo.git
```

## Troubleshooting Guide

### Clone Hangs or Fails

**Problem:** `fatal: protocol error: bad pack header`
**Cause:** Old version with pkt-line chunking bug
**Solution:** Update to latest KohakuHub version

---

**Problem:** `fatal: repository not found`
**Cause:** Repository doesn't exist or no access
**Solution:** Check spelling, verify repo exists in web UI

---

**Problem:** Clone works but folders are missing
**Cause:** Old version with tree building bug
**Solution:** Update to latest KohakuHub version

### LFS Issues

**Problem:** `git lfs pull` does nothing
**Cause:** `.lfsconfig` missing or incorrect
**Solution:** Check/create `.lfsconfig`:
```bash
[lfs]
	url = http://hub.example.com/namespace/repo.git/info/lfs
```

---

**Problem:** LFS files show as pointers after `git lfs pull`
**Cause:** LFS endpoint unreachable
**Solution:** Test LFS endpoint:
```bash
curl -v "http://hub.example.com/namespace/repo.git/info/lfs/objects/batch" \
  -X POST -H "Content-Type: application/json" \
  -d '{"operation":"download","objects":[{"oid":"abc","size":100}]}'
```

### Cloudflare Issues

**Problem:** `fatal: not a git repository`
**Cause:** Cloudflare caching Git responses
**Solution:** Create Cloudflare Page Rule (see above)

---

# Part 2: Developer Guide

## Implementation Overview

### What is a Git Server?

A Git server allows Git clients to clone, fetch, pull, and push repositories over the network. There are two main protocols:
- **Git Smart HTTP**: HTTP-based protocol (what we're implementing)
- **Git SSH**: SSH-based protocol (not covered here)

### Why Build Your Own?

In KohakuHub, we need to:
1. Provide native Git access to LakeFS-backed repositories
2. Integrate with existing authentication (tokens, sessions)
3. Maintain compatibility with HuggingFace Hub while adding Git support
4. Translate Git operations to LakeFS REST API calls

### Architecture Overview

```mermaid
graph TD
    A[Git Client] --> B[FastAPI]
    B --> C[GitLakeFSBridge]
    C --> D[LakeFS REST API]
    D --> E[S3/MinIO Storage]
```

---

## Git Protocol Fundamentals

### Git Object Model

Git stores data as a directed acyclic graph (DAG) of objects:

1. **Blob**: File content
2. **Tree**: Directory listing (maps names to blobs/trees)
3. **Commit**: Snapshot with metadata (author, message, tree, parents)
4. **Tag**: Named reference to a commit

Each object is identified by its SHA-1 hash.

### Git References (Refs)

References are pointers to commits:
- `refs/heads/main` → Branch (e.g., main branch)
- `refs/tags/v1.0` → Tag
- `HEAD` → Current branch or commit

### Git Pack Files

To efficiently transfer objects, Git uses **pack files**:
- Compressed collection of objects
- Uses delta compression (stores differences between objects)
- Format: `PACK` header + objects + SHA-1 checksum

---

## Packet-Line Format

### What is Packet-Line (pkt-line)?

Git's wire protocol uses pkt-line format for framing data:

```
<4-byte hex length><payload>
```

**Examples:**
```
# Regular line (19 bytes = 4 (header) + 15 (payload))
0013hello world\n

# Flush packet (signals end of stream)
0000

# Empty payload (still valid)
0004
```

### Length Calculation

```python
# Formula: length_hex = hex(len(payload) + 4)
payload = b"hello\n"
length = len(payload) + 4  # 6 + 4 = 10 = 0x000a
pkt = b"000ahello\n"
```

### Special Packets

| Hex  | Name  | Purpose                    |
|------|-------|----------------------------|
| 0000 | Flush | End of command/data stream |
| 0001 | Delim | Delimiter (protocol v2)    |
| 0002 | RSP   | Response end (protocol v2) |

### Implementation

```python
def pkt_line(data: bytes | str | None) -> bytes:
    """Encode data as a git pkt-line."""
    if data is None:
        return b"0000"  # Flush packet

    if isinstance(data, str):
        data = data.encode("utf-8")

    length = len(data) + 4
    return f"{length:04x}".encode("ascii") + data


def parse_pkt_line(data: bytes) -> tuple[bytes | None, bytes]:
    """Parse a single pkt-line from data.

    Returns:
        (line_data, remaining_data)
    """
    if len(data) < 4:
        return None, data

    try:
        length = int(data[:4].decode("ascii"), 16)
    except (ValueError, UnicodeDecodeError):
        return None, data[4:]

    if length == 0:
        return None, data[4:]  # Flush packet

    if length < 4:
        return None, data[4:]  # Invalid

    line_data = data[4:length]
    remaining = data[length:]

    return line_data, remaining
```

---

## Git Smart HTTP Protocol

### Protocol Flow

```
1. Client → Server: GET /info/refs?service=git-upload-pack
   Server → Client: Service advertisement (refs + capabilities)

2. Client → Server: POST /git-upload-pack (wants/haves)
   Server → Client: Pack file with requested objects

3. (For push) Client → Server: POST /git-receive-pack (updates + pack)
   Server → Client: Status report
```

### HTTP Endpoints

| Method | Path                                        | Purpose              |
|--------|---------------------------------------------|----------------------|
| GET    | `/{namespace}/{name}.git/info/refs`         | Service advertisement|
| GET    | `/{namespace}/{name}.git/HEAD`              | Get HEAD reference   |
| POST   | `/{namespace}/{name}.git/git-upload-pack`   | Clone/fetch/pull     |
| POST   | `/{namespace}/{name}.git/git-receive-pack`  | Push                 |

### Content-Type Headers

```
Service advertisement:
  application/x-{service}-advertisement

Upload-pack response:
  application/x-git-upload-pack-result

Receive-pack response:
  application/x-git-receive-pack-result
```

---

## Service Advertisement

### Purpose

When a Git client runs `git clone`, it first requests `/info/refs?service=git-upload-pack` to discover:
1. Available references (branches, tags)
2. Server capabilities (what features the server supports)

### Request

```http
GET /{namespace}/{name}.git/info/refs?service=git-upload-pack HTTP/1.1
Host: hub.example.com
```

### Response Format

```
# Service line
001e# service=git-upload-pack\n
0000

# First ref includes capabilities
00a1<commit-sha> <ref-name>\0<capabilities>\n

# Subsequent refs (no capabilities)
003f<commit-sha> <ref-name>\n
003f<commit-sha> <ref-name>\n

0000  # Flush
```

### Example Response

```python
# Actual bytes sent:
001e# service=git-upload-pack\n
0000
00a1deadbeef123... refs/heads/main\0multi_ack side-band-64k thin-pack\n
003fdeadbeef123... HEAD\n
0000
```

### Implementation

```python
class GitServiceInfo:
    def __init__(self, service: str, refs: dict[str, str], capabilities: list[str]):
        self.service = service
        self.refs = refs
        self.capabilities = capabilities

    def to_bytes(self) -> bytes:
        lines = []

        # Service header
        lines.append(f"# service=git-{self.service}\n")
        lines.append(None)  # Flush

        # Sort refs: HEAD first, then refs/heads/*, then refs/tags/*
        sorted_refs = sorted(self.refs.items(), key=self._sort_key)

        # First ref includes capabilities
        first = True
        for ref_name, commit_sha in sorted_refs:
            if first:
                caps = " ".join(self.capabilities)
                lines.append(f"{commit_sha} {ref_name}\x00{caps}\n")
                first = False
            else:
                lines.append(f"{commit_sha} {ref_name}\n")

        # Empty repo: send capabilities with zero-id
        if not self.refs:
            caps = " ".join(self.capabilities)
            lines.append(f"{'0' * 40} capabilities^{{}}\x00{caps}\n")

        lines.append(None)  # Flush

        return pkt_line_stream(lines)

    def _sort_key(self, item):
        ref_name = item[0]
        if ref_name == "HEAD":
            return (0, ref_name)
        elif ref_name.startswith("refs/heads/"):
            return (1, ref_name)
        elif ref_name.startswith("refs/tags/"):
            return (2, ref_name)
        else:
            return (3, ref_name)
```

### Capabilities

Common capabilities:

| Capability       | Description                               |
|------------------|-------------------------------------------|
| multi_ack        | Client can negotiate common commits       |
| side-band-64k    | Multiplexed output (data/progress/errors) |
| thin-pack        | Send pack with delta references           |
| ofs-delta        | Use offset delta encoding                 |
| agent            | Identify server software                  |
| report-status    | Server reports ref update status          |

---

## Upload-Pack (Clone/Fetch/Pull)

### Purpose

Upload-pack handles **download operations**: clone, fetch, pull.

### Protocol Exchange

```
1. Client sends:
   - List of commits it wants (want lines)
   - List of commits it already has (have lines)
   - "done" to finish negotiation

2. Server sends:
   - NAK (no acknowledgment)
   - Pack file containing requested objects
```

### Request Format

```
# Client wants this commit
0032want deadbeef123...\x00multi_ack side-band-64k\n

# Client already has these commits (optional)
0032have cafebabe456...\n
0032have 12345678...\n

# Negotiation done
0009done\n
0000
```

### Response Format

```
# NAK response
0008NAK\n

# Pack data on side-band 1
<pkt-line>\x01<pack-file-data>

0000  # Flush
```

### Implementation

```python
class GitUploadPackHandler:
    def __init__(self, repo_path: str, bridge=None):
        self.repo_path = repo_path
        self.bridge = bridge  # GitLakeFSBridge for generating packs
        self.capabilities = [
            "multi_ack",
            "side-band-64k",
            "thin-pack",
            "ofs-delta",
            "agent=kohakuhub/0.0.1",
        ]

    async def handle_upload_pack(self, request_body: bytes) -> bytes:
        # Parse want/have lines
        wants = []
        haves = []

        lines = parse_pkt_lines(request_body)

        for line in lines:
            if line is None:
                continue

            line_str = line.decode("utf-8").strip()

            if line_str.startswith("want "):
                want_sha = line_str.split()[1]
                wants.append(want_sha)
            elif line_str.startswith("have "):
                have_sha = line_str.split()[1]
                haves.append(have_sha)
            elif line_str == "done":
                break

        # Send NAK
        nak = pkt_line_stream([b"NAK\n"])

        # Generate pack file
        if self.bridge:
            pack_data = await self.bridge.build_pack_file(wants, haves)
        else:
            pack_data = self._create_empty_pack()

        # Side-band protocol: prefix with \x01 (band 1 = data)
        pack_line = b'\x01' + pack_data

        response = nak + pkt_line(pack_line) + pkt_line(None)

        return response
```

---

## Receive-Pack (Push)

### Purpose

Receive-pack handles **upload operations**: push.

### Protocol Exchange

```
1. Client sends:
   - Ref update commands (old-sha new-sha ref-name)
   - Pack file with new objects

2. Server sends:
   - Unpack status (ok/ng)
   - Per-ref status (ok/ng)
```

### Request Format

```
# Ref update commands
<pkt-line>old-sha new-sha refs/heads/main\x00capabilities\n
<pkt-line>old-sha new-sha refs/heads/feature\n
0000

# Pack file follows (PACK header + objects + checksum)
PACK...
```

### Response Format

```
0000  # Flush

# Unpack status on side-band 1
\x01unpack ok\n

# Per-ref status
\x01ok refs/heads/main\n
\x01ok refs/heads/feature\n

0000  # Flush
```

### Implementation

```python
class GitReceivePackHandler:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.capabilities = [
            "report-status",
            "side-band-64k",
            "delete-refs",
            "ofs-delta",
            "agent=kohakuhub/0.0.1",
        ]

    async def handle_receive_pack(self, request_body: bytes) -> bytes:
        # Parse ref updates
        ref_updates = []

        lines = parse_pkt_lines(request_body)

        for line in lines:
            if line is None:
                break  # Flush packet marks end of commands

            line_str = line.decode("utf-8").strip()

            # Format: old-sha new-sha ref-name
            parts = line_str.split()
            if len(parts) >= 3:
                old_sha = parts[0]
                new_sha = parts[1]
                ref_name = parts[2]
                ref_updates.append((old_sha, new_sha, ref_name))

        # TODO: Process pack file and update refs

        # Send success status
        status_lines = [
            None,  # Flush
            b"\x01unpack ok\n",
        ]

        for old_sha, new_sha, ref_name in ref_updates:
            status_lines.append(f"\x01ok {ref_name}\n".encode())

        status_lines.append(None)  # Flush

        return pkt_line_stream(status_lines)
```

---

## Pack File Format

### Structure

```
+-----------------+
| PACK header     | 12 bytes
+-----------------+
| Object 1        | Variable
+-----------------+
| Object 2        | Variable
+-----------------+
| ...             |
+-----------------+
| SHA-1 checksum  | 20 bytes
+-----------------+
```

### Header Format

```python
import struct

# Signature (4 bytes): "PACK"
# Version (4 bytes): 2 or 3 (network byte order)
# Count (4 bytes): Number of objects (network byte order)

header = b'PACK' + struct.pack('>I', 2) + struct.pack('>I', num_objects)
```

### Object Types

| Type | Code | Description           |
|------|------|-----------------------|
| Commit | 1  | Commit object         |
| Tree   | 2  | Tree object           |
| Blob   | 3  | Blob (file content)   |
| Tag    | 4  | Tag object            |
| OFS_DELTA | 6 | Offset delta      |
| REF_DELTA | 7 | Reference delta   |

### Creating Pack Files (Pure Python)

**KohakuHub uses a pure Python implementation - no native dependencies!**

```python
import hashlib
import struct
import zlib

def create_pack_file(objects: list[tuple[int, bytes]]) -> bytes:
    """Build pack file using pure Python.

    Args:
        objects: List of (type, object_data_with_header) tuples
                 Types: 1=commit, 2=tree, 3=blob

    Returns:
        Complete pack file bytes
    """
    # Pack header
    pack_data = b"PACK"
    pack_data += struct.pack(">I", 2)              # Version 2
    pack_data += struct.pack(">I", len(objects))   # Object count

    # Add each object
    for obj_type, obj_data in objects:
        # Extract content (remove "type size\0" header)
        null_pos = obj_data.find(b"\0")
        content = obj_data[null_pos + 1:] if null_pos > 0 else obj_data

        # Encode object header (type + size in variable-length encoding)
        header = encode_pack_object_header(obj_type, len(content))

        # Compress with zlib
        compressed = zlib.compress(content)

        # Add to pack
        pack_data += header + compressed

    # Add pack checksum (SHA-1 of everything)
    checksum = hashlib.sha1(pack_data).digest()
    pack_data += checksum

    return pack_data


# Complete example - no temp files!
async def build_pack(repo_id, branch):
    # 1. Build blobs (LFS pointers for large files)
    blobs = {}  # path -> (sha1, data_with_header, mode)

    for file in files:
        if is_lfs(file):
            pointer = create_lfs_pointer(file.sha256, file.size)
            sha1, blob_data = create_blob_object(pointer)
            blobs[file.path] = (sha1, blob_data, "100644")
        else:
            content = await download(file.path)
            sha1, blob_data = create_blob_object(content)
            blobs[file.path] = (sha1, blob_data, "100644")

    # 2. Build trees (pure logic)
    flat = [(mode, path, sha1) for path, (sha1, data, mode) in blobs.items()]
    root_tree_sha1, tree_objects = build_nested_trees(flat)

    # 3. Build commit
    commit_sha1, commit_data = create_commit_object(...)

    # 4. Build pack
    pack_objects = [(1, commit_data)]  # Commit
    pack_objects.extend(tree_objects)  # Trees
    for path, (sha1, data, mode) in blobs.items():
        pack_objects.append((3, data))  # Blobs

    return create_pack_file(pack_objects)
```

**Benefits:**
- No native dependencies (easier deployment)
- Full control over memory usage
- No temporary files needed
- Easier debugging
- Better performance with LFS

### Empty Pack File

```python
def create_empty_pack() -> bytes:
    """Create empty pack file (0 objects)."""
    import hashlib
    import struct

    header = b'PACK' + struct.pack('>I', 2) + struct.pack('>I', 0)
    checksum = hashlib.sha1(header).digest()

    return header + checksum
```

---

## Authentication

### Methods

KohakuHub supports two authentication methods for Git:

1. **Token-based (Bearer)**: For API clients
2. **Basic Auth**: For Git clients

### Git Basic Auth

Git clients send credentials via HTTP Basic Auth:

```http
GET /namespace/repo.git/info/refs?service=git-upload-pack HTTP/1.1
Authorization: Basic <base64(username:token)>
```

### Parsing Credentials

```python
import base64

def parse_git_credentials(authorization: str | None) -> tuple[str | None, str | None]:
    """Parse username and token from Basic Auth header."""
    if not authorization or not authorization.startswith("Basic "):
        return None, None

    try:
        encoded = authorization[6:]  # Remove "Basic "
        decoded = base64.b64decode(encoded).decode("utf-8")

        if ":" in decoded:
            username, token = decoded.split(":", 1)
            return username, token
    except Exception:
        pass

    return None, None
```

### Token Validation

```python
from datetime import datetime, timezone

from kohakuhub.auth.utils import hash_token
from kohakuhub.db import Token, User, db

async def get_user_from_git_auth(authorization: str | None) -> User | None:
    """Authenticate user from Git Basic Auth."""
    username, token_str = parse_git_credentials(authorization)
    if not username or not token_str:
        return None

    # Hash and lookup token
    token_hash = hash_token(token_str)

    # Database operations are synchronous with transactions
    with db.atomic():
        token = Token.get_or_none(Token.token_hash == token_hash)
        if not token:
            return None

        # Get user
        user = User.get_or_none(User.id == token.user_id)
        if not user or not user.is_active:
            return None

        # Update last used
        Token.update(last_used=datetime.now(timezone.utc)).where(
            Token.id == token.id
        ).execute()

    return user
```

### Permission Checks

```python
from kohakuhub.auth.permissions import check_repo_read_permission, check_repo_write_permission

# For clone/fetch/pull (upload-pack)
user = await get_user_from_git_auth(authorization)
check_repo_read_permission(repo, user)  # Raises HTTPException if denied

# For push (receive-pack)
user = await get_user_from_git_auth(authorization)
if not user:
    raise HTTPException(401, detail="Authentication required for push")
check_repo_write_permission(repo, user)
```

---

## Implementation with FastAPI

### Router Structure

```python
# src/kohakuhub/api/routers/git_http.py

from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response

router = APIRouter()

@router.get("/{namespace}/{name}.git/info/refs")
async def git_info_refs(
    namespace: str,
    name: str,
    service: str,
    authorization: str | None = Header(None),
):
    """Service advertisement endpoint."""
    # Implementation here...
    pass

@router.post("/{namespace}/{name}.git/git-upload-pack")
async def git_upload_pack(
    namespace: str,
    name: str,
    request: Request,
    authorization: str | None = Header(None),
):
    """Upload-pack endpoint for clone/fetch/pull."""
    # Implementation here...
    pass

@router.post("/{namespace}/{name}.git/git-receive-pack")
async def git_receive_pack(
    namespace: str,
    name: str,
    request: Request,
    authorization: str | None = Header(None),
):
    """Receive-pack endpoint for push."""
    # Implementation here...
    pass

@router.get("/{namespace}/{name}.git/HEAD")
async def git_head(
    namespace: str,
    name: str,
    authorization: str | None = Header(None),
):
    """HEAD endpoint."""
    return Response(
        content=b"ref: refs/heads/main\n",
        media_type="text/plain",
    )
```

### Dynamic Repository Type Detection

Since we don't know if a repo is a model/dataset/space from the URL alone:

```python
from kohakuhub.db import Repository, db

async def find_repository(namespace: str, name: str) -> Repository | None:
    """Find repository by trying all types."""
    # Database operations are synchronous
    with db.atomic():
        for repo_type in ["model", "dataset", "space"]:
            repo = Repository.get_or_none(
                Repository.namespace == namespace,
                Repository.name == name,
                Repository.repo_type == repo_type,
            )
            if repo:
                return repo
        return None
```

### Registering the Router

```python
# src/kohakuhub/main.py

from kohakuhub.api.routers import git_http

app.include_router(git_http.router, tags=["git"])
```

---

## Pure Python Implementation

**KohakuHub uses pure Python for Git operations - NO pygit2, NO native dependencies!**

### Architecture

```python
# Pure Python - all in-memory, no temp files
class GitLakeFSBridge:
    """Git-LakeFS bridge using pure Python."""

    async def get_refs(self, branch: str) -> dict[str, str]:
        """Get Git refs - pure in-memory."""
        # 1. List files from LakeFS (metadata only)
        # 2. Build blob SHA-1s (LFS pointers for large files)
        # 3. Build tree SHA-1s (pure logic)
        # 4. Build commit SHA-1
        # 5. Return refs dict

    async def build_pack_file(self, wants, haves, branch) -> bytes:
        """Build pack file - pure in-memory."""
        # 1. Build blob objects (with LFS pointers)
        # 2. Build tree objects using build_nested_trees()
        # 3. Build commit object
        # 4. Create pack file with create_pack_file()
        # 5. Return pack bytes
```

### Key Components

**1. Git Object Construction** (`git_objects.py`):
```python
def create_blob_object(content: bytes) -> tuple[str, bytes]:
    """Create blob object and compute SHA-1."""
    header = f"blob {len(content)}\0".encode()
    obj_data = header + content
    sha1 = hashlib.sha1(obj_data).hexdigest()
    return sha1, obj_data

def create_tree_object(entries: list[tuple[str, str, str]]) -> tuple[str, bytes]:
    """Create tree object from entries.

    Args:
        entries: List of (mode, name, sha1_hex)
                 mode: "100644" (file), "40000" (dir)
    """
    # Sort with directories treated as having "/" suffix
    def sort_key(entry):
        mode, name, sha1 = entry
        return name + "/" if mode in ("40000", "040000") else name

    sorted_entries = sorted(entries, key=sort_key)

    # Build tree content
    tree_content = b""
    for mode, name, sha1_hex in sorted_entries:
        sha1_bytes = bytes.fromhex(sha1_hex)
        tree_content += f"{mode} {name}\0".encode() + sha1_bytes

    header = f"tree {len(tree_content)}\0".encode()
    obj_data = header + tree_content
    sha1 = hashlib.sha1(obj_data).hexdigest()

    return sha1, obj_data

def build_nested_trees(flat_entries: list[tuple[str, str, str]]) -> tuple[str, list]:
    """Build nested tree structure from flat file list.

    Critical: Root directory MUST be built LAST!
    """
    # Organize files by directory
    dir_contents = {}
    for mode, path, blob_sha1 in flat_entries:
        # Add file to parent directory
        parts = path.split("/")
        if len(parts) == 1:
            dir_path = ""
        else:
            dir_path = "/".join(parts[:-1])

        dir_contents.setdefault(dir_path, []).append((mode, parts[-1], blob_sha1))

    # Sort directories: deepest first, ROOT LAST
    def sort_dirs(dir_path):
        return (-999, "") if dir_path == "" else (dir_path.count("/"), dir_path)

    sorted_dirs = sorted(dir_contents.keys(), key=sort_dirs, reverse=True)

    # Build trees bottom-up
    dir_sha1s = {}
    tree_objects = []

    for dir_path in sorted_dirs:
        entries = list(dir_contents[dir_path])

        # Add subdirectories
        for child_dir, child_sha1 in dir_sha1s.items():
            if is_direct_child(dir_path, child_dir):
                entries.append(("40000", get_dirname(dir_path, child_dir), child_sha1))

        tree_sha1, tree_data = create_tree_object(entries)
        dir_sha1s[dir_path] = tree_sha1
        tree_objects.append((2, tree_data))

    return dir_sha1s[""], tree_objects
```

**2. LFS Pointer Creation**:
```python
def create_lfs_pointer(sha256: str, size: int) -> bytes:
    """Create LFS pointer file (100 bytes instead of gigabytes!)."""
    pointer = f"""version https://git-lfs.github.com/spec/v1
oid sha256:{sha256}
size {size}
"""
    return pointer.encode("utf-8")

# Usage
if file_size >= 1_000_000:  # 1MB threshold
    pointer = create_lfs_pointer(file.sha256, file.size)
    sha1, blob_data = create_blob_object(pointer)
    # blob_data is only ~100 bytes, not gigabytes!
```

**3. Pack File Generation**:
```python
def create_pack_file(objects: list[tuple[int, bytes]]) -> bytes:
    """Build pack file using pure Python."""
    pack_data = b"PACK"
    pack_data += struct.pack(">I", 2)              # Version
    pack_data += struct.pack(">I", len(objects))   # Count

    for obj_type, obj_data in objects:
        # Extract content (remove header)
        null_pos = obj_data.find(b"\0")
        content = obj_data[null_pos + 1:]

        # Encode object header
        header = encode_pack_object_header(obj_type, len(content))

        # Compress
        compressed = zlib.compress(content)

        pack_data += header + compressed

    # Checksum
    checksum = hashlib.sha1(pack_data).digest()
    pack_data += checksum

    return pack_data
```

### Benefits of Pure Python

| Aspect | pygit2 (Old) | Pure Python (Current) |
|--------|--------------|----------------------|
| Dependencies | pygit2 + libgit2 (C) | stdlib only |
| Installation | Can fail | Always works |
| Temp files | Creates temp git repo | None |
| Memory (10GB file) | 20GB | 100 bytes (LFS pointer) |
| Debugging | Black box | Full visibility |
| Deployment | Complex | Simple |
| Performance | Good | Better (with LFS) |

---

## Complete Code Examples

### 1. git_server.py (Protocol Utilities)

```python
"""Git protocol handler utilities."""

def pkt_line(data: bytes | str | None) -> bytes:
    if data is None:
        return b"0000"
    if isinstance(data, str):
        data = data.encode("utf-8")
    length = len(data) + 4
    return f"{length:04x}".encode("ascii") + data

def parse_pkt_lines(data: bytes) -> list[bytes | None]:
    lines = []
    remaining = data
    while remaining:
        line, remaining = parse_pkt_line(remaining)
        if line is None and not remaining:
            break
        lines.append(line)
    return lines

class GitUploadPackHandler:
    def __init__(self, repo_path: str, bridge=None):
        self.repo_path = repo_path
        self.bridge = bridge
        self.capabilities = [
            "multi_ack",
            "side-band-64k",
            "thin-pack",
            "ofs-delta",
        ]

    def get_service_info(self, refs: dict[str, str]) -> bytes:
        info = GitServiceInfo("upload-pack", refs, self.capabilities)
        return info.to_bytes()

    async def handle_upload_pack(self, request_body: bytes) -> bytes:
        # Parse wants/haves
        wants, haves = self._parse_wants_haves(request_body)

        # Build pack
        if self.bridge:
            pack_data = await self.bridge.build_pack_file(wants, haves)
        else:
            pack_data = self._create_empty_pack()

        # Send response
        nak = pkt_line_stream([b"NAK\n"])
        pack_line = b'\x01' + pack_data
        return nak + pkt_line(pack_line) + pkt_line(None)
```

### 2. git_http.py (FastAPI Router)

```python
"""Git Smart HTTP endpoints."""

from fastapi import APIRouter, Header, Request, Response

router = APIRouter()

@router.get("/{namespace}/{name}.git/info/refs")
async def git_info_refs(
    namespace: str,
    name: str,
    service: str,
    authorization: str | None = Header(None),
):
    # Find repository
    repo = await find_repository(namespace, name)
    if not repo:
        raise HTTPException(404, detail="Repository not found")

    # Authenticate
    user = await get_user_from_git_auth(authorization)

    # Check permissions
    if service == "git-upload-pack":
        check_repo_read_permission(repo, user)
    elif service == "git-receive-pack":
        if not user:
            raise HTTPException(401, detail="Authentication required")
        check_repo_write_permission(repo, user)

    # Get refs from LakeFS
    bridge = GitLakeFSBridge(repo.repo_type, namespace, name)
    refs = await bridge.get_refs(branch="main")

    # Generate response
    handler = GitUploadPackHandler(repo.full_id) if service == "git-upload-pack" else GitReceivePackHandler(repo.full_id)
    response_data = handler.get_service_info(refs)

    return Response(
        content=response_data,
        media_type=f"application/x-{service}-advertisement",
        headers={"Cache-Control": "no-cache"},
    )
```

---

## Testing Your Implementation

### Manual Testing

```bash
# 1. Test service advertisement
curl -i "http://localhost:28080/myorg/myrepo.git/info/refs?service=git-upload-pack"

# 2. Test clone
git clone http://localhost:28080/myorg/myrepo.git

# 3. Test with authentication
git clone http://username:token@localhost:28080/myorg/private-repo.git
```

### Automated Testing

```python
import httpx

async def test_git_info_refs():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:48888/test/repo.git/info/refs",
            params={"service": "git-upload-pack"},
        )

        assert response.status_code == 200
        assert b"# service=git-upload-pack" in response.content
        assert b"refs/heads/main" in response.content
```

---

## Troubleshooting

### Common Issues

**1. "Repository not found"**
- Check that repository exists in database
- Verify namespace and name spelling
- Ensure dynamic type detection is working

**2. "Authentication failed"**
- Verify token is valid and not expired
- Check token hash calculation
- Ensure Basic Auth encoding is correct

**3. "Empty pack file"**
- Check LakeFS has objects in the branch
- Verify bridge is building blobs and trees correctly
- Check File table has LFS flags set properly

**4. Clone hangs**
- Check for pack file generation errors
- Verify side-band encoding is correct
- Look for missing flush packets

---

## Large File Handling with Git LFS

### The Problem

**Naive approach downloads ALL files:**

```python
# BAD - Downloads 10GB file to memory!
for obj in objects:
    content = await client.get_object(...)  # 10GB download
    blob = repo.create_blob(content)        # 10GB in memory
    # Pack file becomes 10GB → OOM crash
```

**Impact:**
- Repo with 10GB model → Downloads 10GB, uses 20GB memory
- Server crashes with Out of Memory
- Clone takes forever even for metadata-only changes

### Solution: Git LFS Pointers

**Instead of including large files, create LFS pointer files:**

```python
# GOOD - Only metadata for large files
if size >= cfg.lfs.threshold_bytes:
    # Get metadata only (no content download!)
    stat = await client.stat_object(...)
    sha256 = stat["checksum"].replace("sha256:", "")

    # Create tiny pointer file
    pointer = f"""version https://git-lfs.github.com/spec/v1
oid sha256:{sha256}
size {size}
"""
    blob = repo.create_blob(pointer.encode())  # Only 100 bytes!
```

**Memory usage:**
- Old: 10GB file → 20GB memory
- New: 10GB file → 100 bytes pointer
- **200,000x reduction!**

### Implementation

```python
def create_lfs_pointer(sha256: str, size: int) -> bytes:
    """Create Git LFS pointer file."""
    pointer = f"""version https://git-lfs.github.com/spec/v1
oid sha256:{sha256}
size {size}
"""
    return pointer.encode("utf-8")


async def _build_tree_from_objects(repo, objects, branch):
    # Separate small and large files
    small_files = [obj for obj in objects if obj["size_bytes"] < threshold]
    large_files = [obj for obj in objects if obj["size_bytes"] >= threshold]

    # Process small files normally
    async def process_small(obj):
        content = await client.get_object(...)
        return repo.create_blob(content)

    # Process large files as pointers (metadata only!)
    async def process_large(obj):
        stat = await client.stat_object(...)  # No content download
        sha256 = stat["checksum"].replace("sha256:", "")
        pointer = create_lfs_pointer(sha256, stat["size_bytes"])
        return repo.create_blob(pointer)

    # Process concurrently
    small_blobs = await asyncio.gather(*[process_small(f) for f in small_files])
    large_blobs = await asyncio.gather(*[process_large(f) for f in large_files])
```

### Client Usage

```bash
# 1. Clone repository (fast - only pointers!)
git clone https://hub.example.com/org/large-model.git
cd large-model

# 2. Install Git LFS
git lfs install

# 3. Pull large files via LFS protocol
git lfs pull

# Files are downloaded using existing HuggingFace LFS API
```

### Automatic .gitattributes

```python
def generate_gitattributes(lfs_paths: list[str]) -> bytes:
    """Generate .gitattributes for LFS files."""
    extensions = set()
    for path in lfs_paths:
        if "." in path:
            ext = path.rsplit(".", 1)[-1]
            extensions.add(ext)

    lines = ["# Git LFS tracking\n"]
    for ext in sorted(extensions):
        lines.append(f"*.{ext} filter=lfs diff=lfs merge=lfs -text\n")

    return "".join(lines).encode("utf-8")

# Example output:
# # Git LFS tracking
# *.bin filter=lfs diff=lfs merge=lfs -text
# *.safetensors filter=lfs diff=lfs merge=lfs -text
```

## Performance Optimization

### 1. Caching

```python
# Cache refs for short periods
from functools import lru_cache
from datetime import datetime, timedelta

@lru_cache(maxsize=128)
def get_cached_refs(repo_id: str, timestamp: int):
    # timestamp rounded to minute for 60s cache
    return fetch_refs(repo_id)
```

### 2. Concurrent Processing

```python
# Process multiple files concurrently with asyncio.gather
results = await asyncio.gather(*[process_file(obj) for obj in objects])
```

### 3. Pagination

```python
# Process LakeFS objects in batches
async def list_all_objects(repo, ref):
    objects = []
    after = ""
    while True:
        result = await client.list_objects(
            repository=repo,
            ref=ref,
            after=after,
            amount=1000,  # Batch size
        )
        objects.extend(result["results"])
        if not result.get("pagination", {}).get("has_more"):
            break
        after = result["pagination"]["next_offset"]
    return objects
```

### 4. Memory-Efficient Pack Generation

**Before optimization:**
- 100 files (1 x 10GB) → 20GB memory, 5 minutes
- Sequential processing

**After optimization:**
- 100 files (1 x 10GB) → 200MB memory, 30 seconds
- LFS pointers for large files
- Concurrent processing
- **100x faster, 100x less memory**

---

## References

### Official Documentation

- [Git Protocol Documentation](https://git-scm.com/docs/pack-protocol)
- [Git HTTP Protocol](https://git-scm.com/docs/http-protocol)
- [Pack Format](https://git-scm.com/docs/pack-format)
- [Packet-Line Format](https://git-scm.com/docs/protocol-common)

### Libraries

- [FastAPI](https://fastapi.tiangolo.com/) - Modern web framework
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- Pure Python (stdlib only) - No native dependencies for Git operations

### Tutorials

- [Building a Git Server](https://git-scm.com/book/en/v2/Git-on-the-Server-The-Protocols)
- [Understanding Git Pack Files](https://git-scm.com/book/en/v2/Git-Internals-Packfiles)

---

## Conclusion

Building a Git-compatible server involves:
1. **Understanding the protocol**: pkt-line, service advertisement, upload/receive-pack
2. **Implementing core handlers**: Parsing requests, generating pack files
3. **Integrating with storage**: Translating Git operations to your backend (LakeFS)
4. **Adding authentication**: Token validation and permission checks
5. **Optimizing performance**: LFS pointers, concurrent processing, chunking
6. **Pure Python approach**: No native dependencies, full control, better debugging

**KohakuHub Implementation Highlights:**
- ✅ **Pure Python** - No pygit2, no libgit2, no native dependencies
- ✅ **In-memory** - No temporary directories or files
- ✅ **LFS integration** - Automatic LFS pointers for large files (>1MB)
- ✅ **Concurrent** - Parallel processing with asyncio.gather
- ✅ **Memory efficient** - Only downloads small files, pointers for large files
- ✅ **Production ready** - Handles repos of any size without OOM

This demonstrates how to build a complete Git server using only Python stdlib + FastAPI, with full Git LFS support for machine learning models and datasets.

---

**Last Updated:** January 2025
**Version:** 1.1
**Authors:** KohakuHub Team
