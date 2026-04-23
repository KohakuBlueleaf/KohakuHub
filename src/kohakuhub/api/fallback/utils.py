"""Utility functions for fallback system."""

from typing import Optional

import httpx

from kohakuhub.logger import get_logger

logger = get_logger("FALLBACK_UTILS")


def is_not_found_error(response: httpx.Response) -> bool:
    """Check if response indicates resource not found.

    Args:
        response: HTTP response

    Returns:
        True if 404 or similar "not found" error
    """
    return response.status_code in (404, 410)  # 404 Not Found, 410 Gone


def is_client_error(response: httpx.Response) -> bool:
    """Check if response is a client error (4xx).

    Args:
        response: HTTP response

    Returns:
        True if status code is 4xx
    """
    return 400 <= response.status_code < 500


def is_server_error(response: httpx.Response) -> bool:
    """Check if response is a server error (5xx).

    Args:
        response: HTTP response

    Returns:
        True if status code is 5xx
    """
    return 500 <= response.status_code < 600


def extract_error_message(response: httpx.Response) -> str:
    """Extract error message from response.

    Args:
        response: HTTP response

    Returns:
        Error message string
    """
    try:
        error_data = response.json()
        if isinstance(error_data, dict):
            # Try common error field names
            for field in ("error", "message", "detail", "msg"):
                if field in error_data:
                    msg = error_data[field]
                    if isinstance(msg, str):
                        return msg
                    elif isinstance(msg, dict) and "message" in msg:
                        return msg["message"]
        return str(error_data)
    except Exception:
        return response.text or f"HTTP {response.status_code}"


def should_retry_source(response: httpx.Response) -> bool:
    """Determine if request should be retried with next source.

    Args:
        response: HTTP response

    Returns:
        True if should try next source, False if should give up
    """
    # Retry on 404 (not found) - might be in another source
    if response.status_code == 404:
        return True

    # Retry on server errors (5xx) - source might be temporarily down
    if is_server_error(response):
        return True

    # Retry on timeout/connection errors
    if response.status_code in (408, 504, 524):  # Timeout, Gateway Timeout
        return True

    # Don't retry on other client errors (401, 403, 400, etc.)
    # These indicate permission/validation issues
    if is_client_error(response):
        return False

    # Success - don't retry
    if 200 <= response.status_code < 300:
        return False

    # Default: don't retry
    return False


def strip_xet_response_headers(headers: dict) -> None:
    """Remove Xet-protocol hints from a fallback response's headers in place.

    KohakuHub does not natively speak the huggingface.co Xet protocol. When a
    downstream client (`huggingface_hub >= 1.x`) sees `X-Xet-*` response
    headers or a `Link: <...>; rel="xet-auth"` relation, it switches to the
    Xet code path and calls endpoints we do not implement (`/api/models/...
    /xet-read-token/...`) — breaking the entire download. Stripping these
    signals puts the client back on the classic LFS path, which is served by
    the fallback's standard 3xx Location redirect.

    See `huggingface_hub.utils._xet.parse_xet_file_data_from_response` and
    `huggingface_hub.constants.HUGGINGFACE_HEADER_X_XET_*` for the upstream
    trigger list. This mutates `headers` in place and is a no-op for
    responses that carry no Xet signals.
    """
    for key in list(headers.keys()):
        if key.lower().startswith("x-xet-"):
            headers.pop(key, None)

    link_key = next(
        (k for k in headers.keys() if k.lower() == "link"), None
    )
    if not link_key:
        return

    kept = []
    for chunk in headers[link_key].split(","):
        if 'rel="xet-auth"' in chunk.lower() or "rel=xet-auth" in chunk.lower():
            continue
        kept.append(chunk)
    new_link = ",".join(kept).strip().strip(",").strip()
    if new_link:
        headers[link_key] = new_link
    else:
        headers.pop(link_key, None)


def add_source_headers(
    response: httpx.Response, source_name: str, source_url: str
) -> dict:
    """Generate source attribution headers.

    Args:
        response: Original response from external source
        source_name: Display name of the source
        source_url: Base URL of the source

    Returns:
        Dict of headers to add to the response
    """
    return {
        "X-Source": source_name,
        "X-Source-URL": source_url,
        "X-Source-Status": str(response.status_code),
    }
