# KohakuHub Backend System

## Executive Overview

KohakuHub is a self-hosted, HuggingFace-compatible model hub that provides Git-like version control for AI models, datasets, and spaces. The system combines the familiarity of HuggingFace's API with the power of LakeFS for data versioning and S3 for scalable object storage, enabling organizations to maintain full control over their ML artifacts while retaining compatibility with the entire HuggingFace ecosystem.

**Key Value Propositions:**
- **Drop-in Replacement**: 100% compatible with `huggingface_hub` Python client - no code changes needed
- **Native Git Support**: Standard Git operations (clone, pull, push) with pure Python implementation
- **Enterprise-Ready Version Control**: Git-like branching, commits, and tags for all ML artifacts via LakeFS
- **Scalable Storage**: S3-compatible object storage with content-addressed deduplication
- **Multi-Tenancy**: Organization support with role-based access control and quota management
- **Zero Native Dependencies**: Pure Python implementation requiring no system-level Git installation

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Client Applications                           │
│  HuggingFace Hub Client | Transformers | Diffusers | Git CLI | Web UI   │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   │ HTTP/HTTPS
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│                        KohakuHub FastAPI Backend                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  API Layer (kohakuhub.api)                                      │   │
│  │  ┌──────────┬──────────┬──────────┬──────────┬─────────────┐    │   │
│  │  │   Repo   │  Commit  │   Git    │   Org    │   Quota &   │    │   │
│  │  │   CRUD   │   Mgmt   │ Protocol │   Mgmt   │   Settings  │    │   │
│  │  └──────────┴──────────┴──────────┴──────────┴─────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Auth Layer (kohakuhub.auth)                                    │   │
│  │  Session-based (Web) + Token-based (API) + RBAC                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Utils Layer (kohakuhub.utils)                                  │   │
│  │  LakeFS Client + S3 Operations (Presigned URLs, Multipart)      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└──────────────────┬──────────────────┬──────────────────────────────────┘
                   │                  │
        ┌──────────┼──────────┬───────┼────────┐
        │          │          │       │        │
        ▼          ▼          ▼       ▼        ▼
┌──────────┐ ┌──────────┐ ┌─────────────┐ ┌────────┐
│PostgreSQL│ │  LakeFS  │ │ S3 Storage  │ │  SMTP  │
│  (Meta)  │ │(Versions)│ │  (Objects)  │ │(Email) │
└──────────┘ └──────────┘ └─────────────┘ └────────┘
```

### Technology Stack

**Application Framework:**
- **FastAPI**: High-performance async REST API framework
- **Python 3.11+**: Modern async/await patterns throughout
- **Uvicorn**: ASGI server with multi-worker support

**Storage Infrastructure:**
- **LakeFS**: Git-like version control for object storage (REST API integration)
- **S3-Compatible Storage**: MinIO, AWS S3, Cloudflare R2, etc.
- **PostgreSQL/SQLite**: Metadata, authentication, and quota tracking

**Authentication & Security:**
- **Bcrypt**: Password hashing with adaptive rounds
- **SHA3-512**: API token hashing
- **JWT-like Sessions**: HTTP-only cookie sessions for web UI
- **RBAC**: Role-based access control for organizations

**Integration Libraries:**
- **boto3**: S3 operations with presigned URL generation
- **lakefs-sdk**: LakeFS REST API client
- **Peewee ORM**: Database abstraction with atomic transactions

## Module Architecture

KohakuHub is organized into three primary modules that work together to provide a complete ML artifact management system:

### 1. API Module (`kohakuhub.api`)

**Purpose**: Core REST API layer implementing HuggingFace Hub compatibility and Git protocol support

**Key Responsibilities:**
- Repository lifecycle management (create, delete, move, squash)
- Git-like commit operations with atomic multi-file operations
- Native Git protocol support (clone, fetch, pull via git-upload-pack)
- Git LFS Batch API for large file transfers
- Organization management with RBAC
- Storage quota tracking and enforcement
- Branch and tag management
- File browsing and tree navigation

**Key Components:**
- **Repository Management** (`api/repo/`): CRUD operations, file tree browsing, garbage collection
- **Commit Operations** (`api/commit/`): Atomic commits, history, diffs, LFS object tracking
- **Git Protocol** (`api/git/`): Git Smart HTTP, LFS Batch API, pure Python Git server
- **Organization Management** (`api/org/`): Multi-user namespaces, member management, role hierarchy
- **Quota System** (`api/quota/`): Dual quotas (private/public), real-time enforcement

**Integration Points:**
- Communicates with LakeFS for version control operations
- Generates presigned S3 URLs for direct client-to-storage transfers
- Enforces quotas before accepting uploads
- Updates storage counters after successful commits
- Triggers garbage collection for deleted files

### 2. Authentication Module (`kohakuhub.auth`)

**Purpose**: Comprehensive authentication and authorization system

**Key Responsibilities:**
- Dual authentication methods: session-based (web UI) and token-based (API)
- User registration with optional email verification
- Password hashing and secure credential storage
- API token management with SHA3-512 hashing
- Role-based permission checking for repositories and organizations
- Namespace ownership validation

**Authentication Flow:**
- **Web UI**: Session cookies with HTTP-only flag for XSS protection
- **API**: Bearer tokens in Authorization header
- **Git**: HTTP Basic Auth with username/token

**Permission Model:**
- **Repository Access**: Public (all), Private (owner/org members only)
- **Repository Operations**: Read, Write, Delete with org role checks
- **Organization Roles**: visitor, member, admin, super-admin
- **Namespace Validation**: User ownership or organization membership

**Security Features:**
- Bcrypt password hashing with per-password salts
- Token hashing prevents token recovery from database breaches
- Constant-time comparison for timing attack resistance
- Timezone-aware expiry timestamps
- CSPRNG-based token generation

### 3. Utils Module (`kohakuhub.utils`)

**Purpose**: Low-level storage infrastructure and LakeFS integration

**Key Responsibilities:**
- LakeFS REST client configuration and repository naming
- S3 operations with async execution patterns
- Presigned URL generation for uploads and downloads
- Multipart upload support for files >5GB
- Batch operations for copying and deleting objects
- Object metadata retrieval and existence checks

**Key Features:**
- **Async Wrappers**: All S3 operations run in thread pool to avoid blocking
- **Presigned URLs**: Time-limited URLs for direct client-to-S3 transfers
- **Multipart Uploads**: Support for large files with part-by-part uploads
- **Batch Operations**: Efficient prefix-based bulk operations
- **LakeFS Integration**: Repository naming conventions and client access

**Integration Points:**
- Used by all API modules for storage operations
- Provides S3 client configuration and initialization
- Abstracts storage complexity from business logic
- Enables direct client-to-S3 transfers without proxying

## Data Flow Through The System

### Upload Flow (Large Files via Git LFS)

```
1. Client requests upload via LFS Batch API
   └─> POST /{namespace}/{repo}.git/info/lfs/objects/batch

2. Auth module validates user credentials
   └─> Check repository write permission

3. Quota module validates available storage
   └─> Check namespace quota (private/public)

4. S3 utils generate presigned upload URL
   └─> Time-limited PUT URL with SHA256 checksum

5. Client uploads directly to S3
   └─> Bypass application server (efficient)

6. Client notifies completion via commit API
   └─> POST /api/{repo_type}s/{namespace}/{name}/commit/{revision}

7. Commit module creates LakeFS commit
   └─> Link LFS objects to files
   └─> Update file metadata in database
   └─> Update quota counters

8. Response returned to client
   └─> Commit SHA and metadata
```

### Download Flow

```
1. Client requests file download
   └─> GET /{repo_type}s/{namespace}/{name}/resolve/{revision}/{path}

2. Auth module validates read permission
   └─> Public repos: allow all
   └─> Private repos: check ownership/membership

3. Repo module retrieves file metadata from LakeFS
   └─> Get physical address (S3 URI)

4. S3 utils generate presigned download URL
   └─> Time-limited GET URL (default 1 hour)

5. API returns 302 redirect
   └─> Client downloads directly from S3

6. No data proxying through application server
   └─> Efficient for large model files
```

### Git Clone Flow (Pure Python Implementation)

```
1. Git client sends info/refs request
   └─> GET /{namespace}/{name}.git/info/refs?service=git-upload-pack

2. Git protocol handler advertises capabilities
   └─> Pure Python pkt-line protocol
   └─> List all refs (branches, tags)

3. Client sends want/have negotiation
   └─> POST /{namespace}/{name}.git/git-upload-pack

4. LakeFS bridge builds Git pack dynamically
   └─> Construct commit objects from LakeFS commits
   └─> Construct tree objects from LakeFS file trees
   └─> Generate blob objects or LFS pointers
   └─> Files <1MB: include in pack
   └─> Files >=1MB: create LFS pointer

5. Pack streamed to client via side-band protocol
   └─> Memory-efficient streaming
   └─> No temporary files needed

6. Client receives repository with .gitattributes and .lfsconfig
   └─> Automatic LFS configuration
```

## Key System Features

### HuggingFace Hub Compatibility

**Full API Compatibility:**
- All standard `huggingface_hub` client methods work without modification
- Compatible endpoint paths: `/{repo_type}s/{namespace}/{name}`
- Matching response formats and error codes
- Header compatibility: `X-Repo-Commit`, `X-Linked-Etag`
- HF-compatible commit hashes are normalized to 40 hexadecimal characters on public HF response surfaces

**Supported Operations:**
- `create_repo()`, `delete_repo()`, `update_repo_visibility()`
- `upload_file()`, `upload_folder()`, `upload_large_folder()`
- `hf_hub_download()`, `snapshot_download()`
- `list_repo_files()`, `list_repo_tree()`, `get_paths_info()`
- `list_repo_commits()`, `list_repo_refs()`

**Client Library Integration:**
- Works with `transformers` library for model loading
- Works with `diffusers` library for diffusion models
- Works with `datasets` library for dataset management
- Works with `huggingface_hub` CLI tool

### Git-Like Version Control

**LakeFS Integration:**
- Every repository is a LakeFS repository
- Branches and commits tracked via LakeFS
- Diff calculation between any two revisions
- Tag support for pinning versions
- Merge operations with conflict detection
- Revert operations with validation

**Version Control Operations:**
- Create branches from any revision
- Atomic commits with multiple operations (upload, delete, copy)
- Commit history with pagination and diff viewing
- Branch merging with conflict detection
- Tag creation for release management
- Repository squashing to optimize storage

### Deduplication & Storage Optimization

**Content-Addressed Storage:**
- All files identified by SHA256 hash
- LFS objects stored once, referenced many times
- Deduplication across repositories
- Balanced directory structure: `lfs/{oid[:2]}/{oid[2:4]}/{oid}`

**Storage Tracking:**
- Real-time quota tracking per namespace
- Dual quota system: private vs. public repositories
- Incremental counter updates for efficiency
- Periodic recalculation for drift correction

**Garbage Collection:**
- Automatic cleanup of unreferenced LFS objects
- Safe deletion with reference counting
- Triggered on file deletion and repository deletion
- S3 prefix-based batch deletion

### Multi-Tenancy & Organizations

**Organization Features:**
- Organization creation with default quotas
- Member lifecycle management
- Role hierarchy: visitor, member, admin, super-admin
- Organization repositories with shared namespace

**Permission Model:**
- **Read**: Public repos (all), private repos (members/visitors)
- **Write**: Organization members and admins
- **Delete**: Organization admins and super-admins only

**Quota Management:**
- Separate quotas for users and organizations
- Separate quotas for private vs. public repositories
- Configurable default quotas
- Admin-adjustable quotas per namespace

### Pure Python Git Server

**No Native Dependencies:**
- No pygit2 or libgit2 required
- No system Git installation needed
- Pure Python Git object construction
- Pure Python pkt-line protocol

**Memory Efficient:**
- Streaming pack generation
- No temporary files
- Handles repositories of any size
- Chunked transfer encoding

**Git LFS Integration:**
- Automatic LFS pointer generation for large files
- Configurable size threshold (default 1MB)
- LFS Batch API for uploads/downloads
- Automatic `.gitattributes` and `.lfsconfig` generation

## Deployment Architecture

### Production Stack

```
┌──────────────────────────────────────────────────────────────┐
│                    Load Balancer / CDN                       │
│                       (HTTPS: 443)                           │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                     Nginx Reverse Proxy                      │
│           Web UI (/) + API (/api) + Git (/*.git)             │
│                         Port 28080                           │
└──────────────────────┬───────────────────────────────────────┘
                       │
        ┌──────────────┼───────────────┐
        │              │               │
┌───────▼──────┐   ┌────▼────┐   ┌─────▼─────┐
│   Frontend   │   │ Backend │   │  LakeFS   │
│  (Vue SPA)   │   │ FastAPI │   │  (8000)   │
│ Static Files │   │ Workers │   │           │
│              │   │  x4-8   │   │           │
└──────────────┘   └────┬────┘   └─────┬─────┘
                        │              │
        ┌───────────────┼────────┬─────┘
        │               │        │
   ┌────▼────┐   ┌──────▼─────┐  │
   │   S3    │   │ PostgreSQL │  │
   │ Storage │   │ (Metadata) │  │
   │ (MinIO) │   │            │  │
   └─────────┘   └────────────┘  │
                                 │
   ┌─────────────────────────────┘
   │ LakeFS uses S3 for actual data
   └──> All version control metadata
```

### Configuration Requirements

Configuration is managed via `config.toml` and can be overridden by environment variables.

**Primary Configuration:**
- `HUB_CONFIG`: Path to a custom `config.toml` file. If not set, it defaults to `config.toml` in the working directory.

**Environment Variables (override `config.toml`):**

```bash
# -------------------------------------
# --- Application Settings
# -------------------------------------
# Base URL of the application (e.g., https://hub.example.com)
KOHAKU_HUB_BASE_URL=http://localhost:48888
# API base path
KOHAKU_HUB_API_BASE=/api
# Site name displayed in the UI
KOHAKU_HUB_SITE_NAME=KohakuHub
# Log request/response payloads for debugging (can expose sensitive data)
KOHAKU_HUB_DEBUG_LOG_PAYLOADS=false

# -------------------------------------
# --- S3 Storage Settings
# -------------------------------------
# Public-facing S3 endpoint (for client downloads, can be a CDN)
KOHAKU_HUB_S3_PUBLIC_ENDPOINT=http://localhost:9000
# Internal S3 endpoint for the application
KOHAKU_HUB_S3_ENDPOINT=http://localhost:9000
KOHAKU_HUB_S3_ACCESS_KEY=test-access-key
KOHAKU_HUB_S3_SECRET_KEY=test-secret-key
KOHAKU_HUB_S3_BUCKET=test-bucket
# S3 region (e.g., us-east-1)
KOHAKU_HUB_S3_REGION=us-east-1
# S3 signature version (e.g., s3v4 for AWS S3/R2)
KOHAKU_HUB_S3_SIGNATURE_VERSION=

# -------------------------------------
# --- LakeFS Settings
# -------------------------------------
KOHAKU_HUB_LAKEFS_ENDPOINT=http://localhost:8000
KOHAKU_HUB_LAKEFS_ACCESS_KEY=test-access-key
KOHAKU_HUB_LAKEFS_SECRET_KEY=test-secret-key
# Default namespace for repositories in LakeFS
KOHAKU_HUB_LAKEFS_REPO_NAMESPACE=hf

# -------------------------------------
# --- Database Settings
# -------------------------------------
# "sqlite" or "postgres"
KOHAKU_HUB_DB_BACKEND=sqlite
# Connection URL (e.g., "sqlite:///./hub.db" or "postgresql://user:pass@host:port/dbname")
KOHAKU_HUB_DATABASE_URL=sqlite:///./hub.db

# -------------------------------------
# --- Git LFS Settings
# -------------------------------------
# File size threshold to trigger LFS (bytes)
KOHAKU_HUB_LFS_THRESHOLD_BYTES=5242880 # 5MB
# Threshold for using multipart uploads for LFS (bytes)
KOHAKU_HUB_LFS_MULTIPART_THRESHOLD_BYTES=104857600 # 100MB
# Chunk size for LFS multipart uploads (bytes)
KOHAKU_HUB_LFS_MULTIPART_CHUNK_SIZE_BYTES=52428800 # 50MB
# Number of LFS file versions to keep during garbage collection
KOHAKU_HUB_LFS_KEEP_VERSIONS=5
# Automatically run garbage collection on commits
KOHAKU_HUB_LFS_AUTO_GC=false

# -------------------------------------
# --- Authentication & Session Settings
# -------------------------------------
# Require email verification for new users
KOHAKU_HUB_REQUIRE_EMAIL_VERIFICATION=false
# Disable public registration, require invitation
KOHAKU_HUB_INVITATION_ONLY=false
# Secret key for session management (CHANGE IN PRODUCTION)
KOHAKU_HUB_SESSION_SECRET=change-me-in-production
# Session cookie expiration (hours)
KOHAKU_HUB_SESSION_EXPIRE_HOURS=168 # 7 days
# API token expiration (days)
KOHAKU_HUB_TOKEN_EXPIRE_DAYS=365

# -------------------------------------
# --- Admin API Settings
# -------------------------------------
KOHAKU_HUB_ADMIN_ENABLED=true
# Secret token for accessing the admin API (CHANGE IN PRODUCTION)
KOHAKU_HUB_ADMIN_SECRET_TOKEN=change-me-in-production

# -------------------------------------
# --- Storage Quota Settings
# -------------------------------------
# Default private repo quota for users (bytes, blank for unlimited)
KOHAKU_HUB_DEFAULT_USER_PRIVATE_QUOTA_BYTES=
# Default public repo quota for users (bytes, blank for unlimited)
KOHAKU_HUB_DEFAULT_USER_PUBLIC_QUOTA_BYTES=
# Default private repo quota for organizations (bytes, blank for unlimited)
KOHAKU_HUB_DEFAULT_ORG_PRIVATE_QUOTA_BYTES=
# Default public repo quota for organizations (bytes, blank for unlimited)
KOHAKU_HUB_DEFAULT_ORG_PUBLIC_QUOTA_BYTES=

# -------------------------------------
# --- Fallback Source Settings
# -------------------------------------
# Enable fallback to external sources (e.g., HuggingFace)
KOHAKU_HUB_FALLBACK_ENABLED=true
# Cache TTL for fallback repo mappings (seconds)
KOHAKU_HUB_FALLBACK_CACHE_TTL=300
# HTTP request timeout for external sources (seconds)
KOHAKU_HUB_FALLBACK_TIMEOUT=10
# Max concurrent requests to external sources
KOHAKU_HUB_FALLBACK_MAX_CONCURRENT=5
# JSON list of global fallback sources
KOHAKU_HUB_FALLBACK_SOURCES='[]'

# -------------------------------------
# --- SMTP (Email) Settings
# -------------------------------------
KOHAKU_HUB_SMTP_ENABLED=false
KOHAKU_HUB_SMTP_HOST=localhost
KOHAKU_HUB_SMTP_PORT=587
KOHAKU_HUB_SMTP_USERNAME=
KOHAKU_HUB_SMTP_PASSWORD=
KOHAKU_HUB_SMTP_FROM=noreply@localhost
KOHAKU_HUB_SMTP_TLS=true
```

**Scaling Considerations:**
- FastAPI workers: 4-8 workers per instance (CPU cores)
- Database: Use connection pooling for multi-worker setups
- LakeFS: Single instance sufficient for most workloads
- S3: Horizontally scalable (use managed service)
- Redis (future): For caching and session storage

### Security Checklist

**Production Requirements:**
- [ ] Change all default passwords and secrets
- [ ] Use HTTPS with valid TLS certificates
- [ ] Set secure `KOHAKU_HUB_SESSION_SECRET` (32+ bytes)
- [ ] Set secure `LAKEFS_AUTH_ENCRYPT_SECRET_KEY`
- [ ] Only expose port 28080 (nginx), not individual services
- [ ] Enable email verification if allowing public registration
- [ ] Configure SMTP for email notifications
- [ ] Set up firewall rules for service isolation
- [ ] Use managed PostgreSQL with automated backups
- [ ] Configure S3 bucket policies for access control
- [ ] Implement rate limiting at nginx level
- [ ] Set up monitoring and alerting
- [ ] Regular security updates for all dependencies

## Getting Started for Developers

### Local Development Setup

**Prerequisites:**
- Python 3.11+
- Node.js 18+ (for frontend)
- Docker and Docker Compose (for infrastructure)

**Backend Development:**
```bash
# Install dependencies
pip install -e .

# Start infrastructure (LakeFS, MinIO, PostgreSQL)
docker-compose up -d postgres minio lakefs

# Run development server with auto-reload
uvicorn kohakuhub.main:app --reload --port 48888

# Access API documentation
open http://localhost:48888/docs
```

**Frontend Development:**
```bash
cd src/kohaku-hub-ui
npm install
npm run dev
# Opens on http://localhost:5173
```

**Testing:**
```bash
# Run test scripts
python scripts/test.py
python scripts/test_auth.py

# Test HuggingFace Hub compatibility
python scripts/test_hf_client.py
```

### Project Structure

```
KohakuHub/
├── src/kohakuhub/          # Backend Python package
│   ├── api/                # API layer (main module)
│   │   ├── repo/           # Repository operations
│   │   ├── commit/         # Commit operations
│   │   ├── git/            # Git protocol support
│   │   ├── org/            # Organization management
│   │   ├── quota/          # Quota tracking
│   │   ├── files.py        # File operations
│   │   ├── branches.py     # Branch/tag operations
│   │   └── settings.py     # Settings management
│   ├── auth/               # Authentication module
│   │   ├── dependencies.py # FastAPI auth dependencies
│   │   ├── permissions.py  # Permission checking
│   │   ├── routes.py       # Auth endpoints
│   │   └── utils.py        # Crypto utilities
│   ├── utils/              # Infrastructure utilities
│   │   ├── lakefs.py       # LakeFS client
│   │   └── s3.py           # S3 operations
│   ├── db.py               # Database models (Peewee ORM)
│   ├── config.py           # Configuration management
│   ├── logger.py           # Structured logging
│   └── main.py             # FastAPI application
├── src/kohaku-hub-ui/      # Frontend Vue 3 application
├── docker/                 # Docker configurations
├── scripts/                # Test and utility scripts
└── docs/                   # Documentation
```

### Adding New Features

**API Endpoint Pattern:**
```python
from fastapi import APIRouter, Depends, HTTPException
from kohakuhub.auth import get_current_user
from kohakuhub.auth.permissions import check_repo_write_permission
from kohakuhub.db import User, Repository

router = APIRouter()

@router.post("/api/{repo_type}s/{namespace}/{name}/operation")
async def new_operation(
    repo_type: str,
    namespace: str,
    name: str,
    user: User = Depends(get_current_user)
):
    # 1. Retrieve repository
    repo = Repository.get_or_none(
        namespace=namespace,
        name=name,
        repo_type=repo_type
    )
    if not repo:
        raise HTTPException(404, detail={"error": "Repository not found"})

    # 2. Check permissions
    check_repo_write_permission(repo, user)

    # 3. Perform operation
    # ...

    # 4. Return response
    return {"status": "success"}
```

**Database Model Pattern:**
```python
from peewee import *
from kohakuhub.db import BaseModel

class NewModel(BaseModel):
    """New database model with automatic timestamps."""
    name = CharField(unique=True)
    data = TextField()
    user = ForeignKeyField(User, backref='items')

    class Meta:
        table_name = 'new_model'
```

### Common Development Tasks

**Running Multi-Worker Tests:**
```bash
# Test with multiple workers (production-like)
uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 4
```

**Database Migrations:**
```bash
# Database uses Peewee ORM
# Modify models in db.py
# Restart application to create new tables
# For complex migrations, use peewee-migrations
```

**Adding Auth to Endpoints:**
```python
# Required authentication
from kohakuhub.auth import get_current_user
user: User = Depends(get_current_user)

# Optional authentication
from kohakuhub.auth import get_optional_user
user: User | None = Depends(get_optional_user)
```

**Logging Best Practices:**
```python
from kohakuhub.logger import get_logger
logger = get_logger("MODULE_NAME")

logger.info("Operation successful", extra={"user": user.username})
logger.warning("Potential issue detected")
logger.error("Operation failed", exc_info=True)
```

## API Endpoints Summary

### Authentication (`/api/auth`)
- `POST /register` - User registration
- `POST /login` - User login
- `POST /logout` - User logout
- `GET /me` - Current user info
- `GET /tokens` - List API tokens
- `POST /tokens/create` - Create API token
- `DELETE /tokens/{token_id}` - Delete API token

### Repositories (`/api/{repo_type}s`)
- `POST /repos/create` - Create repository
- `DELETE /repos/delete` - Delete repository
- `POST /repos/move` - Move repository
- `POST /repos/squash` - Squash commits
- `GET /{namespace}/{name}` - Get repo info
- `GET /` - List repositories
- `GET /{namespace}/{name}/tree/{revision}/{path}` - Browse tree
- `PUT /{namespace}/{name}/settings` - Update settings

### Commits (`/api/{repo_type}s`)
- `POST /{namespace}/{name}/commit/{revision}` - Create commit
- `GET /{namespace}/{name}/commits/{branch}` - List commits
- `GET /{namespace}/{name}/commit/{commit_id}` - Get commit
- `GET /{namespace}/{name}/commit/{commit_id}/diff` - Get diff

### Branches & Tags (`/api/{repo_type}s`)
- `POST /{namespace}/{name}/branch` - Create branch
- `DELETE /{namespace}/{name}/branch/{branch}` - Delete branch
- `POST /{namespace}/{name}/tag` - Create tag
- `DELETE /{namespace}/{name}/tag/{tag}` - Delete tag
- `POST /{namespace}/{name}/merge/{src}/into/{dest}` - Merge branches
- `POST /{namespace}/{name}/branch/{branch}/reset` - Reset branch

### Files (`/api/{repo_type}s`)
- `GET /{namespace}/{name}/resolve/{revision}/{path}` - Download file
- `HEAD /{namespace}/{name}/resolve/{revision}/{path}` - File metadata
- `POST /{namespace}/{name}/preupload/{revision}` - Preupload check

### Git Protocol
- `GET /{namespace}/{name}.git/info/refs` - Git service advertisement
- `POST /{namespace}/{name}.git/git-upload-pack` - Git fetch/clone
- `POST /{namespace}/{name}.git/info/lfs/objects/batch` - LFS batch API

### Organizations (`/org`)
- `POST /create` - Create organization
- `GET /{org_name}` - Get organization
- `POST /{org_name}/members` - Add member
- `DELETE /{org_name}/members/{username}` - Remove member
- `PUT /{org_name}/members/{username}` - Update member role

### Quota (`/api/quota`)
- `GET /{namespace}` - Get quota info
- `PUT /{namespace}` - Set quotas
- `POST /{namespace}/recalculate` - Recalculate storage

## Performance Characteristics

**Strengths:**
- **Direct S3 Transfers**: Presigned URLs eliminate application bottleneck
- **Async Operations**: Non-blocking I/O for all storage operations
- **Content Deduplication**: Same file stored once, referenced many times
- **Streaming Git Packs**: Memory-efficient for large repositories
- **Parallel Processing**: Concurrent file processing in commits

**Optimization Strategies:**
- Database indexes on frequently queried columns
- LFS object deduplication across repositories
- Incremental quota updates (avoid full recalculation)
- Paginated responses for large datasets
- Presigned URL caching (15-minute default)

**Current Limitations:**
- Synchronous database operations (Peewee ORM)
- No Redis caching layer (planned)
- No background job queue (planned)
- No CDN integration (planned)

## Contributing

**Development Workflow:**
1. Create feature branch from `main`
2. Implement changes with tests
3. Run test suite: `python scripts/test.py`
4. Submit pull request with description

**Code Style:**
- Follow PEP 8 for Python code
- Use type hints throughout
- Add docstrings for public functions
- Keep functions focused and testable

**Testing Requirements:**
- Authentication and authorization tests
- HuggingFace client compatibility tests
- Git protocol compatibility tests
- Database transaction integrity tests

## Documentation References

- [Project README](../../README.md) - Overview and quick start
- [Setup Guide](../../docs/setup.md) - Installation instructions
- [API Documentation](../../docs/API.md) - Detailed API reference
- [Git Documentation](../../docs/Git.md) - Git protocol implementation details
- [CLI Documentation](../../docs/CLI.md) - Command-line tool usage
- [Contributing Guide](../../CONTRIBUTING.md) - Contribution guidelines

## License

AGPL-3.0 - See [LICENSE](../../LICENSE) for details

---

**Status**: Work in Progress - Not Production Ready

For questions and support, join our Discord: https://discord.gg/xWYrkyvJ2s
