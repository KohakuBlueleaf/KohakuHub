"""Admin routers."""

from kohakuhub.api.admin.routers.cache import router as cache_router
from kohakuhub.api.admin.routers.commits import router as commits_router
from kohakuhub.api.admin.routers.credentials import router as credentials_router
from kohakuhub.api.admin.routers.database import router as database_router
from kohakuhub.api.admin.routers.fallback import router as fallback_router
from kohakuhub.api.admin.routers.health import router as health_router
from kohakuhub.api.admin.routers.invitations import router as invitations_router
from kohakuhub.api.admin.routers.quota import router as quota_router
from kohakuhub.api.admin.routers.repositories import router as repositories_router
from kohakuhub.api.admin.routers.search import router as search_router
from kohakuhub.api.admin.routers.stats import router as stats_router
from kohakuhub.api.admin.routers.storage import router as storage_router
from kohakuhub.api.admin.routers.users import router as users_router

__all__ = [
    "cache_router",
    "commits_router",
    "credentials_router",
    "database_router",
    "fallback_router",
    "health_router",
    "invitations_router",
    "quota_router",
    "repositories_router",
    "search_router",
    "stats_router",
    "storage_router",
    "users_router",
]
