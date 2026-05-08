"""LakeFS REST API client using httpx AsyncClient.

This module provides a pure async HTTP client for LakeFS API,
replacing the deprecated lakefs-client library which has threading issues.

Connection pooling: ``LakeFSRestClient`` keeps a single underlying
``httpx.AsyncClient`` for its lifetime (lazily created on first use,
disposed via ``aclose()`` / module-level ``close_lakefs_rest_client()``
/ FastAPI lifespan). This way the per-call TCP+TLS handshake is paid
once per connection, not per request — important for the path-filtered
``logCommits`` calls in the file-list ``expand=true`` flow (issue #59),
which fan out N parallel requests per page.
"""

from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("LAKEFS_REST")


# httpx.Limits values picked so a single FastAPI worker can sustain
# ~16-way concurrent LakeFS calls (see LAST_COMMIT_LOOKUP_CONCURRENCY in
# tree.py) with headroom for the rest of the request volume. Conservative
# — bumping them is safe if a deployment fans out heavier.
_HTTPX_LIMITS = httpx.Limits(
    max_connections=64,
    max_keepalive_connections=32,
    keepalive_expiry=30.0,
)


class StagingLocation(BaseModel):
    """LakeFS staging location for physical address linking.

    Schema from LakeFS API: #/components/schemas/StagingLocation
    """

    physical_address: str
    presigned_url: Optional[str] = None
    presigned_url_expiry: Optional[int] = Field(None, description="Unix Epoch time")


class StagingMetadata(BaseModel):
    """LakeFS staging metadata for link_physical_address API.

    Schema from LakeFS API: #/components/schemas/StagingMetadata
    """

    staging: StagingLocation
    checksum: str = Field(
        ..., description="Unique identifier of object content (typically ETag)"
    )
    size_bytes: int
    user_metadata: Optional[dict[str, str]] = None
    content_type: Optional[str] = Field(None, description="Object media type")
    mtime: Optional[int] = Field(None, description="Unix Epoch in seconds")
    force: bool = False


class LakeFSRestClient:
    """Async LakeFS REST API client using httpx.

    All methods are truly async (no thread pool) and use httpx.AsyncClient.
    Base URL: {endpoint}/api/v1
    Auth: Basic Auth (access_key:secret_key)
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        """Initialize LakeFS REST client.

        Args:
            endpoint: LakeFS endpoint URL (e.g., http://localhost:8000)
            access_key: LakeFS access key
            secret_key: LakeFS secret key
        """
        self.endpoint = endpoint.rstrip("/")
        self.base_url = f"{self.endpoint}/api/v1"
        self.auth = (access_key, secret_key)
        # Lazily-constructed pooled httpx client. We DO NOT build it eagerly
        # because httpx.AsyncClient binds connections to the calling event
        # loop on first use; deferring construction lets the same
        # LakeFSRestClient instance survive pytest fixture re-binds in
        # tests, where the loop changes between modules.
        self._httpx_client: httpx.AsyncClient | None = None

    def _httpx(self) -> httpx.AsyncClient:
        """Return the pooled ``httpx.AsyncClient``, creating it on first call.

        Single client per ``LakeFSRestClient`` instance, configured with
        keepalive limits so consecutive calls reuse TCP+TLS connections.
        Auth is still passed per-request for the same reason the un-pooled
        version did (avoids serialising one client per identity).
        """
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(
                limits=_HTTPX_LIMITS,
                # ``timeout=None`` matches the previous per-call default. Per-
                # call sites can still override via ``timeout=`` kwarg if
                # they want a tighter budget.
                timeout=None,
            )
        return self._httpx_client

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` and drop the reference.

        Safe to call multiple times. Subsequent calls to any client method
        will lazily re-create a fresh client (rare, but useful for tests
        that restart the event loop).
        """
        if self._httpx_client is not None:
            client = self._httpx_client
            self._httpx_client = None
            await client.aclose()

    def _check_response(self, response: httpx.Response) -> None:
        """Check response status and raise detailed error if not OK.

        Args:
            response: httpx Response object

        Raises:
            httpx.HTTPStatusError: With response.text included in exception message
        """
        if not response.is_success:
            # Include response body in error for debugging
            error_detail = response.text if response.text else "(empty response)"

            # Comprehensive logging
            logger.error(
                f"LakeFS API request failed:\n"
                f"  Status: {response.status_code} {response.reason_phrase}\n"
                f"  Method: {response.request.method}\n"
                f"  URL: {response.url}\n"
                f"  Response Body: {error_detail}"
            )

            # Create comprehensive error message
            error_msg = (
                f"LakeFS API error {response.status_code} {response.reason_phrase} "
                f"for {response.request.method} {response.url}: {error_detail}"
            )

            # Raise HTTPStatusError with full context
            raise httpx.HTTPStatusError(
                error_msg,
                request=response.request,
                response=response,
            )

    async def get_object(
        self, repository: str, ref: str, path: str, range_header: str | None = None
    ) -> bytes:
        """Get object content.

        Args:
            repository: Repository name
            ref: Branch or commit ID
            path: Object path
            range_header: Optional byte range (e.g., "bytes=0-1023")

        Returns:
            Object content as bytes
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{ref}/objects"
        headers = {}
        if range_header:
            headers["Range"] = range_header

        client = self._httpx()
        response = await client.get(
            url,
            params={"path": path},
            headers=headers,
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.content

    async def stat_object(
        self, repository: str, ref: str, path: str, user_metadata: bool = True
    ) -> dict[str, Any]:
        """Get object metadata.

        Args:
            repository: Repository name
            ref: Branch or commit ID
            path: Object path
            user_metadata: Include user metadata

        Returns:
            ObjectStats dict with keys: path, path_type, physical_address, checksum, size_bytes, mtime, metadata, content_type
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{ref}/objects/stat"

        client = self._httpx()
        response = await client.get(
            url,
            params={"path": path, "user_metadata": user_metadata},
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.json()

    async def upload_object(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        force: bool = False,
    ) -> dict[str, Any]:
        """Upload object.

        Args:
            repository: Repository name
            branch: Branch name
            path: Object path
            content: File content as bytes
            force: Overwrite existing object

        Returns:
            ObjectStats dict
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}/objects"

        client = self._httpx()
        response = await client.post(
            url,
            params={"path": path, "force": force},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.json()

    async def list_repositories(
        self, amount: int = 1000, after: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """List repositories.

        Args:
            amount: Maximum number of repositories to return
            after: Pagination offset token from a previous response

        Returns:
            Raw LakeFS API response payload
        """
        url = f"{self.base_url}/repositories"
        params: dict[str, Any] = {"amount": amount}
        if after:
            params["after"] = after

        client = self._httpx()
        response = await client.get(
            url,
            params=params,
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.json()

    async def link_physical_address(
        self,
        repository: str,
        branch: str,
        path: str,
        staging_metadata: StagingMetadata | dict[str, Any],
    ) -> dict[str, Any]:
        """Link physical address (for LFS objects).

        Args:
            repository: Repository name
            branch: Branch name
            path: Object path in repo
            staging_metadata: StagingMetadata model or dict with staging, checksum, size_bytes

        Returns:
            ObjectStats dict
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}/staging/backing"

        # Convert StagingMetadata to dict if needed
        if isinstance(staging_metadata, StagingMetadata):
            metadata_dict = staging_metadata.model_dump(exclude_none=True)
        else:
            metadata_dict = staging_metadata

        client = self._httpx()
        response = await client.put(
            url,
            params={"path": path},
            json=metadata_dict,
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.json()

    async def commit(
        self,
        repository: str,
        branch: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create commit.

        Args:
            repository: Repository name
            branch: Branch name
            message: Commit message
            metadata: Optional commit metadata

        Returns:
            Commit dict with keys: id, parents, committer, message, creation_date, meta_range_id, metadata
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}/commits"

        commit_data = {"message": message}
        if metadata:
            commit_data["metadata"] = metadata

        client = self._httpx()
        response = await client.post(
            url,
            json=commit_data,
            auth=self.auth,
            timeout=None,  # No timeout for internal service
        )
        self._check_response(response)
        return response.json()

    async def get_commit(self, repository: str, commit_id: str) -> dict[str, Any]:
        """Get commit details.

        Args:
            repository: Repository name
            commit_id: Commit ID (SHA)

        Returns:
            Commit dict
        """
        url = f"{self.base_url}/repositories/{repository}/commits/{commit_id}"

        client = self._httpx()
        response = await client.get(url, auth=self.auth, timeout=None)
        self._check_response(response)
        return response.json()

    async def log_commits(
        self,
        repository: str,
        ref: str,
        after: str | None = None,
        amount: int | None = None,
        objects: list[str] | None = None,
        prefixes: list[str] | None = None,
        limit: bool | None = None,
        first_parent: bool | None = None,
    ) -> dict[str, Any]:
        """List commits (commit log).

        Args:
            repository: Repository name
            ref: Branch or commit ID
            after: Pagination cursor
            amount: Number of commits to return
            objects: Restrict the log to commits that touched any of these
                exact paths. Server-side filter via LakeFS metarange tree —
                much cheaper than walking diffs client-side. **Requires
                LakeFS v0.54.0 (2021-11-08) or newer.** Pre-v0.54 servers
                ignore this parameter and return the unfiltered log; the
                caller must check the response for the expected commit.
            prefixes: Same as ``objects`` but the entries are path prefixes;
                a commit qualifies if it touched any descendant. Same
                version requirement as ``objects``.
            limit: When True, cap the result set at ``amount`` and stop the
                walk early. Useful with ``amount=1`` to ask "the most recent
                commit that touched X" in a single round-trip. Same version
                requirement as ``objects``.
            first_parent: When True, follow only the first parent at merge
                commits (LakeFS equivalent of ``git log --first-parent``).
                Available since LakeFS v0.96.0.

        Returns:
            Dict with results (list of Commit) and pagination.
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{ref}/commits"
        params: list[tuple[str, Any]] = []
        if after:
            params.append(("after", after))
        if amount:
            params.append(("amount", amount))
        # ``objects`` and ``prefixes`` are repeated query params per LakeFS
        # OpenAPI; use a list-of-tuples so httpx serialises them as repeats
        # instead of joining with commas.
        if objects:
            for obj in objects:
                params.append(("objects", obj))
        if prefixes:
            for prefix in prefixes:
                params.append(("prefixes", prefix))
        if limit is not None:
            # LakeFS expects the literal "true"/"false" strings here.
            params.append(("limit", "true" if limit else "false"))
        if first_parent is not None:
            params.append(("first_parent", "true" if first_parent else "false"))

        client = self._httpx()
        response = await client.get(
            url, params=params, auth=self.auth, timeout=None
        )
        self._check_response(response)
        return response.json()

    async def diff_refs(
        self,
        repository: str,
        left_ref: str,
        right_ref: str,
        after: str | None = None,
        amount: int | None = None,
    ) -> dict[str, Any]:
        """Get diff between two refs.

        Args:
            repository: Repository name
            left_ref: Left reference (base)
            right_ref: Right reference (compare)
            after: Pagination cursor
            amount: Number of diff entries to return

        Returns:
            Dict with results (list of Diff) and pagination
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{left_ref}/diff/{right_ref}"
        params = {}
        if after:
            params["after"] = after
        if amount:
            params["amount"] = amount

        client = self._httpx()
        response = await client.get(
            url, params=params, auth=self.auth, timeout=None
        )
        self._check_response(response)
        return response.json()

    async def list_objects(
        self,
        repository: str,
        ref: str,
        prefix: str = "",
        after: str = "",
        amount: int = 1000,
        delimiter: str = "",
    ) -> dict[str, Any]:
        """List objects in repository.

        Args:
            repository: Repository name
            ref: Branch or commit ID
            prefix: Path prefix filter
            after: Pagination cursor
            amount: Number of objects to return
            delimiter: Delimiter for grouping (e.g., "/" for directory-like listing)

        Returns:
            Dict with results (list of ObjectStats) and pagination
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{ref}/objects/ls"

        # Build params - only include non-empty values to avoid LakeFS issues
        params: dict[str, Any] = {"amount": amount}

        # Only add prefix/after/delimiter if they have values
        if prefix:
            params["prefix"] = prefix
        if after:
            params["after"] = after
        if delimiter:
            params["delimiter"] = delimiter

        client = self._httpx()
        response = await client.get(
            url, params=params, auth=self.auth, timeout=None
        )
        self._check_response(response)
        return response.json()

    async def delete_object(
        self, repository: str, branch: str, path: str, force: bool = False
    ) -> None:
        """Delete object.

        Args:
            repository: Repository name
            branch: Branch name
            path: Object path
            force: Force deletion
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}/objects"

        client = self._httpx()
        response = await client.delete(
            url, params={"path": path, "force": force}, auth=self.auth, timeout=None
        )
        self._check_response(response)

    async def create_repository(
        self, name: str, storage_namespace: str, default_branch: str = "main"
    ) -> dict[str, Any]:
        """Create repository.

        Args:
            name: Repository name
            storage_namespace: S3/storage location (e.g., s3://bucket/prefix)
            default_branch: Default branch name

        Returns:
            Repository dict
        """
        url = f"{self.base_url}/repositories"

        repo_data = {
            "name": name,
            "storage_namespace": storage_namespace,
            "default_branch": default_branch,
        }

        client = self._httpx()
        response = await client.post(
            url, json=repo_data, auth=self.auth, timeout=None
        )
        self._check_response(response)
        return response.json()

    async def delete_repository(self, repository: str, force: bool = False) -> None:
        """Delete repository.

        Args:
            repository: Repository name
            force: Force deletion
        """
        url = f"{self.base_url}/repositories/{repository}"

        client = self._httpx()
        response = await client.delete(
            url, params={"force": force}, auth=self.auth, timeout=None
        )
        self._check_response(response)

    async def get_repository(self, repository: str) -> dict[str, Any]:
        """Get repository details.

        Args:
            repository: Repository name

        Returns:
            Repository dict

        Raises:
            httpx.HTTPStatusError: If repository not found (404)
        """
        url = f"{self.base_url}/repositories/{repository}"

        client = self._httpx()
        response = await client.get(url, auth=self.auth, timeout=None)
        self._check_response(response)
        return response.json()

    async def repository_exists(self, repository: str) -> bool:
        """Check if repository exists.

        Args:
            repository: Repository name

        Returns:
            True if repository exists, False otherwise
        """
        url = f"{self.base_url}/repositories/{repository}"

        client = self._httpx()
        response = await client.get(url, auth=self.auth, timeout=None)
        if response.status_code == 404:
            return False
        self._check_response(response)
        return True

    async def get_branch(self, repository: str, branch: str) -> dict[str, Any]:
        """Get branch details.

        Args:
            repository: Repository name
            branch: Branch name

        Returns:
            Reference dict with commit_id, id (branch name)
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}"

        client = self._httpx()
        response = await client.get(url, auth=self.auth, timeout=None)
        self._check_response(response)
        return response.json()

    async def list_branches(
        self,
        repository: str,
        after: str | None = None,
        amount: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """List repository branches.

        Args:
            repository: Repository name
            after: Pagination cursor
            amount: Number of branches to return

        Returns:
            LakeFS branch list payload
        """
        url = f"{self.base_url}/repositories/{repository}/branches"
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if amount:
            params["amount"] = amount

        client = self._httpx()
        response = await client.get(
            url,
            params=params,
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.json()

    async def create_branch(self, repository: str, name: str, source: str) -> None:
        """Create branch.

        Args:
            repository: Repository name
            name: Branch name
            source: Source reference (branch/commit to branch from)

        Returns:
            None (201 response with text/html body)
        """
        url = f"{self.base_url}/repositories/{repository}/branches"

        branch_data = {"name": name, "source": source}

        client = self._httpx()
        response = await client.post(
            url, json=branch_data, auth=self.auth, timeout=None
        )
        self._check_response(response)
        # LakeFS returns 201 with text/html (plain string ref), not JSON
        # We don't need to return it since we already know the branch name

    async def delete_branch(
        self, repository: str, branch: str, force: bool = False
    ) -> None:
        """Delete branch.

        Args:
            repository: Repository name
            branch: Branch name
            force: Force deletion
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}"

        client = self._httpx()
        response = await client.delete(
            url, params={"force": force}, auth=self.auth, timeout=None
        )
        self._check_response(response)

    async def create_tag(
        self, repository: str, id: str, ref: str, force: bool = False
    ) -> dict[str, Any]:
        """Create tag.

        Args:
            repository: Repository name
            id: Tag name/ID
            ref: Reference to tag (commit/branch)
            force: Force creation

        Returns:
            Reference dict
        """
        url = f"{self.base_url}/repositories/{repository}/tags"

        tag_data = {"id": id, "ref": ref, "force": force}

        client = self._httpx()
        response = await client.post(
            url, json=tag_data, auth=self.auth, timeout=None
        )
        self._check_response(response)
        return response.json()

    async def list_tags(
        self,
        repository: str,
        after: str | None = None,
        amount: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """List repository tags.

        Args:
            repository: Repository name
            after: Pagination cursor
            amount: Number of tags to return

        Returns:
            LakeFS tag list payload
        """
        url = f"{self.base_url}/repositories/{repository}/tags"
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if amount:
            params["amount"] = amount

        client = self._httpx()
        response = await client.get(
            url,
            params=params,
            auth=self.auth,
            timeout=None,
        )
        self._check_response(response)
        return response.json()

    async def delete_tag(self, repository: str, tag: str, force: bool = False) -> None:
        """Delete tag.

        Args:
            repository: Repository name
            tag: Tag name
            force: Force deletion
        """
        url = f"{self.base_url}/repositories/{repository}/tags/{tag}"

        client = self._httpx()
        response = await client.delete(
            url, params={"force": force}, auth=self.auth, timeout=None
        )
        self._check_response(response)

    async def revert_branch(
        self,
        repository: str,
        branch: str,
        ref: str,
        parent_number: int = 1,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        force: bool = False,
        allow_empty: bool = False,
    ) -> None:
        """Revert a commit on a branch.

        Args:
            repository: Repository name
            branch: Branch name to revert on
            ref: The commit to revert (commit ID or ref)
            parent_number: When reverting a merge commit, parent number (starting from 1)
            message: Optional custom commit message
            metadata: Optional commit metadata
            force: Force revert
            allow_empty: Allow empty commit (revert without changes)
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}/revert"

        revert_data: dict[str, Any] = {
            "ref": ref,
            "parent_number": parent_number,
            "force": force,
            "allow_empty": allow_empty,
        }

        if message or metadata:
            commit_overrides: dict[str, Any] = {}
            if message:
                commit_overrides["message"] = message
            if metadata:
                commit_overrides["metadata"] = metadata
            revert_data["commit_overrides"] = commit_overrides

        client = self._httpx()
        response = await client.post(
            url, json=revert_data, auth=self.auth, timeout=None
        )
        self._check_response(response)

    async def merge_into_branch(
        self,
        repository: str,
        source_ref: str,
        destination_branch: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        strategy: str | None = None,
        force: bool = False,
        allow_empty: bool = False,
        squash_merge: bool = False,
    ) -> dict[str, Any]:
        """Merge source ref into destination branch.

        Args:
            repository: Repository name
            source_ref: Source reference (branch/commit to merge from)
            destination_branch: Destination branch name
            message: Merge commit message
            metadata: Merge commit metadata
            strategy: Conflict resolution strategy ('dest-wins' or 'source-wins')
            force: Allow merge into read-only branch or same content
            allow_empty: Allow merge when branches have same content
            squash_merge: Squash merge (single commit)

        Returns:
            MergeResult dict with reference and summary
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{source_ref}/merge/{destination_branch}"

        merge_data: dict[str, Any] = {
            "force": force,
            "allow_empty": allow_empty,
            "squash_merge": squash_merge,
        }

        if message:
            merge_data["message"] = message
        if metadata:
            merge_data["metadata"] = metadata
        if strategy:
            merge_data["strategy"] = strategy

        client = self._httpx()
        response = await client.post(
            url, json=merge_data, auth=self.auth, timeout=None
        )
        self._check_response(response)
        return response.json()

    async def hard_reset_branch(
        self,
        repository: str,
        branch: str,
        ref: str,
        force: bool = False,
    ) -> None:
        """Hard reset branch to point to a specific commit.

        This is like 'git reset --hard <ref>' - it relocates the branch
        to point to the specified ref, effectively resetting the branch
        state to that commit.

        Args:
            repository: Repository name
            branch: Branch name to reset
            ref: Target commit ID or ref to reset to
            force: Force reset even if branch has uncommitted data

        Raises:
            httpx.HTTPStatusError: If reset fails
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}/hard_reset"

        params = {
            "ref": ref,
            "force": force,
        }

        client = self._httpx()
        response = await client.put(
            url, params=params, auth=self.auth, timeout=None
        )
        self._check_response(response)


_singleton_client: LakeFSRestClient | None = None


def get_lakefs_rest_client() -> LakeFSRestClient:
    """Return the process-wide ``LakeFSRestClient`` singleton.

    A single instance is reused for the lifetime of the process so its
    pooled ``httpx.AsyncClient`` can keep connections alive across calls.
    Lazily constructed on first call (and on first call after each
    ``close_lakefs_rest_client()``).
    """
    global _singleton_client
    if _singleton_client is None:
        _singleton_client = LakeFSRestClient(
            endpoint=cfg.lakefs.endpoint,
            access_key=cfg.lakefs.access_key,
            secret_key=cfg.lakefs.secret_key,
        )
    return _singleton_client


async def close_lakefs_rest_client() -> None:
    """Tear down the singleton client and its pooled httpx connections.

    Wired into FastAPI's lifespan shutdown hook so workers exit cleanly
    instead of dangling open sockets to LakeFS. Safe to call multiple
    times — subsequent ``get_lakefs_rest_client()`` calls lazily rebuild.
    """
    global _singleton_client
    if _singleton_client is not None:
        client = _singleton_client
        _singleton_client = None
        await client.aclose()
