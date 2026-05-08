"""HTTP client for external fallback sources with URL mapping."""

from typing import Optional

import httpx

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("FALLBACK_CLIENT")


class FallbackClient:
    """Async HTTP client for querying external fallback sources.

    Handles URL mapping for different source types (HuggingFace vs KohakuHub).
    """

    def __init__(self, source_url: str, source_type: str, token: Optional[str] = None):
        """Initialize fallback client.

        Args:
            source_url: Base URL (e.g., "https://huggingface.co")
            source_type: "huggingface" or "kohakuhub"
            token: Optional API token for authentication
        """
        self.source_url = source_url.rstrip("/")
        self.source_type = source_type
        self.token = token
        self.timeout = cfg.fallback.timeout_seconds

    def map_url(self, kohaku_path: str, repo_type: str) -> str:
        """Map KohakuHub path to external source path.

        HuggingFace URL patterns:
        - Models download: /models/{ns}/{name}/resolve/... -> /{ns}/{name}/resolve/...
        - Datasets download: /datasets/{ns}/{name}/resolve/... -> /datasets/{ns}/{name}/resolve/...
        - Spaces download: /spaces/{ns}/{name}/resolve/... -> /spaces/{ns}/{name}/resolve/...
        - API endpoints: /api/{type}s/... -> /api/{type}s/... (all types keep prefix)

        KohakuHub instances use same URL structure, so no mapping needed.

        Args:
            kohaku_path: KohakuHub request path (e.g., "/models/openai/whisper/resolve/main/config.json")
            repo_type: "model", "dataset", or "space"

        Returns:
            Mapped URL for the external source
        """
        match self.source_type:
            case "kohakuhub":
                # Other KohakuHub instances use same URL structure
                return f"{self.source_url}{kohaku_path}"

            case "huggingface":
                # HuggingFace has asymmetric URLs

                # API endpoints: KEEP /api/{type}s/ prefix for all types
                if kohaku_path.startswith("/api/"):
                    return f"{self.source_url}{kohaku_path}"

                # Check if this is a download/resolve URL (NOT an API endpoint)
                if "/resolve/" in kohaku_path:
                    match repo_type:
                        case "model" if kohaku_path.startswith("/models/"):
                            # Models: DROP /models/ prefix
                            # /models/{ns}/{name}/resolve/... -> /{ns}/{name}/resolve/...
                            mapped_path = kohaku_path.replace("/models/", "/", 1)
                            return f"{self.source_url}{mapped_path}"

                        case "dataset" if kohaku_path.startswith("/datasets/"):
                            # Datasets: KEEP /datasets/ prefix
                            return f"{self.source_url}{kohaku_path}"

                        case "space" if kohaku_path.startswith("/spaces/"):
                            # Spaces: KEEP /spaces/ prefix
                            return f"{self.source_url}{kohaku_path}"

                        case _:
                            # Fallback: use as-is
                            logger.warning(
                                f"Unexpected resolve path pattern: {kohaku_path} for type {repo_type}"
                            )
                            return f"{self.source_url}{kohaku_path}"

                # Fallback for other patterns
                return f"{self.source_url}{kohaku_path}"

            case _:
                raise ValueError(f"Unknown source_type: {self.source_type}")

    async def get(
        self, kohaku_path: str, repo_type: str, follow_redirects: bool = True, **kwargs
    ) -> httpx.Response:
        """Make GET request to external source with URL mapping.

        Args:
            kohaku_path: KohakuHub request path
            repo_type: "model", "dataset", or "space"
            follow_redirects: Whether to follow redirects (default: True)
            **kwargs: Additional arguments passed to httpx.get()

        Returns:
            httpx.Response object

        Raises:
            httpx.HTTPError: On request failure
        """
        external_url = self.map_url(kohaku_path, repo_type)
        # ``dict.pop(key, default)`` returns the value when the key is
        # present even if that value is ``None``; the default is only
        # used when the key is absent. Callers (notably
        # ``_resolve_one_source`` via ``client_headers or None``) may
        # pass ``headers=None`` to mean "no extra headers" — coerce
        # explicitly so the subsequent ``headers["Authorization"] = ...``
        # cannot land on ``None``.
        headers = kwargs.pop("headers", None) or {}

        # Add authentication if token available
        # IMPORTANT: Only use admin-configured token, NEVER user auth!
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        logger.debug(f"GET {external_url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                external_url,
                headers=headers,
                follow_redirects=follow_redirects,
                **kwargs,
            )
            return response

    async def head(self, kohaku_path: str, repo_type: str, **kwargs) -> httpx.Response:
        """Make HEAD request to external source with URL mapping.

        Args:
            kohaku_path: KohakuHub request path
            repo_type: "model", "dataset", or "space"
            **kwargs: Additional arguments passed to httpx.head()

        Returns:
            httpx.Response object

        Raises:
            httpx.HTTPError: On request failure
        """
        external_url = self.map_url(kohaku_path, repo_type)
        # ``dict.pop(key, default)`` returns the value when the key is
        # present even if that value is ``None``; the default is only
        # used when the key is absent. Callers (notably
        # ``_resolve_one_source`` via ``client_headers or None``) may
        # pass ``headers=None`` to mean "no extra headers" — coerce
        # explicitly so the subsequent ``headers["Authorization"] = ...``
        # cannot land on ``None``.
        headers = kwargs.pop("headers", None) or {}

        # Add authentication if token available
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        logger.debug(f"HEAD {external_url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.head(external_url, headers=headers, **kwargs)
            return response

    async def post(
        self,
        kohaku_path: str,
        repo_type: str,
        follow_redirects: bool = True,
        **kwargs,
    ) -> httpx.Response:
        """Make POST request to external source with URL mapping.

        ``follow_redirects`` defaults to ``True`` because HuggingFace
        emits 307 redirects on POST routes that pre-date its
        canonical-name normalization (notably ``/paths-info/``,
        observed 2026-04-30 against ``bert-base-uncased``: HF returns
        ``307`` with ``Location: /api/models/google-bert/bert-base-uncased/paths-info/main``
        that the client is expected to follow as a POST). Without
        following, the classifier sees the 307 — classifies it as
        ``BIND_AND_RESPOND`` — and the caller's ``response.json()``
        fails on the empty redirect body.

        HTTP 307 preserves the request method per RFC 7231, so httpx
        re-POSTs the body to the new Location.

        Args:
            kohaku_path: KohakuHub request path
            repo_type: "model", "dataset", or "space"
            follow_redirects: Whether httpx should auto-follow 3xx
                (default ``True`` — see above for the
                paths-info-on-canonical-name case).
            **kwargs: Additional arguments passed to httpx.post()

        Returns:
            httpx.Response object

        Raises:
            httpx.HTTPError: On request failure
        """
        external_url = self.map_url(kohaku_path, repo_type)
        # ``dict.pop(key, default)`` returns the value when the key is
        # present even if that value is ``None``; the default is only
        # used when the key is absent. Callers (notably
        # ``_resolve_one_source`` via ``client_headers or None``) may
        # pass ``headers=None`` to mean "no extra headers" — coerce
        # explicitly so the subsequent ``headers["Authorization"] = ...``
        # cannot land on ``None``.
        headers = kwargs.pop("headers", None) or {}

        # Add authentication if token available
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        logger.debug(f"POST {external_url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                external_url,
                headers=headers,
                follow_redirects=follow_redirects,
                **kwargs,
            )
            return response
