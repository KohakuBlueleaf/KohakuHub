# KohakuHub Deployment Architecture

## Setup Instructions

### First Time Setup

#### Option 1: Interactive Generator (Recommended)

Use the interactive generator to create a customized `docker-compose.yml`:

```bash
# Run the generator
python scripts/generate_docker_compose.py
```

The generator will ask you to configure:
- PostgreSQL (built-in container or external database)
- LakeFS database backend (PostgreSQL or SQLite)
- S3 storage (built-in MinIO or external S3/R2)
- Security keys (auto-generated or custom)

See [scripts/README.md](../scripts/README.md#docker-compose-generator) for detailed usage.

#### Option 2: Manual Configuration

1. **Copy configuration file:**
   ```bash
   cp docker-compose.example.yml docker-compose.yml
   ```

2. **Edit docker-compose.yml:**
   - Change MinIO credentials (MINIO_ROOT_USER, MINIO_ROOT_PASSWORD)
   - Change PostgreSQL password (POSTGRES_PASSWORD)
   - Change LakeFS secret key (LAKEFS_AUTH_ENCRYPT_SECRET_KEY)
   - Change session secret (KOHAKU_HUB_SESSION_SECRET)
   - Update BASE_URL if deploying to a domain

#### Build and Start

After configuration (either option):

```bash
npm install --prefix ./src/kohaku-hub-ui
npm run build --prefix ./src/kohaku-hub-ui
docker-compose up -d --build
```

**Note:** The repository only includes `docker-compose.example.yml` as a template. Your customized `docker-compose.yml` is excluded from git to prevent committing sensitive credentials.

## Port Configuration

### Production Deployment (Docker)

**Exposed Port:**
- **28080** - Main entry point (Web UI + API via nginx reverse proxy)

**Internal Ports (not exposed to users):**
- 48888 - Backend API server (proxied by nginx)
- 28000 - LakeFS UI (admin only)
- 29000 - MinIO Console (admin only)
- 29001 - MinIO S3 API (used by backend)
- 25432 - PostgreSQL (optional, for external access)

### Nginx Reverse Proxy

**Configuration:** `docker/nginx/default.conf`

```mermaid
graph TD
    A[Client] --> B{Request}
    B --> C{/api/*}
    B --> D{/org/*}
    B --> E{/{ns}/{repo}.git/*}
    B --> F{/resolve/*}
    B --> G[Static Files]
    C --> H[FastAPI]
    D --> H
    E --> H
    F --> H
    G --> I[Vue 3 Frontend]
```

**Nginx routing rules:**
1. Serves frontend static files from `/usr/share/nginx/html`
2. Proxies API requests to `hub-api:48888`:
   - `/api/*` → API endpoints
   - `/org/*` → Organization endpoints
   - `/{namespace}/{name}.git/*` → Git Smart HTTP protocol
   - `/{type}s/{namespace}/{name}/resolve/*` → File download endpoints
   - `/admin/*` → Admin portal (if enabled)

### Client Configuration

**For HuggingFace Client:**
```python
import os
os.environ["HF_ENDPOINT"] = "http://localhost:28080"  # Use nginx port
os.environ["HF_TOKEN"] = "your_token"
```

**For kohub-cli:**
```bash
export HF_ENDPOINT=http://localhost:28080
kohub-cli auth login
```

**For Git Clone:**
```bash
# Clone repository
git clone http://localhost:28080/namespace/repo.git

# With authentication (private repos)
git clone http://username:token@localhost:28080/namespace/repo.git

# Download large files
cd repo
git lfs install
git lfs pull
```

**❌ WRONG:**
```python
os.environ["HF_ENDPOINT"] = "http://localhost:48888"  # Don't use backend port directly
```

## Architecture Diagram

```mermaid
graph TD
    A[Client] --> B[Nginx]
    B --> C[FastAPI]
    C --> D[LakeFS]
    C --> E[PostgreSQL]
    C --> F[MinIO]
    D --> F
```

**Port Mapping:**
- **28080** - Public entry point (Nginx)
- **48888** - Internal FastAPI (not exposed)
- **28000** - LakeFS admin UI (optional, for admins)
- **29000** - MinIO console (optional, for admins)
- **29001** - MinIO S3 API (internal + public for downloads)
- **25432** - PostgreSQL (optional, for external access)

## Development vs Production

### Development

**Frontend Dev Server** (port 5173):
```bash
npm run dev --prefix ./src/kohaku-hub-ui
# Proxies /api → http://localhost:48888
```

**Backend** (port 48888):
```bash
# Single worker (development with hot reload)
uvicorn kohakuhub.main:app --reload --port 48888

# Multi-worker (production-like testing)
uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 4
```

**Client Access:**
- Frontend: http://localhost:5173
- API: http://localhost:48888 (direct)
- Swagger Docs: http://localhost:48888/docs

### Production (Docker)

**All services via docker-compose:**
```bash
./deploy.sh
```

**Client Access:**
- **Everything:** http://localhost:28080 (Web UI + API)
- Swagger Docs (dev): http://localhost:48888/docs (if port exposed)

## Multi-Worker Deployment

KohakuHub supports horizontal scaling with multiple worker processes.

### Database Architecture

**Synchronous Database Operations:**
- Uses Peewee ORM with synchronous operations
- `db.atomic()` transactions ensure consistency across workers
- No async database wrappers needed
- Safe for multi-worker deployments

**Why Synchronous?**
- PostgreSQL and SQLite handle concurrent connections internally
- Atomic transactions prevent race conditions
- Simpler code without async/await complexity
- Better compatibility with multi-worker setups

**Future:** Migration to peewee-async is planned for improved concurrency.

### Running Multi-Worker

**Development/Testing:**
```bash
# 4 workers (recommended for testing)
uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 4

# 8 workers (production-like load)
uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 8
```

**Docker Deployment:**
```yaml
# docker-compose.yml
services:
  hub-api:
    command: uvicorn kohakuhub.main:app --host 0.0.0.0 --port 48888 --workers 4
```

### Worker Recommendations

| Deployment | Workers | CPU | Memory | Notes |
|------------|---------|-----|--------|-------|
| Development | 1 | 2 cores | 2GB | Hot reload enabled |
| Small | 2-4 | 4 cores | 4GB | For <100 users |
| Medium | 4-8 | 8 cores | 8GB | For <1000 users |
| Large | 8-16 | 16+ cores | 16GB+ | For >1000 users |

**Formula:** Workers = (2 × CPU cores) + 1

### Benefits of Multi-Worker

1. **Horizontal Scaling:** Handle more concurrent requests
2. **High Availability:** Worker crashes don't affect others
3. **Better Resource Utilization:** Leverage multiple CPU cores
4. **Load Distribution:** Requests distributed across workers

### Limitations

- Cannot use `--reload` with multiple workers
- Shared state must use database or external cache
- Log aggregation recommended for debugging

## Security Best Practices

### Production Deployment

1. **Only expose port 28080**
   ```yaml
   # docker-compose.yml
   hub-ui:
     ports:
       - "28080:80"  # ONLY THIS PORT

   hub-api:
     # NO ports section - internal only
   ```

2. **Use HTTPS with reverse proxy**
   ```nginx
   # Production nginx config
   server {
       listen 443 ssl;
       server_name your-domain.com;

       ssl_certificate /etc/nginx/ssl/cert.pem;
       ssl_certificate_key /etc/nginx/ssl/key.pem;

       location / {
           proxy_pass http://hub-ui:80;
       }
   }
   ```

3. **Set BASE_URL to your domain**
   ```yaml
   environment:
     - KOHAKU_HUB_BASE_URL=https://your-domain.com
     - KOHAKU_HUB_S3_PUBLIC_ENDPOINT=https://s3.your-domain.com
   ```

## Common Mistakes

❌ **Don't do this:**
```python
# Wrong - bypassing nginx
os.environ["HF_ENDPOINT"] = "http://localhost:48888"
```

✅ **Do this:**
```python
# Correct - using nginx reverse proxy
os.environ["HF_ENDPOINT"] = "http://localhost:28080"
```

## Data Flow Examples

### Upload Flow (with LFS)

```mermaid
sequenceDiagram
    participant C as Client
    participant N as Nginx
    participant S as FastAPI
    participant L as LakeFS
    participant M as MinIO

    C->>N: 1. Upload request
    N->>S: 2. Forward request
    S->>L: 3. Get presigned URL
    L->>M: 4. Generate URL
    M-->>L: 5. Presigned URL
    L-->>S: 6. Presigned URL
    S-->>N: 7. Presigned URL
    N-->>C: 8. Presigned URL
    C->>M: 9. Upload file
    M-->>C: 10. Upload complete
    C->>N: 11. Commit file
    N->>S: 12. Forward request
    S->>L: 13. Commit file
    L-->>S: 14. Commit complete
    S-->>N: 15. Commit complete
    N-->>C: 16. Commit complete
```

### Download Flow (Direct S3)

```mermaid
sequenceDiagram
    participant C as Client
    participant N as Nginx
    participant S as FastAPI
    participant L as LakeFS
    participant M as MinIO

    C->>N: 1. Download request
    N->>S: 2. Forward request
    S->>L: 3. Get presigned URL
    L->>M: 4. Generate URL
    M-->>L: 5. Presigned URL
    L-->>S: 6. Presigned URL
    S-->>N: 7. Presigned URL
    N-->>C: 8. Presigned URL
    C->>M: 9. Download file
    M-->>C: 10. Download complete
```

## Why This Architecture?

1. **Single Entry Point:** Users only need to know one port (28080)
2. **Security:** Backend (48888) not exposed to internet
3. **SSL Termination:** Nginx handles HTTPS
4. **Static File Serving:** Nginx serves frontend efficiently
5. **Load Balancing:** Can add multiple backend instances behind nginx
6. **Caching:** Nginx can cache static assets
7. **Direct Downloads:** Files downloaded directly from S3, not proxied
8. **Scalability:** Each component can scale independently

## Troubleshooting

### "Connection refused to localhost:48888"

**Problem:** Client trying to connect directly to backend

**Solution:** Change `HF_ENDPOINT` to use port 28080:
```bash
export HF_ENDPOINT=http://localhost:28080
```

### "CORS errors in browser"

**Problem:** Frontend trying to access wrong port

**Solution:** Ensure `KOHAKU_HUB_BASE_URL` is set correctly:
```yaml
environment:
  - KOHAKU_HUB_BASE_URL=http://localhost:28080
```

### "API calls returning HTML instead of JSON"

**Problem:** Hitting nginx for a non-proxied path

**Solution:** Check nginx config ensures all API paths are proxied
