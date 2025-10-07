"""Main FastAPI application for Kohaku Hub."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from kohakuhub.api.routers import (
    admin,
    branches,
    commit_history,
    commits,
    files,
    git_http,
    lfs,
    misc,
    quota,
    repo_crud,
    repo_info,
    repo_tree,
    settings,
    ssh_keys,
)
from kohakuhub.api.routers.files import resolve_file_get, resolve_file_head
from kohakuhub.api.utils.s3 import init_storage
from kohakuhub.auth import router as auth_router
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.config import cfg
from kohakuhub.db import Repository, User
from kohakuhub.logger import get_logger
from kohakuhub.org import router as org_router

logger = get_logger("MAIN")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate configuration for production safety
    config_warnings = cfg.validate_production_safety()
    if config_warnings:
        logger.warning("=" * 80)
        logger.warning("CONFIGURATION WARNINGS - Using default/test values:")
        logger.warning("=" * 80)
        for warning in config_warnings:
            logger.warning(f"  - {warning}")
        logger.warning("=" * 80)
        logger.warning(
            "These defaults are fine for development/testing but MUST be changed for production!"
        )
        logger.warning("=" * 80)

    init_storage()
    yield


app = FastAPI(
    title="Kohaku Hub",
    description="HuggingFace-compatible hub with LakeFS and S3 storage",
    version="0.0.1",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=cfg.app.api_base)
app.include_router(repo_crud.router, prefix=cfg.app.api_base, tags=["repositories"])
app.include_router(repo_info.router, prefix=cfg.app.api_base, tags=["repositories"])
app.include_router(repo_tree.router, prefix=cfg.app.api_base, tags=["repositories"])
app.include_router(files.router, prefix=cfg.app.api_base, tags=["files"])
app.include_router(commits.router, prefix=cfg.app.api_base, tags=["commits"])
app.include_router(commit_history.router, prefix=cfg.app.api_base, tags=["commits"])
app.include_router(lfs.router, tags=["lfs"])
app.include_router(branches.router, prefix=cfg.app.api_base, tags=["branches"])
app.include_router(settings.router, prefix=cfg.app.api_base, tags=["settings"])
app.include_router(quota.router, tags=["quota"])
app.include_router(admin.router, prefix="/admin/api", tags=["admin"])
app.include_router(misc.router, prefix=cfg.app.api_base, tags=["utils"])
app.include_router(org_router, prefix="/org", tags=["organizations"])
app.include_router(git_http.router, tags=["git"])
app.include_router(ssh_keys.router, tags=["ssh-keys"])


@app.head("/{namespace}/{name}/resolve/{revision}/{path:path}")
@app.head("/{type}s/{namespace}/{name}/resolve/{revision}/{path:path}")
async def public_resolve_head(
    namespace: str,
    name: str,
    revision: str,
    path: str,
    type: str = "model",
    user: User | None = Depends(get_optional_user),
):
    """Public HEAD endpoint without /api prefix - returns file metadata only."""
    logger.debug(f"HEAD {type}/{namespace}/{name}/resolve/{revision}/{path}")

    repo = Repository.get_or_none(name=name, namespace=namespace, repo_type=type)
    if not repo:
        logger.warning(f"Repository not found: {type}/{namespace}/{name}")
        raise HTTPException(404, detail={"error": "Repository not found"})

    return await resolve_file_head(
        repo_type=type,
        namespace=namespace,
        name=name,
        revision=revision,
        path=path,
        user=user,
    )


@app.get("/{namespace}/{name}/resolve/{revision}/{path:path}")
@app.get("/{type}s/{namespace}/{name}/resolve/{revision}/{path:path}")
async def public_resolve_get(
    namespace: str,
    name: str,
    revision: str,
    path: str,
    type: str = "model",
    user: User | None = Depends(get_optional_user),
):
    """Public GET endpoint without /api prefix - redirects to S3 download."""
    logger.debug(f"GET {type}/{namespace}/{name}/resolve/{revision}/{path}")

    repo = Repository.get_or_none(name=name, namespace=namespace, repo_type=type)
    if not repo:
        logger.warning(f"Repository not found: {type}/{namespace}/{name}")
        raise HTTPException(404, detail={"error": "Repository not found"})

    return await resolve_file_get(
        repo_type=type,
        namespace=namespace,
        name=name,
        revision=revision,
        path=path,
        user=user,
    )


@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "name": "Kohaku Hub",
        "version": "0.0.1",
        "description": "HuggingFace-compatible hub with LakeFS and S3 storage",
        "endpoints": {
            "api": cfg.app.api_base,
            "auth": f"{cfg.app.api_base}/auth",
            "docs": "/docs",
            "redoc": "/redoc",
        },
    }


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
