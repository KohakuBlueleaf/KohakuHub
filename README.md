# Kohaku Hub - Self-hosted HuggingFace Alternative

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/theblackcat102/KohakuHub/4a32355364b40c751735d59c651dab023f6c6d68/images/logo-banner-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/theblackcat102/KohakuHub/4a32355364b40c751735d59c651dab023f6c6d68/images/logo-banner.svg">
    <img alt="kohub library logo" src="https://raw.githubusercontent.com/theblackcat102/KohakuHub/4a32355364b40c751735d59c651dab023f6c6d68/images/logo-banner.svg" width="600" height="80" style="max-width: 100%; padding-left: 100px">
  </picture>
  <br/>
  <br/>
</p> 



<p align="center">
    <a href="https://github.com/KohakuBlueleaf/KohakuHub"><img alt="GitHub" src="https://img.shields.io/github/stars/KohakuBlueleaf/KohakuHub?style=social"></a>
    <!-- <a href="https://github.com/KohakuBlueleaf/KohakuHub/releases"><img alt="GitHub release" src="https://img.shields.io/github/release/KohakuBlueleaf/KohakuHub.svg"></a>
    <a href="https://pypi.org/project/kohub-cli"><img alt="PyPI - Python Version" src="https://img.shields.io/pypi/pyversions/kohub-cli.svg"></a> -->
    <a href="https://github.com/KohakuBlueleaf/KohakuHub/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/KohakuBlueleaf/KohakuHub"></a>
    <a href="https://deepwiki.com/KohakuBlueleaf/KohakuHub"><img alt="Ask DeepWiki" src="https://deepwiki.com/badge.svg"></a>
</p>

---

**🚀 Active Development - Alpha Release Ready**

DEMO Site (**testing only, no guarantee on data integrity**): https://hub.kohaku-lab.org

Self-hosted HuggingFace alternative with Git-like versioning for AI models and datasets. Compatible* with the official `huggingface_hub` Python client.

> **Status:** Core features are complete and functional. Ready for testing and early adoption. APIs may evolve as we gather feedback.

> *: May not perform exactly same behavior, if you meet any unexpected result, feel free to open issue.

</div>

|![1761148256913](image/README/1761148256913.png)|![1761148225463](image/README/1761148225463.png)|
|-|-|

**Join our community:** https://discord.gg/xWYrkyvJ2s

## Features

### KohakuHub (Model/Dataset Repository)

- **HuggingFace Compatible** - Drop-in replacement for `huggingface_hub`, `hfutils`, `transformers`, `diffusers`
- **External Source Fallback** - Browse HuggingFace (or other KohakuHub instances) when repos not found locally
- **User External Tokens** - Configure your own tokens for external sources (HuggingFace, etc.) with encrypted storage
- **Native Git Clone** - Standard Git operations (clone) with Git LFS support
- **Git-Like Versioning** - Branches, commits, tags via LakeFS
- **S3 Storage** - Works with MinIO, AWS S3, Cloudflare R2, etc.
- **Large File Support** - Git LFS protocol with automatic LFS pointers (>1MB files)
- **Organizations** - Multi-user namespaces with role-based access
- **Quota Management** - Storage quotas for users and organizations
- **Web UI** - Vue 3 interface with file browser, editor, commit history, Mermaid chart support
- **Admin Portal** - Comprehensive admin interface for user and repository management
- **CLI Tool** - Full-featured command-line interface with interactive TUI mode
- **File Deduplication** - Content-addressed storage by SHA256
- **Trending & Likes** - Repository popularity tracking
- **Pure Python Git Server** - No native dependencies, memory-efficient

### KohakuBoard (Experiment Tracking) - Standalone Repository

**Repository:** https://github.com/KohakuBlueleaf/KohakuBoard

- **Non-Blocking Logging** - Background writer process, zero training overhead
- **Rich Data Types** - Scalars, images, videos, tables, histograms
- **Hybrid Storage** - Lance (columnar) + SQLite (row-oriented) for optimal performance
- **Local-First** - View experiments locally with `kobo open`, no server required
- See the KohakuBoard repository for full documentation

## Quick Start

### Deploy with Docker

```bash
git clone https://github.com/KohakuBlueleaf/KohakuHub.git
cd KohakuHub

# Option 1: Use interactive generator (recommended)
python scripts/generate_docker_compose.py

# Option 2: Manual configuration
# cp docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml to change credentials and secrets

# Build frontend and start services
npm install --prefix ./src/kohaku-hub-ui
npm install --prefix ./src/kohaku-hub-admin
npm run build --prefix ./src/kohaku-hub-ui
npm run build --prefix ./src/kohaku-hub-admin
docker-compose up -d --build
```

**Access:**
- Web UI & API: http://localhost:28080 (all traffic goes here)
- Web Admin Portal: http://localhost:28080/admin
    - Use the value of KOHAKU_HUB_ADMIN_SECRET_TOKEN to login the portal
- API Docs (Swagger): http://localhost:48888/docs (direct access for development)
- LakeFS UI: http://localhost:28000
- MinIO Console: http://localhost:29000

**LakeFS credentials:** Auto-generated in `docker/hub-meta/hub-api/credentials.env`

### Use with Python

```python
import os
os.environ["HF_ENDPOINT"] = "http://localhost:28080"
os.environ["HF_TOKEN"] = "your_token_here"

from huggingface_hub import HfApi

api = HfApi()

# Create repo
api.create_repo("my-org/my-model", repo_type="model")

# Upload file
api.upload_file(
    path_or_fileobj="model.safetensors",
    path_in_repo="model.safetensors",
    repo_id="my-org/my-model",
)

# Download file
api.hf_hub_download(repo_id="my-org/my-model", filename="model.safetensors")
```

### Use with Transformers/Diffusers

```python
import os
os.environ["HF_ENDPOINT"] = "http://localhost:28080"
os.environ["HF_TOKEN"] = "your_token_here" # needed for private repository

from diffusers import AutoencoderKL
vae = AutoencoderKL.from_pretrained("my-org/my-model")
```

### CLI Tool

```bash
# Install
pip install -e .

# Interactive mode
kohub-cli interactive

# Command mode
kohub-cli auth login
kohub-cli repo create my-org/my-model --type model
kohub-cli repo list --type model
kohub-cli org create my-org
kohub-cli org member add my-org alice --role admin
```

See [docs/CLI.md](./docs/CLI.md) for complete CLI documentation.

### Git Clone (Native Git Support)

```bash
# Clone repository (fast - only metadata and small files)
git clone http://localhost:28080/namespace/repo-name.git

# For private repositories, use token authentication
git clone http://username:your-token@localhost:28080/namespace/private-repo.git

# Install Git LFS for large files
cd repo-name
git lfs install
git lfs pull  # Download large files (>1MB)

# (push operations coming soon)
```

**How it works:**
- Files **<1MB**: Included directly in Git pack (fast clone)
- Files **>=1MB**: Stored as LFS pointers (download via `git lfs pull`)
- Pure Python implementation (no pygit2/libgit2 dependencies)
- Automatic `.gitattributes` and `.lfsconfig` generation
- Memory-efficient (handles repos of any size)

See [docs/Git.md](./docs/Git.md) for complete Git clone documentation and implementation details.

## Architecture

**Stack:**
- **FastAPI** - HuggingFace-compatible API
- **LakeFS** - Git-like versioning (branches, commits, diffs) via REST API
- **MinIO/S3** - Object storage with deduplication
- **PostgreSQL/SQLite** - Metadata database (synchronous with db.atomic() transactions)
- **Vue 3** - Modern web interface

**Implementation Notes:**
- **LakeFS:** Uses REST API directly (lakefs_rest_client.py), providing pure async operations
- **Database:** Synchronous operations with Peewee ORM and `db.atomic()` for transaction safety. Supports multi-worker deployment (4-8 workers) for horizontal scaling.

**Data Flow:**
1. Small files (<10MB) → Base64 in commit payload
2. Large files (>10MB) → Direct S3 upload via presigned URL (LFS protocol)
3. All files linked to LakeFS commits for version control
4. Downloads → 302 redirect to S3 presigned URL (no proxy)

See [docs/API.md](./docs/API.md) for detailed API documentation.

## Configuration

**Environment Variables** (in `docker-compose.yml`):

```yaml
# Application
KOHAKU_HUB_BASE_URL=http://localhost:28080
KOHAKU_HUB_LFS_THRESHOLD_BYTES=10000000  # 10MB

# S3 Storage
KOHAKU_HUB_S3_PUBLIC_ENDPOINT=http://localhost:29001
KOHAKU_HUB_S3_BUCKET=hub-storage

# Database
KOHAKU_HUB_DB_BACKEND=postgres
KOHAKU_HUB_DATABASE_URL=postgresql://hub:pass@postgres:5432/hubdb

# Auth
KOHAKU_HUB_SESSION_SECRET=change-me-in-production
KOHAKU_HUB_REQUIRE_EMAIL_VERIFICATION=false

# Admin Portal
KOHAKU_HUB_ADMIN_ENABLED=true
KOHAKU_HUB_ADMIN_SECRET_TOKEN=change-me-in-production

# External Tokens (for user-specific fallback tokens)
KOHAKU_HUB_DATABASE_KEY=$(openssl rand -hex 32)  # Required for encryption
```

See [config-example.toml](./config-example.toml) for all options.

### External Fallback Tokens

Users can provide their own tokens for external sources (e.g., HuggingFace) to access private repositories:

**Via Web UI:**
1. Go to Settings → External Tokens
2. Add your HuggingFace token
3. Tokens are encrypted and stored securely

**Via CLI:**
```bash
kohub-cli settings user external-tokens add --url https://huggingface.co --token hf_abc123
```

**Via Authorization Header (API/programmatic):**
```bash
curl -H "Authorization: Bearer my_token|https://huggingface.co,hf_abc123" \
  http://localhost:28080/api/models/org/model
```

**How it works:**
- User tokens override admin-configured tokens
- Tokens encrypted at rest using AES-256
- Works with session auth, API tokens, and anonymous requests
- Automatically used when repos not found locally

## Development

**Backend:**
```bash
pip install -e .

# Single worker (development)
uvicorn kohakuhub.main:app --reload --port 48888

# Multi-worker (production-like testing)
uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 4

# Note: Database uses db.atomic() for transaction safety in multi-worker setups
# Note: In production, access via nginx on port 28080
```

**Frontend:**
```bash
npm install --prefix ./src/kohaku-hub-ui
npm run dev --prefix ./src/kohaku-hub-ui
```

**Testing:**
```bash
python scripts/test.py
python scripts/test_auth.py
```

## Documentation

- [docs/setup.md](./docs/setup.md) - Setup and installation guide
- [docs/deployment.md](./docs/deployment.md) - Deployment architecture
- [docs/ports.md](./docs/ports.md) - Port configuration reference
- [docs/API.md](./docs/API.md) - API endpoints and workflows
- [docs/CLI.md](./docs/CLI.md) - Command-line tool usage
- [docs/Admin.md](./docs/Admin.md) - Admin portal & fallback system
- [docs/Git.md](./docs/Git.md) - Git clone support
- [CONTRIBUTING.md](./CONTRIBUTING.md) - Contributing guide & roadmap

## Security Notes

⚠️ **Before Production:**
- Change all default passwords in `docker-compose.yml`
- Set secure `KOHAKU_HUB_SESSION_SECRET`
- Set secure `KOHAKU_HUB_ADMIN_SECRET_TOKEN`
- Set secure `LAKEFS_AUTH_ENCRYPT_SECRET_KEY`
- Use HTTPS with reverse proxy
- Only expose port 28080 (Web UI)

## Known Limitations

While core features are stable for alpha release, some advanced features are still in development:

- Repository transfer/squash/delete are experimental/not stable
- Some HuggingFace API endpoints may be incomplete
    - Feel free to open issue in this case, but remember to provide full information and minimal reproduction!

See [CONTRIBUTING.md](./CONTRIBUTING.md#project-status) for full roadmap.

## License

AGPL-3.0

**NOTE**: We may release some new features under non-commercial license.

**Commercial Exemption**: If you need any commercial exemption licenses (to not fully open source your system built upon KohakuHub), please contact kohaku@kblueleaf.net

## Support

- **Discord:** https://discord.gg/xWYrkyvJ2s
- **Issues:** https://github.com/KohakuBlueleaf/KohakuHub/issues

## Acknowledgments

- [HuggingFace](https://huggingface.co/) - API design and client library
- [LakeFS](https://lakefs.io/) - Data versioning engine (REST API)
- [MinIO](https://min.io/) - Object storage

---

**Ready for Alpha Testing!** Core features are stable, but APIs may evolve based on community feedback. Use in development/testing environments and help us improve.
