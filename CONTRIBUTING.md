# Contributing to KohakuHub

Thank you for your interest in contributing to KohakuHub! We welcome contributions from the community.

## Quick Links

- **Discord:** https://discord.gg/xWYrkyvJ2s (Best for discussions)
- **GitHub Issues:** Bug reports and feature requests
- **Roadmap:** See [Project Status](#project-status) below

## Code Conventions and Rules

### Python Code Style

**Core Principles:**
1. **Minimal solution, but you can't skip anything.** If any implementation/target/goal are too difficult, discuss first. Don't silently ignore them.
2. **Modern Python:** Use match-case instead of nested if-else, utilize native type hints (use `list[]`, `dict[]` instead of importing from `typing` unless needed)
3. **Clean code:** Try to split large functions into smaller ones
4. **Type hints recommended but not required** - No static type checking, but use type hints for documentation

**Import Order Rules:**
```python
# 1. builtin packages
import asyncio
import hashlib
from datetime import datetime

# 2. Third-party packages (alphabetical)
import bcrypt
from fastapi import APIRouter, Depends
from peewee import fn

# 3. Our packages (shorter paths first, then alphabetical)
from kohakuhub.config import cfg
from kohakuhub.db import User
from kohakuhub.db_operations import get_repository
from kohakuhub.api.quota.util import get_storage_info
from kohakuhub.auth.dependencies import get_current_user

# Within each group:
# - `import xxx` comes before `from xxx import`
# - Shorter paths before longer paths
# - Alphabetical order
```

**Type Hints - Use Native Types:**
```python
# ✅ Good - native types (Python 3.10+)
def process_data(items: list[str]) -> dict[str, int]:
    results: dict[str, int] = {}
    return results

# ❌ Avoid - importing from typing
from typing import List, Dict
def process_data(items: List[str]) -> Dict[str, int]:
    pass
```

**Modern Python Patterns:**
```python
# ✅ Good - use match-case
match status:
    case "active":
        handle_active()
    case "pending":
        handle_pending()
    case _:
        handle_default()

# ❌ Avoid - nested if-else
if status == "active":
    handle_active()
elif status == "pending":
    handle_pending()
else:
    handle_default()

# ✅ Good - native union syntax
def get_user(username: str) -> User | None:
    return User.get_or_none(User.username == username)

# ❌ Avoid - Optional from typing
from typing import Optional
def get_user(username: str) -> Optional[User]:
    pass
```

**No imports in functions** (except to avoid circular imports):
```python
# ✅ Good - imports at top
from kohakuhub.db import User

def process_user(user_id: int):
    user = User.get_by_id(user_id)
    return user

# ❌ Avoid - imports in function
def process_user(user_id: int):
    from kohakuhub.db import User
    user = User.get_by_id(user_id)
    return user
```

**Code formatting:**
- Use `black` for code formatting
- Line length: 100 characters (black default is 88, we use 100)
- Use `asyncio.gather()` for parallel async operations (NOT sequential await in loops)

### File Structure Rules

**Global Infrastructure** (used by multiple features):
```
kohakuhub/
├── utils/                  # Global infrastructure
│   ├── s3.py              # S3 client wrapper
│   ├── lakefs.py          # LakeFS client wrapper
│   └── names.py           # Name normalization
├── auth/                   # Cross-cutting concern (stays at root)
│   ├── routes.py          # Auth endpoints
│   ├── dependencies.py    # Used by ALL routers
│   └── permissions.py     # Used by ALL routers
├── config.py              # Configuration
├── db.py                  # Database models (Peewee ORM - synchronous)
├── db_operations.py       # Database operation wrappers
├── logger.py              # Logging utilities
└── lakefs_rest_client.py  # LakeFS REST client
```

**API Endpoints** (FastAPI routers):

**Rule 1:** Simple, standalone endpoint → Single file in `api/`
```
api/
├── admin.py               # Admin portal endpoints
├── avatar.py              # Avatar management
├── branches.py            # Branch operations
├── files.py               # File operations (large but no specific utils)
├── likes.py               # Repository likes
├── misc.py                # Misc utilities
├── settings.py            # Settings endpoints
├── stats.py               # Statistics and trending
└── validation.py          # Name validation
```

**Rule 2:** Feature with utils → `api/<feature>/`
```
api/org/
├── router.py              # Organization endpoints
└── util.py                # Organization utilities

api/quota/
├── router.py              # Quota endpoints
└── util.py                # Quota calculations

api/invitation/
├── router.py              # Invitation endpoints
└── util.py                # Invitation utilities (if needed)
```

**Rule 3:** Complex feature (multiple routers) → `api/<feature>/routers/`
```
api/repo/
├── routers/
│   ├── crud.py            # Create/delete/move repositories
│   ├── info.py            # Repository info/listing
│   └── tree.py            # File tree operations
└── utils/
    ├── hf.py              # HuggingFace compatibility (used by multiple routers)
    └── gc.py              # Garbage collection

api/commit/
└── routers/
    ├── operations.py      # Commit operations
    └── history.py         # Commit history/diff

api/git/
├── routers/
│   ├── http.py            # Git Smart HTTP
│   ├── lfs.py             # Git LFS protocol
│   └── ssh_keys.py        # SSH key management
└── utils/
    ├── objects.py         # Pure Python Git objects
    ├── server.py          # Git protocol (pkt-line)
    └── lakefs_bridge.py   # Git-LakeFS translation
```

**Decision Tree:**
1. **No utils needed?** → Use Rule 1 (single file `api/xxx.py`)
2. **Needs utils?** → Use Rule 2 (folder `api/xxx/` with `router.py` + `util.py`)
3. **Multiple routers?** → Use Rule 3 (folder `api/xxx/routers/` + optional `utils/`)
4. **Utils used by EVERYONE?** → Put in root `utils/` (s3, lakefs, names)
5. **Utils used by multiple routers in same feature?** → Put in `api/xxx/utils/`

**Router Import Pattern in `main.py`:**
```python
# Rule 1 (single file exports router)
from kohakuhub.api import admin, avatar, branches, files, likes, misc, settings, stats, validation

# Rule 2 (folder exports router)
from kohakuhub.api.org import router as org
from kohakuhub.api.quota import router as quota
from kohakuhub.api.invitation import router as invitation

# Rule 3 (multiple routers)
from kohakuhub.api.commit import router as commits, history as commit_history
from kohakuhub.api.repo.routers import crud, info, tree
from kohakuhub.api.git.routers import http as git_http, lfs, ssh_keys

# Usage in app.include_router():
app.include_router(admin.router, ...)      # admin IS a module with .router
app.include_router(org, ...)               # org IS the router (imported as router)
app.include_router(commit_history.router, ...)  # commit_history is a module
```

### Database Operations

KohakuHub uses **synchronous database operations** with Peewee ORM for simplicity and multi-worker compatibility.

**✅ Use db.atomic() for transactions:**
```python
from kohakuhub.db import Repository, User, db

async def create_repository(repo_type: str, namespace: str, name: str, owner: User):
    """Create repository with transaction safety."""
    with db.atomic():
        # Check if exists
        existing = Repository.get_or_none(
            Repository.repo_type == repo_type,
            Repository.namespace == namespace,
            Repository.name == name,
        )
        if existing:
            raise ValueError("Repository already exists")

        # Create repository
        repo = Repository.create(
            repo_type=repo_type,
            namespace=namespace,
            name=name,
            full_id=f"{namespace}/{name}",
            owner=owner,
        )
        return repo
```

**✅ Simple queries don't need transactions:**
```python
from kohakuhub.db import Repository

async def get_repository(repo_type: str, namespace: str, name: str):
    """Get repository - no transaction needed for simple reads."""
    return Repository.get_or_none(
        Repository.repo_type == repo_type,
        Repository.namespace == namespace,
        Repository.name == name,
    )
```

**Why Synchronous?**
- PostgreSQL and SQLite handle concurrent connections internally
- `db.atomic()` ensures ACID compliance across workers
- Simpler code without async/await complexity
- Better compatibility with multi-worker setups

### Permission Checks

Always check permissions before write operations:

```python
from kohakuhub.auth.permissions import check_repo_write_permission, check_repo_read_permission

async def upload_file(repo: Repository, user: User):
    # Check permission first
    check_repo_write_permission(repo, user)

    # Then proceed with operation
    ...

async def download_file(repo: Repository, user: User | None):
    # Check read permission (user can be None for public repos)
    check_repo_read_permission(repo, user)

    # Then proceed
    ...
```

### Error Handling

Use HuggingFace-compatible error responses:

```python
from fastapi import HTTPException

raise HTTPException(
    status_code=404,
    detail={"error": "Repository not found"},
    headers={"X-Error-Code": "RepoNotFound"}
)
```

### Logging

Use the custom logger system with colored output:

```python
from kohakuhub.logger import get_logger

logger = get_logger("MY_MODULE")

# Log different levels
logger.debug("Verbose debugging info")
logger.info("General information")
logger.success("Operation completed successfully")
logger.warning("Something unusual happened")
logger.error("An error occurred")

# Exception handling with formatted traceback
try:
    risky_operation()
except Exception as e:
    logger.exception("Operation failed", e)
    # Automatically prints formatted traceback with stack frames
```

**Pre-created loggers available:**
- `logger_auth`, `logger_file`, `logger_lfs`, `logger_repo`, `logger_org`, `logger_settings`, `logger_api`, `logger_db`, `logger_admin`, `logger_quota`, `logger_likes`, `logger_stats`

### Frontend Code Style

**Core Principles:**
- JavaScript only (no TypeScript), use JSDoc comments for type hints
- Vue 3 Composition API with `<script setup>`
- Split reusable components
- **Always** implement dark/light mode together using `dark:` classes
- Mobile responsive design
- Use `prettier` for code formatting
- UnoCSS for styling

**Example:**
```vue
<script setup>
// Use composition API
import { ref, computed, onMounted } from 'vue'

// Reactive state
const data = ref(null)
const loading = ref(false)

// Computed properties
const isReady = computed(() => data.value !== null)

// Async operations
async function fetchData() {
  loading.value = true
  try {
    const response = await fetch('/api/endpoint')
    data.value = await response.json()
  } catch (error) {
    // Handle error
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchData()
})
</script>

<template>
  <!-- Always support dark mode -->
  <div class="bg-white dark:bg-gray-900 text-black dark:text-white">
    <div v-if="loading">Loading...</div>
    <div v-else-if="isReady">{{ data }}</div>
  </div>
</template>
```

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- Docker & Docker Compose
- Git

### Setup

```bash
git clone https://github.com/KohakuBlueleaf/KohakuHub.git
cd KohakuHub

# Backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Frontend
npm install --prefix ./src/kohaku-hub-ui
npm install --prefix ./src/kohaku-hub-admin

# Start with Docker
cp docker-compose.example.yml docker-compose.yml
# IMPORTANT: Edit docker-compose.yml to change default passwords and secrets
python ./scripts/deploy.py
```

**Access:** http://localhost:28080

## Development Workflow

### Backend Development

**Implementation Notes:**
- **LakeFS:** Uses REST API directly (lakefs_rest_client.py) instead of deprecated lakefs-client library. All LakeFS operations are pure async.
- **Database:** Synchronous operations with Peewee ORM and `db.atomic()` transactions. Safe for multi-worker deployments (4-8 workers recommended).

```bash
# Start infrastructure
docker-compose up -d lakefs minio postgres

# Single worker (development with hot reload)
uvicorn kohakuhub.main:app --reload --port 48888

# Multi-worker (production-like testing)
uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 4

# API documentation available at:
# http://localhost:48888/docs
```

### Frontend Development

```bash
# Run frontend dev server (proxies API to localhost:48888)
npm run dev --prefix ./src/kohaku-hub-ui

# Access at http://localhost:5173
```

### Full Docker Deployment

```bash
# Build frontend and start all services
npm run build --prefix ./src/kohaku-hub-ui
npm run build --prefix ./src/kohaku-hub-admin
docker-compose up -d --build

# View logs
docker-compose logs -f hub-api
docker-compose logs -f hub-ui
```

## How to Contribute

### Reporting Bugs

Create an issue with:
- Clear title
- Steps to reproduce
- Expected vs actual behavior
- Environment (OS, Python/Node version)
- Logs/error messages

### Suggesting Features

- Check [Project Status](#project-status) first
- Open GitHub issue or discuss on Discord
- Describe use case and value
- Propose implementation approach

### Contributing Code

1. Pick an issue or create one
2. Fork and create branch
3. Make changes following style guidelines
4. Test thoroughly
5. Submit pull request

## Best Practices

### Adding a New API Endpoint

1. Add route in appropriate router (`api/routers/` or feature-specific)
2. Use auth dependency: `user: User = Depends(get_current_user)` or `Depends(get_optional_user)`
3. Follow HuggingFace error format: raise `HTTPException(status_code, detail={"error": "message"})`
4. Import router in `main.py`: `app.include_router(router, prefix=cfg.app.api_base)`

**Example:**
```python
from fastapi import APIRouter, Depends
from kohakuhub.auth.dependencies import get_current_user
from kohakuhub.db import User

router = APIRouter()

@router.get("/{type}s/{namespace}/{name}/my-endpoint")
async def my_endpoint(
    type: str,
    namespace: str,
    name: str,
    user: User = Depends(get_current_user)
):
    # Implementation
    return {"status": "ok"}
```

### Database Schema Changes

1. Modify models in `src/kohakuhub/db.py`
2. Add new model to `init_db()` table list
3. Database auto-creates tables on startup (Peewee ORM)
4. For complex migrations, manually handle in production (Peewee doesn't have migrations)

### Adding Frontend Route/Page

1. Create file in `src/kohaku-hub-ui/src/pages/` with route pattern
2. Use auto-imported composables: `useRoute()`, `useRouter()`
3. API calls via `utils/api.js` (e.g., `repoAPI.getInfo(type, namespace, name)`)
4. Components auto-import from `src/components/`

**Example:**
```vue
<template>
  <div>
    <h1>{{ repo.name }}</h1>
  </div>
</template>

<script setup>
import { repoAPI } from '@/utils/api'

const route = useRoute()
const { type, namespace, name } = route.params

const repo = ref(null)

onMounted(async () => {
  const res = await repoAPI.getInfo(type, namespace, name)
  repo.value = res.data
})
</script>
```

## Pull Request Process

1. **Before submitting:**
   - Update relevant documentation (API.md, CLI.md, etc.)
   - Add tests for new functionality
   - Ensure code follows style guidelines
   - Test in both development and Docker deployment modes
   - Run `black` on Python code
   - Run `prettier` on frontend code

2. **Submitting PR:**
   - Create a clear, descriptive title
   - Describe what changes were made and why
   - Link related issues
   - Include screenshots for UI changes
   - List any breaking changes
   - Request review from maintainers

3. **After submission:**
   - Address feedback promptly
   - Keep PR focused (split large changes into multiple PRs)
   - Rebase on main if needed

## Project Status

*Last Updated: January 2025*

### ✅ Core Features (Complete)

**API & Storage:**
- HuggingFace Hub API compatibility
- Git LFS protocol for large files
- File deduplication (SHA256)
- Repository management (create, delete, list, move/rename)
- Branch and tag management
- Commit history
- S3-compatible storage (MinIO, AWS S3, etc.)
- LakeFS versioning (branches, commits, diffs) - using REST API directly

**Authentication:**
- User registration with email verification (optional)
- Session-based auth + API tokens
- Organization management with role-based access
- Permission system (namespace-based)
- SSH key management

**Web UI:**
- Vue 3 interface with dark/light mode
- Repository browsing and file viewer
- Code editor (CodeMirror 6) with syntax highlighting
- Markdown rendering with Mermaid chart support
- Commit history viewer
- Settings pages (user, org, repo)
- Documentation viewer

**Admin Portal:**
- User management (create, delete, email verification toggle)
- Repository browser with statistics
- Commit history viewer across all repositories
- S3 storage browser
- Quota management (users, organizations, repositories)
- System statistics dashboard
- Time-series analytics
- Invitation management

**CLI Tool:**
- Full-featured `kohub-cli` with interactive TUI mode
- Repository, organization, user management
- Branch/tag operations
- File upload/download
- Commit history viewing
- LFS settings management
- Health check
- Operation history tracking
- Shell autocomplete (bash/zsh/fish)

**Social Features:**
- Repository likes (similar to GitHub stars)
- Trending repositories (based on download activity)
- Download tracking and statistics
- Avatar management (users and organizations)

**Quota System:**
- User and organization storage quotas (separate private/public)
- Repository-specific quotas
- Storage usage tracking
- Automatic quota enforcement
- Recalculation and sync tools

**Invitations:**
- Organization invitations
- Registration invitations (for invite-only mode)
- Reusable invitations with usage limits
- Email notifications (optional)

**Git Support:**
- Native Git clone support (pure Python implementation)
- Git LFS integration
- Automatic LFS pointers for large files (>1MB)
- Memory-efficient (no temp files)
- SSH key authentication support

### 🚧 In Progress

**KohakuHub:**
- Rate limiting
- Repository transfer between namespaces
- Search functionality
- Git push support

**KohakuBoard** (Standalone Repository - https://github.com/KohakuBlueleaf/KohakuBoard):
- Remote server mode with authentication (WIP)
- Sync protocol for uploading local boards (WIP)
- Frontend UI improvements (WIP)

### 📋 Planned Features

**Advanced Features:**
- Pull requests / merge requests
- Discussion/comments
- Model/dataset card templates
- Automated model evaluation
- Multi-region CDN support
- Webhook system

**UI Improvements:**
- Diff viewer for commits
- Image/media file preview
- Activity feed
- Branch/tag management UI

**Testing & Quality:**
- Unit tests for API endpoints
- Integration tests for HF client
- E2E tests for web UI
- Performance/load testing

## Development Areas

We're especially looking for help in:

### 🎨 Frontend (High Priority)
- Improving UI/UX
- Missing pages (diff viewer, activity feed)
- Mobile responsiveness
- Accessibility

### 🔧 Backend
- Additional HuggingFace API compatibility
- Performance optimizations
- Advanced repository features
- Search functionality

### 📊 KohakuBoard (Standalone Repository)
- See https://github.com/KohakuBlueleaf/KohakuBoard for contributing to KohakuBoard
- Remote server authentication system
- Sync protocol implementation
- Frontend chart improvements
- Documentation and examples

### 📚 Documentation
- Tutorial videos
- Architecture deep-dives
- Deployment guides
- API examples

### 🧪 Testing
- Unit test coverage
- Integration tests
- E2E scenarios
- Load testing

## Community

- **Discord:** https://discord.gg/xWYrkyvJ2s
- **GitHub Issues:** https://github.com/KohakuBlueleaf/KohakuHub/issues

## License and Copyright

By contributing, you agree to the following:

1. **License Grant**: Your contributions will be licensed under AGPL-3.0 for the main project, or under a non-commercial license for specific modules as designated by the project maintainer.

2. **Commercial Licensing Rights**: You grant KohakuBlueLeaf (the project owner) perpetual, irrevocable rights to:
   - Relicense your contributions under commercial terms
   - Include your contributions in commercial exemption licenses sold to third parties
   - Use your contributions in any way necessary for the commercial operation of this project

3. **Copyright**: You retain copyright to your contributions, but grant the above license rights to the project.

---

Thank you for contributing to KohakuHub!
