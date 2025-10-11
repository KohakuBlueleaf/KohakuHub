# Admin Portal Guide

*Complete guide to KohakuHub's administration interface*

**Last Updated:** January 2025
**Access:** http://your-hub.com/admin

---

## Admin Portal Architecture

```mermaid
graph TD
    A[Admin] -- "X-Admin-Token" --> B[Admin Portal]
    B -- "REST API" --> C[Admin Endpoints]
    C -- "Queries" --> D[Database]
    C -- "S3 API" --> E[MinIO]
    C -- "REST" --> F[LakeFS]
```

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Dashboard](#dashboard)
4. [User Management](#user-management)
5. [Repository Management](#repository-management)
6. [Commit History Viewer](#commit-history-viewer)
7. [S3 Storage Browser](#s3-storage-browser)
8. [Quota Management](#quota-management)
9. [API Reference](#api-reference)
10. [Security Best Practices](#security-best-practices)

---

## Overview

The Admin Portal provides a centralized interface for managing your KohakuHub instance. It offers:

- **User Management** - Create, view, and delete users
- **Repository Browser** - View all repositories with statistics
- **Commit History** - Track commits across all repositories
- **Storage Browser** - Browse S3 buckets and objects
- **Quota Management** - Set and monitor storage quotas
- **Statistics Dashboard** - Real-time insights into usage

**Access URL:**
```
http://your-hub.com/admin
```

---

## Authentication

### Admin Token

The admin portal requires a secret token configured in your environment:

**Configuration:**
```yaml
# docker-compose.yml
environment:
  KOHAKU_HUB_ADMIN_ENABLED: "true"
  KOHAKU_HUB_ADMIN_SECRET_TOKEN: "your-secret-token-here"  # CHANGE THIS!
```

**Security:**
- ⚠️ **NEVER** use default token `"change-me-in-production"` in production
- ✅ Generate strong random token: `openssl rand -hex 32`
- ✅ Store securely (environment variable, secrets manager)
- ✅ Rotate regularly
- ✅ Use HTTPS in production

### Login

1. Navigate to `/admin`
2. Enter your admin secret token
3. Token is stored in browser session (not localStorage for security)
4. Auto-logout on browser close

**Example:**
```bash
# Generate secure token
openssl rand -hex 32
# Output: a1b2c3d4e5f6...

# Add to docker-compose.yml
KOHAKU_HUB_ADMIN_SECRET_TOKEN: "a1b2c3d4e5f6..."

# Restart
docker-compose up -d
```

---

## Dashboard

### Overview Statistics

The dashboard shows real-time statistics from your database:

**User Stats:**
- Total users
- Active users
- Email verified users
- Inactive users

**Repository Stats:**
- Total repositories
- Private vs public repositories
- Breakdown by type (models, datasets, spaces)

**Commit Stats:**
- Total commits
- Top contributors (by commit count)

**Storage Stats:**
- Total storage used
- Private vs public storage
- LFS object count and size

**Quick Actions:**
- Navigate to user management
- Browse repositories
- View commits
- Inspect S3 storage
- Manage quotas

---

## User Management

### List Users

**Features:**
- View all users with pagination
- Sort by ID, username, storage usage
- Filter and search
- Storage quota visualization

**Columns:**
- ID, Username, Email
- Private storage (used/quota)
- Public storage (used/quota)
- Total storage
- Email verification status
- Active status
- Created date

### Create User

**Fields:**
- Username (required, unique)
- Email (required, unique)
- Password (required)
- Email verified (checkbox)
- Private quota (bytes, optional = unlimited)
- Public quota (bytes, optional = unlimited)

**Example:**
```
Username: alice
Email: alice@example.com
Password: ********
Email Verified: ✓
Private Quota: 10737418240  (10 GB)
Public Quota: 53687091200   (50 GB)
```

### View User Details

Click "View" to see:
- User ID, username, email
- Verification and active status
- Storage quotas (private, public)
- Storage used (private, public)
- Created date

**Actions:**
- Manage Quota (navigate to quota page)

### Delete User

**Normal Delete:**
- Deletes user account
- Deletes all sessions and tokens
- Deletes organization memberships
- **Keeps** repositories (must delete separately)

**Force Delete:**
- Deletes everything above
- **Also deletes** all owned repositories
- ⚠️ Cannot be undone!

**Workflow:**
1. Click "Delete" → Confirmation dialog
2. If user owns repos → Shows repo list
3. Choose: Cancel or Force Delete
4. Confirm force delete → All data deleted

### Toggle Email Verification

**Use case:** Manually verify users when email verification is disabled or failed.

**Action:** Click "Verify" or "Unverify" button → Instant update

---

## Repository Management

### List Repositories

**Filters:**
- Repository type (model/dataset/space)
- Namespace (user or organization)

**Columns:**
- ID
- Type (color-coded badge)
- Full repository ID (namespace/name)
- Privacy status (Private/Public badge)
- Owner username
- Created date

**Actions:**
- View Details → Opens detailed dialog

### Repository Details

**Information:**
- ID, Type, Full ID
- Namespace, Name
- Owner username
- Privacy status
- Created date
- **File count** (from database)
- **Commit count** (from database)
- **Total size** (sum of all files)

**Actions:**
- View in Main App → Opens repository in main UI

### API Endpoints

```
GET /admin/api/repositories
  Query: repo_type, namespace, limit, offset

GET /admin/api/repositories/{type}/{namespace}/{name}
  Returns: Detailed repo info with stats
```

---

## Commit History Viewer

### Overview

View all commits across all repositories in your instance.

**Filters:**
- Repository ID (e.g., "org/repo-name")
- Author username

**Columns:**
- Commit ID (first 8 chars)
- Repository (type badge + full ID)
- Branch
- Author
- Message (truncated, hover for full)
- Created date

**Sorting:**
- Sort by ID, created date, username, repository

**Pagination:**
- Page size: 10, 20, 50, 100
- Navigate through pages

### Use Cases

- Track user activity
- Find specific commits
- Monitor repository changes
- Debug commit issues
- Audit trail

### API Endpoint

```
GET /admin/api/commits
  Query: repo_full_id, username, limit, offset
```

---

## S3 Storage Browser

### Bucket List

**Overview:**
- View all S3 buckets
- Total size and object count
- Visual progress bars
- Creation dates

**Metrics:**
- Bucket name
- Total size (formatted: KB, MB, GB)
- Object count
- Creation date
- Progress bar (relative to 100GB)

**Actions:**
- Click bucket → Browse contents

### Object Browser

**Features:**
- List objects in selected bucket
- Filter by prefix (e.g., "lfs/", "models/")
- Pagination (up to 1000 objects)

**Columns:**
- Key (full S3 path)
- Size
- Storage class (STANDARD, etc.)
- Last modified date

**Prefix Filtering:**
```
Enter prefix: lfs/
→ Shows only objects starting with "lfs/"

Enter prefix: hf-model-org-repo/
→ Shows objects for specific repository
```

### API Endpoints

```
GET /admin/api/storage/buckets
  Returns: All buckets with sizes

GET /admin/api/storage/objects/{bucket}
  Query: prefix, limit
  Returns: Objects in bucket
```

---

## Quota Management

### View Quota

**Per-user or per-organization:**
- Private quota (limit)
- Private used
- Public quota (limit)
- Public used
- Total usage
- Usage percentages

### Set Quota

**Fields:**
- Private quota bytes (null = unlimited)
- Public quota bytes (null = unlimited)

**Examples:**
```
10 GB = 10737418240 bytes
50 GB = 53687091200 bytes
Unlimited = (empty/null)
```

### Recalculate Storage

**Purpose:** Re-scan all files and update storage usage.

**When to use:**
- Database out of sync
- After manual S3 operations
- Quota shows incorrect values

**Process:**
1. Scans all LakeFS objects for namespace
2. Sums file sizes
3. Updates User/Organization table

### API Endpoints

```
GET /admin/api/quota/{namespace}?is_org=false
  Returns: Quota information

PUT /admin/api/quota/{namespace}
  Body: {private_quota_bytes, public_quota_bytes}
  Returns: Updated quota

POST /admin/api/quota/{namespace}/recalculate
  Returns: Recalculated usage
```

---

## API Reference

### Authentication

All admin API endpoints require `X-Admin-Token` header:

```bash
curl -H "X-Admin-Token: your-secret-token" \
  http://localhost:48888/admin/api/stats
```

### Endpoints Overview

**User Management:**
```
GET    /admin/api/users                    # List users
GET    /admin/api/users/{username}         # Get user info
POST   /admin/api/users                    # Create user
DELETE /admin/api/users/{username}         # Delete user
PATCH  /admin/api/users/{username}/email-verification  # Set verification
```

**Repository Management:**
```
GET /admin/api/repositories                # List repositories
GET /admin/api/repositories/{type}/{namespace}/{name}  # Get details
```

**Commit History:**
```
GET /admin/api/commits                     # List commits
```

**Storage:**
```
GET /admin/api/storage/buckets             # List buckets
GET /admin/api/storage/objects/{bucket}    # List objects
```

**Statistics:**
```
GET /admin/api/stats                       # Basic stats
GET /admin/api/stats/detailed              # Detailed stats
GET /admin/api/stats/timeseries?days=30    # Time-series data
GET /admin/api/stats/top-repos?by=commits  # Top repositories
```

**Quota:**
```
GET  /admin/api/quota/{namespace}          # Get quota
PUT  /admin/api/quota/{namespace}          # Set quota
POST /admin/api/quota/{namespace}/recalculate  # Recalculate
```

### Response Formats

**User Info:**
```json
{
  "id": 1,
  "username": "alice",
  "email": "alice@example.com",
  "email_verified": true,
  "is_active": true,
  "private_quota_bytes": 10737418240,
  "public_quota_bytes": 53687091200,
  "private_used_bytes": 1234567,
  "public_used_bytes": 9876543,
  "created_at": "2025-01-01T00:00:00.000000Z"
}
```

**Repository Info:**
```json
{
  "id": 42,
  "repo_type": "model",
  "namespace": "org",
  "name": "my-model",
  "full_id": "org/my-model",
  "private": false,
  "owner_id": 1,
  "owner_username": "alice",
  "created_at": "2025-01-01T00:00:00.000000Z",
  "file_count": 15,
  "commit_count": 8,
  "total_size": 12345678
}
```

**Detailed Stats:**
```json
{
  "users": {
    "total": 100,
    "active": 95,
    "verified": 80,
    "inactive": 5
  },
  "organizations": {
    "total": 10
  },
  "repositories": {
    "total": 250,
    "private": 100,
    "public": 150,
    "by_type": {
      "model": 180,
      "dataset": 60,
      "space": 10
    }
  },
  "commits": {
    "total": 1500,
    "top_contributors": [
      {"username": "alice", "commit_count": 150},
      {"username": "bob", "commit_count": 120}
    ]
  },
  "lfs": {
    "total_objects": 500,
    "total_size": 107374182400
  },
  "storage": {
    "private_used": 10737418240,
    "public_used": 53687091200,
    "total_used": 64424509440
  }
}
```

---

## Security Best Practices

### Token Management

**DO:**
- ✅ Generate cryptographically random tokens
- ✅ Use environment variables (never hardcode)
- ✅ Rotate tokens regularly (monthly)
- ✅ Use HTTPS in production
- ✅ Restrict admin portal access (firewall, VPN)

**DON'T:**
- ❌ Use default token in production
- ❌ Commit tokens to git
- ❌ Share tokens via insecure channels
- ❌ Use same token across environments
- ❌ Store tokens in browser localStorage

### Token Rotation

```bash
# 1. Generate new token
NEW_TOKEN=$(openssl rand -hex 32)

# 2. Update docker-compose.yml
KOHAKU_HUB_ADMIN_SECRET_TOKEN: "$NEW_TOKEN"

# 3. Restart services
docker-compose up -d

# 4. Update saved tokens in admin portal sessions
```

### Network Security

**Production Deployment:**

```nginx
# Restrict admin portal to specific IPs
location /admin {
    allow 192.168.1.0/24;  # Internal network
    allow 10.0.0.0/8;      # VPN
    deny all;

    # ... rest of config
}
```

**Alternative: Basic Auth Layer**

```nginx
location /admin/api/ {
    auth_basic "Admin Area";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # Then require X-Admin-Token header
    proxy_pass http://hub-api:48888;
}
```

### Audit Logging

Admin operations are logged with `[ADMIN]` prefix:

```
[WARNING] [ADMIN] [07:05:55] Admin deleted user: testuser (deleted 5 repositories)
[INFO] [ADMIN] [07:06:12] Admin set quota for user alice: private=10737418240, public=53687091200
[WARNING] [ADMIN] [07:06:45] Admin deleted repository: model:org/test-model
```

**Monitor logs:**
```bash
docker logs khub-hub-api | grep "\[ADMIN\]"
```

---

## Use Cases

### Scenario 1: New User Onboarding

```
1. Dashboard → Quick Actions → "Manage Users"
2. Click "Create User"
3. Fill form:
   - Username: newuser
   - Email: newuser@company.com
   - Password: (generate secure password)
   - Email Verified: ✓
   - Quotas: 10GB private, 50GB public
4. Click "Create User"
5. Share credentials with user
```

### Scenario 2: Storage Cleanup

```
1. Dashboard → "Browse Storage"
2. Click on "hub-storage" bucket
3. Filter by prefix: "lfs/"
4. Review large objects
5. Identify unused LFS objects
6. (Manually delete via CLI/API if needed)
```

### Scenario 3: User Investigation

```
1. Dashboard → "View Commits"
2. Filter by username: "suspicious-user"
3. Review commit activity
4. Click repository links to inspect content
5. If needed: Go to Users → Delete user (with force)
```

### Scenario 4: Quota Enforcement

```
1. Dashboard → "Manage Quotas"
2. Select namespace (user or org)
3. View current usage
4. Set new limits if exceeded
5. Click "Recalculate" to verify
6. Monitor dashboard for compliance
```

---

## Troubleshooting

### Can't Login

**Problem:** Invalid admin token
**Solution:** Check `KOHAKU_HUB_ADMIN_SECRET_TOKEN` in docker-compose.yml matches your input

---

**Problem:** "Admin API is disabled"
**Solution:** Set `KOHAKU_HUB_ADMIN_ENABLED=true` in environment

---

### Statistics Not Updating

**Problem:** Stale data
**Solution:** Click "Refresh Stats" button on dashboard

---

### Storage Size Incorrect

**Problem:** Database out of sync with S3
**Solution:** Use "Recalculate" button in Quota Management

---

### Can't Delete User

**Problem:** User owns repositories
**Solution:** Either delete repos first, or use "Force Delete" option

---

## Advanced Features

### Time-Series Statistics

**API:**
```bash
curl -H "X-Admin-Token: your-token" \
  "http://localhost:48888/admin/api/stats/timeseries?days=30"
```

**Returns:**
```json
{
  "repositories_by_day": {
    "2025-01-01": {"model": 5, "dataset": 2, "space": 0},
    "2025-01-02": {"model": 3, "dataset": 1, "space": 1}
  },
  "commits_by_day": {
    "2025-01-01": 15,
    "2025-01-02": 20
  },
  "users_by_day": {
    "2025-01-01": 2,
    "2025-01-02": 1
  }
}
```

**Use case:** Build custom dashboards with charts

### Top Repositories

**By Commits:**
```bash
curl -H "X-Admin-Token: your-token" \
  "http://localhost:48888/admin/api/stats/top-repos?by=commits&limit=10"
```

**By Size:**
```bash
curl -H "X-Admin-Token: your-token" \
  "http://localhost:48888/admin/api/stats/top-repos?by=size&limit=10"
```

---

## Integration with CI/CD

### Automated User Creation

```python
import requests

admin_token = "your-admin-token"
base_url = "http://hub.example.com"

# Create user via API
response = requests.post(
    f"{base_url}/admin/api/users",
    headers={"X-Admin-Token": admin_token},
    json={
        "username": "ci-bot",
        "email": "ci@company.com",
        "password": "generated-password",
        "email_verified": True,
        "private_quota_bytes": 107374182400,  # 100 GB
        "public_quota_bytes": None,  # Unlimited
    }
)

user = response.json()
print(f"Created user: {user['username']} (ID: {user['id']})")
```

### Monitoring Script

```python
import requests

admin_token = "your-admin-token"

# Get statistics
response = requests.get(
    "http://hub.example.com/admin/api/stats/detailed",
    headers={"X-Admin-Token": admin_token}
)

stats = response.json()

# Alert if storage > 80%
total_used = stats['storage']['total_used']
if total_used > 0.8 * (100 * 1024 * 1024 * 1024):  # 80GB
    print("WARNING: Storage usage high!")

# Alert if too many inactive users
if stats['users']['inactive'] > 10:
    print(f"WARNING: {stats['users']['inactive']} inactive users")
```

---

## Performance Considerations

### Database Queries

Admin operations run synchronous queries in the DB thread pool:
- User listings: `O(n)` where n = total users
- Repository stats: Aggregation queries
- Commit history: Indexed by repo_full_id and username

**Optimization:**
- Limit page size (default: 20, max: 100)
- Use filters to reduce result sets
- Statistics are computed on-demand (cache in frontend if needed)

### S3 Bucket Scanning

**Warning:** Scanning large buckets is slow!

```python
# For bucket with 100,000 objects:
# - Scan time: 30-60 seconds
# - Uses pagination (1000 objects per request)
```

**Recommendation:**
- Limit to specific prefixes when possible
- Don't scan too frequently
- Consider caching results for large buckets

---

## Comparison: Admin Portal vs CLI

| Feature | Admin Portal | kohub-cli | Best For |
|---------|--------------|-----------|----------|
| User management | ✅ GUI | ✅ Commands | GUI: Quick actions<br>CLI: Automation |
| Repository browser | ✅ Full | ⚠️ Limited | Portal: Overview<br>CLI: Specific repos |
| Commit history | ✅ Full | ❌ No | Portal only |
| Storage browser | ✅ Full | ❌ No | Portal only |
| Quota management | ✅ Full | ⚠️ API only | Portal: Visual<br>CLI: Scripting |
| Statistics | ✅ Dashboard | ❌ No | Portal only |
| Automation | ❌ Manual | ✅ Scripts | Portal: Manual<br>CLI: Automation |

**Recommendation:** Use portal for exploration/monitoring, CLI for automation.

---

## Frequently Asked Questions

**Q: Can I disable the admin portal?**
A: Yes, set `KOHAKU_HUB_ADMIN_ENABLED=false`

**Q: Is the admin token different from user tokens?**
A: Yes, admin token is system-wide. User tokens are per-user.

**Q: Can I create multiple admin users?**
A: No, admin portal uses shared secret token. For user-based admin, implement role system.

**Q: Does deleting a user delete their repositories?**
A: No (unless force delete). Repositories can be transferred to another user.

**Q: Can I access admin API without the portal UI?**
A: Yes, use curl/Python with `X-Admin-Token` header.

**Q: Is audit logging enabled by default?**
A: Yes, all admin operations are logged with `[ADMIN]` prefix.

---

**Last Updated:** January 2025
**Version:** 1.0
**Status:** ✅ Production Ready
