---
title: Production Deployment
description: SSL, domain setup, external S3, security hardening
icon: i-carbon-cloud-upload
---

# Production Deployment

Deploy KohakuHub for production use.

## Component Versions

- **LakeFS ≥ v0.54.0** (released 2021-11-08). The bundled docker compose
  uses `treeverse/lakefs:latest` and is always compatible. If your
  production stack pins an older LakeFS image, upgrade before rolling out
  KohakuHub — the file-list `expand=true` endpoint depends on
  path-filtered `logCommits` (`objects=` / `prefixes=` / `limit=`)
  introduced in v0.54.0; pre-v0.54 servers silently drop those
  parameters and would surface wrong `lastCommit` metadata.

## SSL & Domain

**nginx config:**
```nginx
server {
    listen 443 ssl http2;
    server_name hub.yourdomain.com;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # Forward every KohakuHub path to the backend. The hf_hub-compatible
    # public URLs (no /api prefix) MUST be reachable at the root since
    # huggingface_hub clients hit them directly:
    #   /<repo_type>s/<ns>/<name>/resolve/<rev>/<path>   (HEAD/GET file)
    #   /<repo_type>s/<ns>/<name>/tree/<rev>/...         (list files)
    #   /<ns>/<name>/resolve/...                         (model default)
    # The chain tester in the admin SPA exercises these same routes
    # (see src/kohaku-hub-admin/vite.config.js for the dev-mode mirror)
    # so misconfigured nginx → CHAIN_EXHAUSTED on every probe.
    location / {
        proxy_pass http://kohakuhub-backend:48888;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Admin SPA static bundle — path-prefixed under /admin so it
    # coexists with the hf_hub-compat root paths.
    location /admin/ {
        alias /var/www/kohakuhub-admin/;
        try_files $uri $uri/ /admin/index.html;
    }
}
```

**Update base URL:**
```yaml
KOHAKU_HUB_BASE_URL: https://hub.yourdomain.com
```

## External S3

**Cloudflare R2:**
```yaml
KOHAKU_HUB_S3_ENDPOINT: https://account.r2.cloudflarestorage.com
KOHAKU_HUB_S3_PUBLIC_ENDPOINT: https://pub.r2.dev
KOHAKU_HUB_S3_REGION: auto
KOHAKU_HUB_S3_SIGNATURE_VERSION: s3v4
```

**AWS S3:**
```yaml
KOHAKU_HUB_S3_ENDPOINT: https://s3.amazonaws.com
KOHAKU_HUB_S3_REGION: us-east-1
KOHAKU_HUB_S3_FORCE_PATH_STYLE: false
```

## Security

**Change all secrets:**
```bash
python scripts/generate_secret.py
# Update SESSION_SECRET, ADMIN_SECRET_TOKEN
```

**Change passwords:**
- PostgreSQL
- MinIO
- LakeFS

## Scaling

**Multi-worker:**
```yaml
command: uvicorn kohakuhub.main:app --workers 8
```

Database uses `db.atomic()` for safety.

## Backups

```bash
docker exec postgres pg_dump -U hub kohakuhub | gzip > backup.sql.gz
```

See [Security](./security.md) for hardening guide.
