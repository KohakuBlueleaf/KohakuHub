"""Explicit 501 Not Implemented responses for features KohakuHub does not support.

Why this module exists
----------------------
`huggingface_hub` clients — and, indirectly, every downstream library such as
`gradio` and `datasets` — probe endpoints like ``/discussions`` or
``/spaces/{id}/runtime`` as part of normal flows. If those endpoints do not
exist, FastAPI's default 404 body is generic and the request winds up in
`HfHubHTTPError` without any hint of *why* it failed. That produces confusing
tracebacks and, worse, hides the fact that we intentionally do not support
the feature — a user reading the trace cannot distinguish "KohakuHub bug"
from "KohakuHub scope decision".

This router registers explicit catch-alls for the HuggingFace Hub surfaces
that live in the PR #18 matrix as "Not Supported / Not Claimed":

* Discussions / Pull Requests
* Space runtime management (restart / sleep / hardware / storage / variables / secrets)
* Collections
* Webhooks

Each catch-all returns a HuggingFace-compatible 501 response with:

* ``X-Error-Code: NotImplemented`` — read by ``hf_raise_for_status``
* ``X-Error-Message`` — a specific explanation interpolated into the client's
  ``HfHubHTTPError`` message so users can see *which* feature is missing.

The routes are deliberately registered last in ``main.py`` so they never
shadow real endpoints.
"""

from fastapi import APIRouter, Request

from kohakuhub.api.repo.utils.hf import hf_not_implemented

router = APIRouter()


_DISCUSSION_REASON = (
    "KohakuHub intentionally does not implement the discussions / pull-request "
    "workflow. Use your repo's external issue tracker or contact the maintainer "
    "instead."
)

_SPACE_RUNTIME_REASON = (
    "KohakuHub does not manage Space runtimes, hardware, secrets, or variables "
    "through the hub API. Configure your deployment environment directly."
)

_COLLECTIONS_REASON = "Collections are not implemented by KohakuHub."

_WEBHOOKS_REASON = "Webhooks are not implemented by KohakuHub."


# ---------------------------------------------------------------------------
# Discussions / Pull requests
# ---------------------------------------------------------------------------

@router.api_route(
    "/{repo_type}s/{namespace}/{name}/discussions",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def discussions_list_create(
    repo_type: str, namespace: str, name: str, request: Request
):
    return hf_not_implemented("discussions", _DISCUSSION_REASON)


@router.api_route(
    "/{repo_type}s/{namespace}/{name}/discussions/{path:path}",
    methods=["GET", "POST", "DELETE", "PUT"],
    include_in_schema=False,
)
async def discussions_subpath(
    repo_type: str, namespace: str, name: str, path: str, request: Request
):
    return hf_not_implemented("discussions", _DISCUSSION_REASON)


# ---------------------------------------------------------------------------
# Space runtime management
# ---------------------------------------------------------------------------

@router.api_route(
    "/spaces/{namespace}/{name}/{subresource}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def space_runtime(
    namespace: str, name: str, subresource: str, request: Request
):
    # Only the runtime-management subresources are redirected to 501;
    # anything else falls through (FastAPI matches earlier routes first,
    # so this only fires for unmatched paths under /spaces/*/*).
    runtime_resources = {
        "runtime",
        "restart",
        "sleep",
        "pause",
        "hardware",
        "storage",
        "variables",
        "secrets",
    }
    if subresource in runtime_resources:
        return hf_not_implemented(
            f"Space {subresource}", _SPACE_RUNTIME_REASON
        )
    return hf_not_implemented(
        f"/spaces/{namespace}/{name}/{subresource}",
        _SPACE_RUNTIME_REASON,
    )


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

@router.api_route(
    "/collections",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def collections_root(request: Request):
    return hf_not_implemented("collections", _COLLECTIONS_REASON)


@router.api_route(
    "/collections/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def collections_subpath(path: str, request: Request):
    return hf_not_implemented("collections", _COLLECTIONS_REASON)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

@router.api_route(
    "/settings/webhooks",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def webhooks_root(request: Request):
    return hf_not_implemented("webhooks", _WEBHOOKS_REASON)


@router.api_route(
    "/settings/webhooks/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def webhooks_subpath(path: str, request: Request):
    return hf_not_implemented("webhooks", _WEBHOOKS_REASON)
