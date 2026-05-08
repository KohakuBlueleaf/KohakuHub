"""Admin API - Requires admin secret token authentication.

Organized router structure:
- users: User management (CRUD, verification)
- repositories: Repository management and file browsing
- quota: Quota management and recalculation
- stats: System statistics and analytics
- storage: S3 bucket and object browsing
- commits: Commit history
- invitations: Invitation management
- search: Global search across entities
- health: Live probes for backing services (Postgres, MinIO, LakeFS, SMTP)
- cache: L2 cache (Valkey) hit/miss/error counters and memory state
"""

from fastapi import APIRouter

from kohakuhub.api.admin.routers import (
    cache_router,
    commits_router,
    credentials_router,
    database_router,
    fallback_router,
    health_router,
    invitations_router,
    quota_router,
    repositories_router,
    search_router,
    stats_router,
    storage_router,
    users_router,
)

# Create main admin router
router = APIRouter()

# Include all sub-routers
router.include_router(users_router, tags=["admin-users"])
router.include_router(repositories_router, tags=["admin-repositories"])
router.include_router(quota_router, tags=["admin-quota"])
router.include_router(stats_router, tags=["admin-stats"])
router.include_router(storage_router, tags=["admin-storage"])
router.include_router(commits_router, tags=["admin-commits"])
router.include_router(invitations_router, tags=["admin-invitations"])
router.include_router(search_router, tags=["admin-search"])
router.include_router(database_router, tags=["admin-database"])
router.include_router(fallback_router, tags=["admin-fallback"])
router.include_router(health_router, tags=["admin-health"])
router.include_router(credentials_router, tags=["admin-credentials"])
router.include_router(cache_router, tags=["admin-cache"])

__all__ = ["router"]
