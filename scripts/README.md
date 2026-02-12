# KohakuHub Utility Scripts

This directory contains utility scripts for KohakuHub administration and maintenance.

## Deployment Setup

### Docker Compose Generator

Interactive tool to generate a customized `docker-compose.yml` based on your deployment preferences.

**Features:**
- ✅ Choose between built-in or external PostgreSQL
- ✅ Configure LakeFS to use PostgreSQL or SQLite
- ✅ Choose between built-in MinIO or external S3 storage
- ✅ Auto-generate secure secret keys
- ✅ Comprehensive configuration validation

**Usage:**

```bash
# Interactive mode (asks questions)
python scripts/generate_docker_compose.py

# Generate a configuration template
python scripts/generate_docker_compose.py --generate-config

# Use configuration file (non-interactive)
python scripts/generate_docker_compose.py --config kohakuhub.conf
```

**Configuration Options:**

1. **PostgreSQL:**
   - Built-in container (managed by docker-compose)
   - External PostgreSQL (specify host, port, credentials)
   - Default database name for hub-api: `kohakuhub`

2. **LakeFS Database:**
   - Use PostgreSQL (recommended for production)
   - Use local SQLite (simpler for development)
   - Default database name: `lakefs` (separate from hub-api database)
   - **Automatic database creation**: Both databases are created automatically when LakeFS starts
   - Works with both built-in and external PostgreSQL

3. **S3 Storage:**
   - Built-in MinIO container (self-hosted)
   - External S3-compatible storage (AWS S3, CloudFlare R2, etc.)

4. **Security:**
   - Auto-generated session secret key
   - Auto-generated admin secret token
   - Option to use custom secrets

5. **Network:**
   - External Docker bridge network support
   - Allows cross-compose communication with external PostgreSQL/S3 services
   - Automatically added to hub-api and lakefs when using external services

**Example: Interactive Mode**

```
$ python scripts/generate_docker_compose.py

=============================================================
KohakuHub Docker Compose Generator
=============================================================

--- PostgreSQL Configuration ---
Use built-in PostgreSQL container? [Y/n]: y
PostgreSQL username [hub]:
PostgreSQL password [hubpass]:
PostgreSQL database name for hub-api [kohakuhub]:

--- LakeFS Database Configuration ---
Use PostgreSQL for LakeFS? (No = use local SQLite) [Y/n]: y
PostgreSQL database name for LakeFS [lakefs]:

--- S3 Storage Configuration ---
Use built-in MinIO container? [Y/n]: n
S3 endpoint URL: https://my-account.r2.cloudflarestorage.com
S3 access key: xxxxxxxxxxxx
S3 secret key: yyyyyyyyyyyy
S3 region [us-east-1]: auto

--- Security Configuration ---
Generated session secret: AbCdEf123456...
Use generated session secret? [Y/n]: y

Use same secret for admin token? [y/N]: n
Generated admin secret: XyZ789...
Use generated admin secret? [Y/n]: y

=============================================================
Generating docker-compose.yml...
=============================================================

✓ Successfully generated: docker-compose.yml
✓ Database initialization scripts will run automatically when LakeFS starts
  - scripts/init-databases.sh
  - scripts/lakefs-entrypoint.sh

Configuration Summary:
------------------------------------------------------------
PostgreSQL: Built-in
  Hub-API Database: kohakuhub
  LakeFS Database: lakefs
LakeFS Database Backend: PostgreSQL
S3 Storage: Custom S3
  Endpoint: https://my-account.r2.cloudflarestorage.com
Session Secret: AbCdEf123456...
Admin Secret: XyZ789...
------------------------------------------------------------

Next steps:
1. Review the generated docker-compose.yml
2. Build frontend: npm run build --prefix ./src/kohaku-hub-ui
3. Start services: docker-compose up -d

   Note: Databases will be created automatically on first startup:
   - kohakuhub (hub-api)
   - lakefs (LakeFS)

4. Access at: http://localhost:28080
```

**Example: Using Configuration File**

```bash
# Step 1: Generate template
$ python scripts/generate_docker_compose.py --generate-config
[OK] Generated configuration template: kohakuhub.conf

Edit this file with your settings, then run:
  python scripts/generate_docker_compose.py --config kohakuhub.conf

# Step 2: Edit kohakuhub.conf with your settings
$ nano kohakuhub.conf  # or use any text editor

# Step 3: Generate docker-compose.yml from config
$ python scripts/generate_docker_compose.py --config kohakuhub.conf

============================================================
KohakuHub Docker Compose Generator
============================================================

Loading configuration from: kohakuhub.conf

Loaded configuration:
  PostgreSQL: External
    Host: db.example.com:5432
    Database: kohakuhub
  LakeFS: PostgreSQL
    Database: lakefs
  S3: External S3
    Endpoint: https://s3.example.com

============================================================
Generating docker-compose.yml...
============================================================

[OK] Successfully generated: docker-compose.yml
[OK] Database initialization scripts will run automatically when LakeFS starts

Configuration Summary:
------------------------------------------------------------
PostgreSQL: Custom
  Host: db.example.com:5432
  Hub-API Database: kohakuhub
  LakeFS Database: lakefs
LakeFS Database Backend: PostgreSQL
S3 Storage: Custom S3
  Endpoint: https://s3.example.com
Session Secret: AbCdEf123456...
Admin Secret: XyZ789...
------------------------------------------------------------
```

**Requirements:**
- Python 3.10+
- No additional dependencies required

**Configuration File Format:**

The configuration file (`kohakuhub.conf`) uses INI format:

```ini
[postgresql]
builtin = true
user = hub
password = hubpass
database = kohakuhub

[lakefs]
use_postgres = true
database = lakefs

[s3]
builtin = true
access_key = minioadmin
secret_key = minioadmin

[security]
session_secret = your-secret-here
admin_secret = your-admin-secret

[network]
# Optional: for cross-compose communication
external_network = shared-network
```

For external PostgreSQL or S3:
```ini
[postgresql]
builtin = false
host = your-postgres-host.com
port = 5432
user = hub
password = your-password
database = kohakuhub

[s3]
builtin = false
endpoint = https://your-s3-endpoint.com
access_key = your-access-key
secret_key = your-secret-key
region = us-east-1

[network]
# Required if external services are in different Docker Compose
external_network = shared-network
```

**Using External Docker Network:**

If PostgreSQL or S3 are in separate Docker Compose setups, you need a shared network:

```bash
# Create the shared network first
docker network create shared-network

# Your PostgreSQL docker-compose.yml
services:
  postgres:
    # ... your config
    networks:
      - shared-network

networks:
  shared-network:
    external: true

# Generate KohakuHub with external network
python scripts/generate_docker_compose.py --config kohakuhub.conf
```

The generator will automatically:
- Add the external network to `hub-api` and `lakefs` services
- Configure them to use both `default` (hub-net) and the external network
- Allow container name resolution across compose files

**Important Notes:**
- Shell scripts automatically use LF line endings (configured in `.gitattributes`)
- Database initialization runs automatically on LakeFS startup
- Works on both Windows and Linux development environments
- Configuration file (`kohakuhub.conf`) is ignored by git (contains sensitive data)
- Use `--generate-config` to create a template configuration file

---

## Storage Management

### Clear S3 Storage

For demo deployments with constrained S3 storage (like CloudFlare R2 free tier), use these scripts to manage storage.

**All scripts now accept S3 credentials via command-line arguments or environment variables, making them standalone tools independent of KohakuHub configuration.**

#### Python Script (Recommended)

**Features:**
- ✅ Rich progress display with progress bars
- ✅ Detailed statistics (object count, total size)
- ✅ Dry run mode
- ✅ Batch deletion (handles large buckets efficiently)
- ✅ Prefix filtering
- ✅ Error reporting

**Usage:**

```bash
# Clear all content (interactive confirmation required)
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket

# Clear only LFS files (large files)
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket \
    --prefix lfs/

# Clear specific repository type
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket \
    --prefix hf-model-

# Clear multiple prefixes
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket \
    --prefix lfs/ --prefix hf-model-

# Dry run (show what would be deleted without deleting)
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket \
    --dry-run

# Force delete without confirmation (dangerous!)
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket \
    --force

# Use environment variables for credentials
export S3_ENDPOINT=https://s3.amazonaws.com
export S3_ACCESS_KEY=YOUR_ACCESS_KEY
export S3_SECRET_KEY=YOUR_SECRET_KEY
export S3_BUCKET=my-bucket
python scripts/clear_s3_storage.py --prefix lfs/

# Limit number of objects (for testing)
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_ACCESS_KEY \
    --secret-key YOUR_SECRET_KEY \
    --bucket my-bucket \
    --max-objects 100
```

**Requirements:**
- Python 3.10+
- `boto3` and `rich` packages (`pip install boto3 rich`)
- S3 credentials with delete permissions

---

#### Shell Script (Alternative)

**Features:**
- ✅ No Python dependencies (uses AWS CLI or MinIO client)
- ✅ Simple and portable
- ✅ Supports both `aws` CLI and `mc` (MinIO client)

**Usage:**

```bash
# Make script executable
chmod +x scripts/clear_s3_storage.sh

# Clear all content
./scripts/clear_s3_storage.sh

# Clear with prefix
./scripts/clear_s3_storage.sh --prefix lfs/

# Dry run
./scripts/clear_s3_storage.sh --dry-run

# Force delete
./scripts/clear_s3_storage.sh --force
```

**Requirements:**
- AWS CLI (`aws`) or MinIO Client (`mc`)
- KohakuHub environment variables configured

**Install AWS CLI:**
```bash
# Linux/macOS
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Or via package manager
sudo apt install awscli  # Ubuntu/Debian
brew install awscli      # macOS
```

**Install MinIO Client:**
```bash
# Linux
wget https://dl.min.io/client/mc/release/linux-amd64/mc
chmod +x mc
sudo mv mc /usr/local/bin/

# macOS
brew install minio/stable/mc

# Windows
choco install minio-client
```

---

## Common Prefixes in KohakuHub

Understanding KohakuHub's S3 storage structure:

| Prefix | Description | Example |
|--------|-------------|---------|
| `lfs/` | Large File Storage objects (files >5MB) | `lfs/ab/cd/abcd1234...` |
| `hf-model-*` | Model repository data | `hf-model-myuser-mymodel/` |
| `hf-dataset-*` | Dataset repository data | `hf-dataset-myuser-mydataset/` |
| `hf-space-*` | Space repository data | `hf-space-myuser-myspace/` |

**Note:** LFS objects are deduplicated by SHA256 hash. Deleting from `lfs/` prefix removes all large files across all repositories.

---

## Database Migration Scripts

### Add Storage Quota Fields

```bash
# Add quota tracking to users/organizations
python scripts/migrate_add_storage_quota.py
python scripts/migrate_separate_quotas.py
python scripts/migrate_add_quotas_final.py
```

### Update Repository Schema

```bash
python scripts/migrate_repository_schema.py
```

---

## Testing Scripts

### Generate Test Files

```bash
# Generate test files for upload testing
python scripts/generate_test_files.py
```

### Test Authentication

```bash
# Test authentication flow
python scripts/test_auth.py
```

---

## Security

### Generate Secret Keys

```bash
# Generate secure random secret for session/admin tokens
python scripts/generate_secret.py
```

**Output:**
```
Generated secure random secret (64 characters):
a1b2c3d4e5f6...

Add this to your docker-compose.yml:
  KOHAKU_HUB_SESSION_SECRET=a1b2c3d4e5f6...
  KOHAKU_HUB_ADMIN_SECRET_TOKEN=a1b2c3d4e5f6...
```

---

## Environment Variables

### S3 Storage Scripts (`clear_s3_storage.py`, `show_s3_usage.py`)

These scripts accept credentials via command-line arguments or environment variables:

```bash
# Option 1: Command-line arguments
python scripts/clear_s3_storage.py \
    --endpoint https://s3.amazonaws.com \
    --access-key YOUR_KEY \
    --secret-key YOUR_SECRET \
    --bucket my-bucket

# Option 2: Environment variables
export S3_ENDPOINT=https://s3.amazonaws.com
export S3_ACCESS_KEY=YOUR_ACCESS_KEY
export S3_SECRET_KEY=YOUR_SECRET_KEY
export S3_BUCKET=my-bucket
export S3_REGION=us-east-1  # Optional, default: us-east-1

python scripts/clear_s3_storage.py --prefix lfs/
```

### Migration Scripts

Migration scripts require full KohakuHub configuration:

```bash
# S3 Storage
export KOHAKU_HUB_S3_ENDPOINT=http://localhost:29001
export KOHAKU_HUB_S3_PUBLIC_ENDPOINT=http://localhost:29001
export KOHAKU_HUB_S3_ACCESS_KEY=minioadmin
export KOHAKU_HUB_S3_SECRET_KEY=minioadmin
export KOHAKU_HUB_S3_BUCKET=hub-storage

# LakeFS
export KOHAKU_HUB_LAKEFS_ENDPOINT=http://localhost:28000
export KOHAKU_HUB_LAKEFS_ACCESS_KEY=...
export KOHAKU_HUB_LAKEFS_SECRET_KEY=...

# Database
export KOHAKU_HUB_DATABASE_URL=postgresql://user:pass@localhost:5432/kohakuhub

# Application
export KOHAKU_HUB_BASE_URL=http://localhost:28080
```

---

## JSON Schema Generator

### Generate JSON Schema

```bash
python scripts/generate_json_schema.py
```

This script will generate a JSON schema for the KohakuHub types in `__generated__/schemas/<filename>.json`.

Currently supported files:
- `config.json`

---

## Tips for Demo Deployments

### CloudFlare R2 Free Tier

- **Limit:** 10GB storage
- **Strategy:** Regularly clear LFS files and old repository data

```bash
# Weekly cleanup schedule
# 1. Clear old LFS files
python scripts/clear_s3_storage.py --prefix lfs/ --force

# 2. Clear test repositories
python scripts/clear_s3_storage.py --prefix hf-space-test --force
```

### MinIO Development

- **No limits:** MinIO has no storage limits
- **Cleanup:** Only needed for testing or reset

```bash
# Reset entire storage for clean slate
python scripts/clear_s3_storage.py --force
```

---

## Safety Features

All deletion scripts include:

1. **Dry run mode:** See what would be deleted before committing
2. **Interactive confirmation:** Requires explicit "yes" to proceed
3. **Double confirmation:** Extra prompt for full bucket deletion
4. **Progress display:** See deletion progress in real-time
5. **Error reporting:** Track and report any deletion failures

---

## Troubleshooting

### "NoSuchBucket" Error

**Problem:** Bucket doesn't exist

**Solution:**
```bash
# Create bucket using AWS CLI
aws s3 mb "s3://${KOHAKU_HUB_S3_BUCKET}" --endpoint-url="${KOHAKU_HUB_S3_ENDPOINT}"

# Or using MinIO client
mc mb kohakuhub-temp/${KOHAKU_HUB_S3_BUCKET}
```

### "Access Denied" Error

**Problem:** S3 credentials lack delete permissions

**Solution:** Check your S3 access key has `s3:DeleteObject` permission

### Script Hangs

**Problem:** Large bucket (millions of objects)

**Solution:** Use `--max-objects` to test first:
```bash
python scripts/clear_s3_storage.py --max-objects 1000 --dry-run
```

---

## Contributing

When adding new scripts:

1. Add docstring with usage examples
2. Include error handling
3. Support dry-run mode if applicable
4. Update this README
5. Test with both SQLite and PostgreSQL backends
