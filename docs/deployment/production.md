---
title: Production Deployment
description: SSL, domain setup, external S3, security hardening
icon: i-carbon-cloud-upload
---

# Production Deployment

Deploy KohakuHub for production use.

## SSL & Domain

**nginx config:**
```nginx
server {
    listen 443 ssl http2;
    server_name hub.yourdomain.com;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
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
