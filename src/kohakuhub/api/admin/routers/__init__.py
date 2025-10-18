"""Admin routers."""

from kohakuhub.api.admin.routers.commits import router as commits_router
from kohakuhub.api.admin.routers.database import router as database_router
from kohakuhub.api.admin.routers.invitations import router as invitations_router
from kohakuhub.api.admin.routers.quota import router as quota_router
from kohakuhub.api.admin.routers.repositories import router as repositories_router
from kohakuhub.api.admin.routers.search import router as search_router
from kohakuhub.api.admin.routers.stats import router as stats_router
from kohakuhub.api.admin.routers.storage import router as storage_router
from kohakuhub.api.admin.routers.users import router as users_router

__all__ = [
    "commits_router",
    "database_router",
    "invitations_router",
    "quota_router",
    "repositories_router",
    "search_router",
    "stats_router",
    "storage_router",
    "users_router",
]
