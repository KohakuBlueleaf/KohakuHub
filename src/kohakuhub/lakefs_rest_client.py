"""LakeFS REST API client using httpx AsyncClient.

This module provides a pure async HTTP client for LakeFS API,
replacing the deprecated lakefs-client library which has threading issues.
"""

from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("LAKEFS_REST")


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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
            response = await client.get(url, auth=self.auth, timeout=None)
            self._check_response(response)
            return response.json()

    async def log_commits(
        self,
        repository: str,
        ref: str,
        after: str | None = None,
        amount: int | None = None,
    ) -> dict[str, Any]:
        """List commits (commit log).

        Args:
            repository: Repository name
            ref: Branch or commit ID
            after: Pagination cursor
            amount: Number of commits to return

        Returns:
            Dict with results (list of Commit) and pagination
        """
        url = f"{self.base_url}/repositories/{repository}/refs/{ref}/commits"
        params = {}
        if after:
            params["after"] = after
        if amount:
            params["amount"] = amount

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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
        try:
            await self.get_repository(repository)
            return True
        except Exception:
            return False

    async def get_branch(self, repository: str, branch: str) -> dict[str, Any]:
        """Get branch details.

        Args:
            repository: Repository name
            branch: Branch name

        Returns:
            Reference dict with commit_id, id (branch name)
        """
        url = f"{self.base_url}/repositories/{repository}/branches/{branch}"

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
            response = await client.put(
                url, params=params, auth=self.auth, timeout=None
            )
            self._check_response(response)


def get_lakefs_rest_client() -> LakeFSRestClient:
    """Get LakeFS REST client instance.

    Returns:
        LakeFSRestClient configured from app config
    """
    return LakeFSRestClient(
        endpoint=cfg.lakefs.endpoint,
        access_key=cfg.lakefs.access_key,
        secret_key=cfg.lakefs.secret_key,
    )
