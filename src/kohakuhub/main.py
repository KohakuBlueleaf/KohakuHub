"""Main FastAPI application for Kohaku Hub."""

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from kohakuhub.api import (
    admin,
    avatar,
    branches,
    files,
    likes,
    misc,
    settings,
    stats,
    validation,
)
from kohakuhub.api.repo.utils.hf import HFErrorCode
from kohakuhub.api.invitation import router as invitation
from kohakuhub.auth import router as auth_router
from kohakuhub.api.auth import external_tokens
from kohakuhub.config import cfg
from kohakuhub.db import Repository, User
from kohakuhub.db_operations import get_repository
from kohakuhub.logger import get_logger
from kohakuhub.api.commit import history as commit_history
from kohakuhub.api.commit import router as commits
from kohakuhub.api.fallback import with_repo_fallback
from kohakuhub.api.files import resolve_file_get, resolve_file_head
from kohakuhub.api.org import router as org
from kohakuhub.api.quota import router as quota
from kohakuhub.auth.dependencies import get_optional_user
from kohakuhub.utils.s3 import init_storage
from kohakuhub.api.git.routers import http as git_http
from kohakuhub.api.git.routers import lfs, ssh_keys
from kohakuhub.api.repo.routers import crud as repo_crud
from kohakuhub.api.repo.routers import info as repo_info
from kohakuhub.api.repo.routers import tree as repo_tree
from kohakuhub.api.xet.routers import cas as xet_cas
from kohakuhub.api.xet.routers import xet as xet_token
from kohakuhub.api import not_implemented as not_implemented_router

# Conditional import for Dataset Viewer
if not cfg.app.disable_dataset_viewer:
    from kohakuhub.datasetviewer import router as dataset_viewer

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


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach an ``X-Request-Id`` header to every response.

    ``huggingface_hub.HfHubHTTPError`` reads this header (falling back to
    ``X-Amzn-Trace-Id`` / ``X-Amz-Cf-Id``) and embeds the value in the
    exception's ``request_id`` attribute. Surfacing it on every response —
    success and failure alike — gives operators a correlation token for
    debugging across the backend logs and the client traceback.

    If the client already supplied an ``X-Request-Id`` header (e.g. upstream
    gateway, load test rig), we echo it back rather than overwriting.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming or uuid.uuid4().hex
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", request_id)
        return response


app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Needed for the pure-client preview path (safetensors/parquet): the SPA
    # issues cross-origin Range reads against /resolve/ and follows the 302 to
    # MinIO. Without explicit expose_headers, browsers strip everything beyond
    # the "CORS-safelisted" set, hiding Content-Range / X-Linked-* / ETag from
    # JS. Keep in sync with the header set emitted by _get_file_metadata in
    # src/kohakuhub/api/files.py.
    expose_headers=[
        "Accept-Ranges",
        "Content-Range",
        "Content-Length",
        "ETag",
        "Location",
        "X-Repo-Commit",
        "X-Linked-Etag",
        "X-Linked-Size",
        "X-Xet-Hash",
    ],
)

app.include_router(auth_router, prefix=cfg.app.api_base)
app.include_router(external_tokens.router, prefix=cfg.app.api_base, tags=["auth"])
app.include_router(repo_crud.router, prefix=cfg.app.api_base, tags=["repositories"])
app.include_router(repo_info.router, prefix=cfg.app.api_base, tags=["repositories"])
app.include_router(repo_tree.router, prefix=cfg.app.api_base, tags=["repositories"])
app.include_router(files.router, prefix=cfg.app.api_base, tags=["files"])
app.include_router(commits, prefix=cfg.app.api_base, tags=["commits"])
app.include_router(commit_history.router, prefix=cfg.app.api_base, tags=["commits"])
app.include_router(lfs.router, tags=["lfs"])
app.include_router(branches.router, prefix=cfg.app.api_base, tags=["branches"])
app.include_router(settings.router, prefix=cfg.app.api_base, tags=["settings"])
app.include_router(avatar.router, prefix=cfg.app.api_base, tags=["avatars"])
app.include_router(likes.router, prefix=cfg.app.api_base, tags=["likes"])
app.include_router(stats.router, prefix=cfg.app.api_base, tags=["stats"])
app.include_router(invitation, prefix=cfg.app.api_base, tags=["invitations"])
app.include_router(quota, tags=["quota"])
app.include_router(admin.router, prefix="/admin/api", tags=["admin"])
app.include_router(misc.router, prefix=cfg.app.api_base, tags=["utils"])
app.include_router(org, prefix="/org", tags=["organizations"])
app.include_router(git_http.router, tags=["git"])
app.include_router(ssh_keys.router, tags=["ssh-keys"])
app.include_router(validation.router, tags=["validation"])
app.include_router(xet_token.router, prefix=cfg.app.api_base, tags=["xet"])
app.include_router(xet_cas.router, tags=["xet-cas"])

# Conditional: Dataset Viewer (Kohaku License)
if not cfg.app.disable_dataset_viewer:
    app.include_router(dataset_viewer.router, prefix=cfg.app.api_base)
    logger.info("Dataset Viewer enabled (Kohaku Software License 1.0)")
else:
    logger.info("Dataset Viewer disabled (AGPL-3 only build)")

# Registered last so catch-all 501 routes for won't-support features
# (discussions, space runtime, collections, webhooks) never shadow the
# real endpoints defined above.
app.include_router(
    not_implemented_router.router,
    prefix=cfg.app.api_base,
    tags=["not-implemented"],
)


@app.head("/{namespace}/{name}/resolve/{revision}/{path:path}")
@app.head("/{type}s/{namespace}/{name}/resolve/{revision}/{path:path}")
@with_repo_fallback("resolve")
async def public_resolve_head(
    namespace: str,
    name: str,
    revision: str,
    path: str,
    request: Request,
    type: str = "model",
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Public HEAD endpoint without /api prefix - returns file metadata only."""
    logger.debug(f"HEAD {type}/{namespace}/{name}/resolve/{revision}/{path}")

    repo = get_repository(type, namespace, name)
    if not repo:
        logger.warning(f"Repository not found: {type}/{namespace}/{name}")
        # Public resolve is on the critical `huggingface_hub` download path;
        # emit the HF-compatible headers so the client raises
        # `RepositoryNotFoundError` with a useful server_message instead of
        # a generic HfHubHTTPError — transformers / datasets / etc. all
        # special-case the named exception.
        raise HTTPException(
            status_code=404,
            detail={"error": f"Repository '{namespace}/{name}' not found"},
            headers={
                "X-Error-Code": HFErrorCode.REPO_NOT_FOUND,
                "X-Error-Message": f"Repository '{namespace}/{name}' ({type}) not found",
            },
        )

    return await resolve_file_head(
        repo_type=type,
        namespace=namespace,
        name=name,
        revision=revision,
        path=path,
        request=request,
        user=user,
    )


@app.get("/{namespace}/{name}/resolve/{revision}/{path:path}")
@app.get("/{type}s/{namespace}/{name}/resolve/{revision}/{path:path}")
@with_repo_fallback("resolve")
async def public_resolve_get(
    namespace: str,
    name: str,
    revision: str,
    path: str,
    request: Request,
    type: str = "model",
    fallback: bool = True,
    user: User | None = Depends(get_optional_user),
):
    """Public GET endpoint without /api prefix - redirects to S3 download."""
    logger.debug(f"GET {type}/{namespace}/{name}/resolve/{revision}/{path}")

    repo = get_repository(type, namespace, name)
    if not repo:
        logger.warning(f"Repository not found: {type}/{namespace}/{name}")
        # Public resolve is on the critical `huggingface_hub` download path;
        # emit the HF-compatible headers so the client raises
        # `RepositoryNotFoundError` with a useful server_message instead of
        # a generic HfHubHTTPError — transformers / datasets / etc. all
        # special-case the named exception.
        raise HTTPException(
            status_code=404,
            detail={"error": f"Repository '{namespace}/{name}' not found"},
            headers={
                "X-Error-Code": HFErrorCode.REPO_NOT_FOUND,
                "X-Error-Message": f"Repository '{namespace}/{name}' ({type}) not found",
            },
        )

    return await resolve_file_get(
        repo_type=type,
        namespace=namespace,
        name=name,
        revision=revision,
        path=path,
        request=request,
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
