"""HuggingFace Hub API compatibility utilities.

This module provides utilities for making Kohaku Hub compatible with
`huggingface_hub` client behavior.
"""

from typing import Optional

from fastapi.responses import Response


class HFErrorCode:
    """HuggingFace error codes for X-Error-Code header.

    These error codes are read by huggingface_hub client's hf_raise_for_status()
    function to provide specific error types.

    HuggingFace Hub officially supports:
    - RepoNotFound
    - RevisionNotFound
    - EntryNotFound
    - GatedRepo

    We add additional codes for KohakuHub-specific errors that don't map to HF codes.

    Reference: huggingface_hub/utils/_http.py
    """

    # HuggingFace official error codes (DO NOT CHANGE)
    REPO_NOT_FOUND = "RepoNotFound"
    REVISION_NOT_FOUND = "RevisionNotFound"
    ENTRY_NOT_FOUND = "EntryNotFound"
    GATED_REPO = "GatedRepo"

    # KohakuHub custom error codes (not in official HF Hub)
    REPO_EXISTS = "RepoExists"
    BAD_REQUEST = "BadRequest"
    INVALID_REPO_TYPE = "InvalidRepoType"
    INVALID_REPO_ID = "InvalidRepoId"
    SERVER_ERROR = "ServerError"


def _sanitize_header_value(value: str) -> str:
    """Normalize header values so HTTP servers can emit them safely."""
    return " ".join(str(value).split())


def hf_error_response(
    status_code: int,
    error_code: str,
    message: str,
    headers: Optional[dict] = None,
) -> Response:
    """Create HuggingFace-compatible error response.

    HuggingFace client reads error information from HTTP headers, not from response body:
    - X-Error-Code: Specific error code (see HFErrorCode class)
    - X-Error-Message: Human-readable error message

    The response body should be empty. The client's hf_raise_for_status() function
    parses these headers to throw appropriate exceptions like:
    - RepositoryNotFoundError
    - RevisionNotFoundError
    - GatedRepoError
    - EntryNotFoundError
    - etc.

    Args:
        status_code: HTTP status code (404, 403, 400, 500, etc.)
        error_code: HuggingFace error code (use HFErrorCode constants)
        message: Human-readable error message
        headers: Additional headers to include

    Returns:
        Response with proper error headers and empty body

    Examples:
        >>> # Repository not found (404)
        >>> return hf_error_response(
        ...     404,
        ...     HFErrorCode.REPO_NOT_FOUND,
        ...     "Repository 'owner/repo' not found"
        ... )

        >>> # Gated repository (403)
        >>> return hf_error_response(
        ...     403,
        ...     HFErrorCode.GATED_REPO,
        ...     "You need to accept terms to access this repository"
        ... )

        >>> # Revision not found (404)
        >>> return hf_error_response(
        ...     404,
        ...     HFErrorCode.REVISION_NOT_FOUND,
        ...     "Revision 'v1.0' not found"
        ... )
    """
    response_headers = {
        "X-Error-Code": error_code,
        "X-Error-Message": _sanitize_header_value(message),
    }
    if headers:
        response_headers.update(
            {key: _sanitize_header_value(value) for key, value in headers.items()}
        )

    # Return empty body with error in headers
    # HuggingFace client reads from headers, not body
    return Response(
        status_code=status_code,
        headers=response_headers,
    )


def hf_repo_not_found(repo_id: str, repo_type: Optional[str] = None) -> Response:
    """Shortcut for repository not found error (404).

    Args:
        repo_id: Repository ID (e.g., "owner/repo")
        repo_type: Optional repository type for more specific message

    Returns:
        404 response with RepoNotFound error code
    """
    type_str = f" ({repo_type})" if repo_type else ""
    return hf_error_response(
        404,
        HFErrorCode.REPO_NOT_FOUND,
        f"Repository '{repo_id}'{type_str} not found",
    )


def hf_gated_repo(repo_id: str, message: Optional[str] = None) -> Response:
    """Shortcut for gated repository error (403).

    Args:
        repo_id: Repository ID
        message: Optional custom message

    Returns:
        403 response with GatedRepo error code
    """
    if message is None:
        message = (
            f"Repository '{repo_id}' is gated. "
            "You need to accept the terms to access it."
        )

    return hf_error_response(
        403,
        HFErrorCode.GATED_REPO,
        message,
    )


def hf_revision_not_found(
    repo_id: str,
    revision: str,
) -> Response:
    """Shortcut for revision not found error (404).

    Args:
        repo_id: Repository ID
        revision: Revision/branch name that was not found

    Returns:
        404 response with RevisionNotFound error code
    """
    return hf_error_response(
        404,
        HFErrorCode.REVISION_NOT_FOUND,
        f"Revision '{revision}' not found in repository '{repo_id}'",
    )


def hf_entry_not_found(
    repo_id: str,
    path: str,
    revision: Optional[str] = None,
) -> Response:
    """Shortcut for file/entry not found error (404).

    Args:
        repo_id: Repository ID
        path: File path that was not found
        revision: Optional revision/branch name

    Returns:
        404 response with EntryNotFound error code
    """
    revision_str = f" at revision '{revision}'" if revision else ""
    return hf_error_response(
        404,
        HFErrorCode.ENTRY_NOT_FOUND,
        f"Entry '{path}' not found in repository '{repo_id}'{revision_str}",
    )


def hf_bad_request(message: str) -> Response:
    """Shortcut for bad request error (400).

    Args:
        message: Error message

    Returns:
        400 response with BadRequest error code
    """
    return hf_error_response(
        400,
        HFErrorCode.BAD_REQUEST,
        message,
    )


def hf_server_error(message: str, error_code: Optional[str] = None) -> Response:
    """Shortcut for server error (500).

    Args:
        message: Error message
        error_code: Optional custom error code (defaults to ServerError)

    Returns:
        500 response with ServerError error code
    """
    return hf_error_response(
        500,
        error_code or HFErrorCode.SERVER_ERROR,
        message,
    )


async def collect_hf_siblings(
    repo_row,
    repo_type: str,
    repo_id: str,
    revision: str,
) -> list[dict]:
    """Collect repository files using the schema expected by `huggingface_hub`."""
    from kohakuhub.db_operations import get_file, should_use_lfs
    from kohakuhub.utils.lakefs import get_lakefs_client, lakefs_repo_name

    lakefs_repo = lakefs_repo_name(repo_type, repo_id)
    client = get_lakefs_client()
    all_results = []
    after = ""

    while True:
        result = await client.list_objects(
            repository=lakefs_repo,
            ref=revision,
            prefix="",
            delimiter="",
            amount=1000,
            after=after,
        )

        if isinstance(result, list):
            all_results.extend(result)
            break

        all_results.extend(result.get("results", []))
        pagination = result.get("pagination", {})
        if not pagination.get("has_more"):
            break

        after = pagination.get("next_offset")
        if not after:
            break

    file_objects = [obj for obj in all_results if obj.get("path_type") == "object"]
    file_records = {}

    for obj in file_objects:
        path = obj["path"]
        size = obj.get("size_bytes", 0)
        if not should_use_lfs(repo_row, path, size):
            continue

        try:
            record = get_file(repo_row, path)
        except Exception:
            record = None

        if record is not None:
            file_records[path] = record

    siblings = []
    for obj in file_objects:
        path = obj["path"]
        size = obj.get("size_bytes", 0)
        sibling = {
            "rfilename": path,
            "size": size,
        }

        if should_use_lfs(repo_row, path, size):
            file_record = file_records.get(path)
            checksum = (
                file_record.sha256
                if file_record is not None and file_record.sha256
                else obj.get("checksum", "")
            )
            sibling["lfs"] = {
                "sha256": checksum,
                "size": size,
                "pointerSize": 134,
            }

        siblings.append(sibling)

    return siblings


def format_hf_datetime(dt) -> Optional[str]:
    """Format datetime for HuggingFace API responses.

    Handles both datetime objects and string timestamps from database.

    Args:
        dt: datetime object, string timestamp, or None

    Returns:
        ISO format datetime string with milliseconds or None

    Example:
        >>> from datetime import datetime
        >>> dt = datetime(2025, 1, 15, 10, 30, 45)
        >>> format_hf_datetime(dt)
        '2025-01-15T10:30:45.000000Z'
    """
    if dt is None:
        return None

    # Import here to avoid circular dependency
    from kohakuhub.utils.datetime_utils import safe_strftime

    # HuggingFace format: "2025-01-15T10:30:45.123456Z"
    return safe_strftime(dt, "%Y-%m-%dT%H:%M:%S.%fZ")


def is_lakefs_not_found_error(error: Exception) -> bool:
    """Check if an exception is a LakeFS not found error.

    Args:
        error: Exception to check

    Returns:
        True if the error indicates a 404/not found condition
    """
    error_str = str(error).lower()
    return "404" in error_str or "not found" in error_str


def is_lakefs_revision_error(error: Exception) -> bool:
    """Check if an exception is a LakeFS revision/branch error.

    Args:
        error: Exception to check

    Returns:
        True if the error is related to revision/branch
    """
    error_str = str(error).lower()
    return "revision" in error_str or "branch" in error_str or "ref" in error_str
