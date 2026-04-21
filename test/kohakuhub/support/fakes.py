"""Fake external services used by isolated backend helper tests."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_bytes(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _deserialize_bytes(data: str | None) -> bytes | None:
    if data is None:
        return None
    return base64.b64decode(data.encode("ascii"))


@dataclass(slots=True)
class FakeS3Object:
    """Stored fake S3 object."""

    body: bytes
    content_type: str = "application/octet-stream"
    last_modified: datetime = field(default_factory=_utc_now)

    @property
    def etag(self) -> str:
        return hashlib.md5(self.body).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": _serialize_bytes(self.body),
            "content_type": self.content_type,
            "last_modified": self.last_modified.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FakeS3Object":
        return cls(
            body=_deserialize_bytes(payload["body"]) or b"",
            content_type=payload.get("content_type", "application/octet-stream"),
            last_modified=datetime.fromisoformat(payload["last_modified"]),
        )


class FakeS3Paginator:
    """Small paginator emulating boto3 list_objects_v2 pagination."""

    def __init__(self, service: "FakeS3Service"):
        self.service = service

    def paginate(self, Bucket: str, Prefix: str = "") -> Iterable[dict[str, Any]]:
        keys = sorted(
            key for bucket, key in self.service.objects if bucket == Bucket and key.startswith(Prefix)
        )
        if not keys:
            yield {}
            return

        yield {"Contents": [{"Key": key} for key in keys]}


class FakeS3Client:
    """Minimal boto3-compatible client."""

    def __init__(self, service: "FakeS3Service"):
        self.service = service

    def head_bucket(self, Bucket: str) -> None:
        if Bucket not in self.service.buckets:
            error = Exception("Bucket not found")
            error.response = {"Error": {"Code": "404"}}
            raise error

    def create_bucket(self, Bucket: str, CreateBucketConfiguration: dict | None = None) -> None:
        self.service.buckets.add(Bucket)

    def generate_presigned_url(
        self,
        operation_name: str,
        Params: dict[str, Any],
        ExpiresIn: int = 3600,
        HttpMethod: str | None = None,
    ) -> str:
        bucket = Params["Bucket"]
        key = Params["Key"]
        query = [f"op={operation_name}", f"expires_in={ExpiresIn}"]
        if "UploadId" in Params:
            query.append(f"upload_id={Params['UploadId']}")
        if "PartNumber" in Params:
            query.append(f"part_number={Params['PartNumber']}")
        return f"https://fake-s3.local/{bucket}/{quote(key)}?{'&'.join(query)}"

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        obj = self.service.objects.get((Bucket, Key))
        if obj is None:
            raise FileNotFoundError(f"S3 object not found: {Bucket}/{Key}")
        return {
            "ContentLength": len(obj.body),
            "ETag": f'"{obj.etag}"',
            "LastModified": obj.last_modified,
            "ContentType": obj.content_type,
        }

    def get_paginator(self, operation_name: str) -> FakeS3Paginator:
        if operation_name != "list_objects_v2":
            raise ValueError(f"Unsupported paginator: {operation_name}")
        return FakeS3Paginator(self.service)

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        deleted = []
        for entry in Delete.get("Objects", []):
            key = entry["Key"]
            if (Bucket, key) in self.service.objects:
                del self.service.objects[(Bucket, key)]
                deleted.append({"Key": key})
        return {"Deleted": deleted}

    def copy_object(self, Bucket: str, CopySource: dict[str, str], Key: str) -> dict[str, Any]:
        src_bucket = CopySource["Bucket"]
        src_key = CopySource["Key"]
        src_obj = self.service.objects[(src_bucket, src_key)]
        self.service.put_object(
            Bucket=Bucket,
            Key=Key,
            Body=src_obj.body,
            ContentType=src_obj.content_type,
        )
        return {"CopyObjectResult": {"ETag": src_obj.etag}}

    def delete_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        self.service.objects.pop((Bucket, Key), None)
        return {}

    def create_multipart_upload(self, Bucket: str, Key: str, ContentType: str | None = None) -> dict[str, Any]:
        upload_id = hashlib.sha1(f"{Bucket}:{Key}:{time.time()}".encode()).hexdigest()
        self.service.multipart_uploads[upload_id] = {
            "bucket": Bucket,
            "key": Key,
            "content_type": ContentType or "application/octet-stream",
            "parts": {},
        }
        return {"UploadId": upload_id}

    def complete_multipart_upload(self, Bucket: str, Key: str, UploadId: str, MultipartUpload: dict[str, Any]) -> dict[str, Any]:
        upload = self.service.multipart_uploads.pop(UploadId)
        ordered_parts = sorted(upload["parts"].items())
        body = b"".join(part_body for _, part_body in ordered_parts)
        self.service.put_object(
            Bucket=Bucket,
            Key=Key,
            Body=body,
            ContentType=upload["content_type"],
        )
        return {"Bucket": Bucket, "Key": Key}

    def abort_multipart_upload(self, Bucket: str, Key: str, UploadId: str) -> dict[str, Any]:
        self.service.multipart_uploads.pop(UploadId, None)
        return {}


class FakeS3Service:
    """In-memory fake S3 backend."""

    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], FakeS3Object] = {}
        self.multipart_uploads: dict[str, dict[str, Any]] = {}

    def get_client(self) -> FakeS3Client:
        return FakeS3Client(self)

    def init_storage(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str | None = None,
    ) -> None:
        self.buckets.add(Bucket)
        self.objects[(Bucket, Key)] = FakeS3Object(
            body=Body,
            content_type=ContentType or "application/octet-stream",
        )

    def object_exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, Any]:
        obj = self.objects[(bucket, key)]
        return {
            "size": len(obj.body),
            "etag": obj.etag,
            "last_modified": obj.last_modified,
            "content_type": obj.content_type,
        }

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        keys = [key for key in self.objects if key[0] == bucket and key[1].startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)

    def copy_prefix(self, bucket: str, from_prefix: str, to_prefix: str, exclude_prefix: str | None = None) -> int:
        copied = 0
        source_keys = sorted(
            key for key in self.objects if key[0] == bucket and key[1].startswith(from_prefix)
        )
        for bucket_name, key in source_keys:
            relative = key[len(from_prefix) :]
            if exclude_prefix and relative.startswith(exclude_prefix):
                continue
            obj = self.objects[(bucket_name, key)]
            self.put_object(
                Bucket=bucket_name,
                Key=f"{to_prefix}{relative}",
                Body=obj.body,
                ContentType=obj.content_type,
            )
            copied += 1
        return copied

    def export_snapshot(self) -> dict[str, Any]:
        return {
            "buckets": sorted(self.buckets),
            "objects": {
                json.dumps([bucket, key]): obj.to_dict()
                for (bucket, key), obj in sorted(self.objects.items())
            },
            "multipart_uploads": copy.deepcopy(self.multipart_uploads),
        }

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.buckets = set(snapshot.get("buckets", []))
        self.objects = {
            tuple(json.loads(encoded_key)): FakeS3Object.from_dict(payload)
            for encoded_key, payload in snapshot.get("objects", {}).items()
        }
        self.multipart_uploads = copy.deepcopy(snapshot.get("multipart_uploads", {}))


@dataclass(slots=True)
class FakeLakeFSObject:
    """Stored fake LakeFS object."""

    path: str
    content: bytes | None
    physical_address: str
    checksum: str
    size_bytes: int
    mtime: float
    content_type: str = "application/octet-stream"

    def to_stat(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "path_type": "object",
            "physical_address": self.physical_address,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "content_type": self.content_type,
            "metadata": {},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content": _serialize_bytes(self.content),
            "physical_address": self.physical_address,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FakeLakeFSObject":
        return cls(
            path=payload["path"],
            content=_deserialize_bytes(payload.get("content")),
            physical_address=payload["physical_address"],
            checksum=payload["checksum"],
            size_bytes=payload["size_bytes"],
            mtime=payload["mtime"],
            content_type=payload.get("content_type", "application/octet-stream"),
        )


class FakeLakeFSClient:
    """In-memory fake LakeFS REST client."""

    def __init__(self, s3_service: FakeS3Service, default_bucket: str):
        self.s3_service = s3_service
        self.default_bucket = default_bucket
        self.repositories: dict[str, dict[str, Any]] = {}

    def _copy_snapshot(self, snapshot: dict[str, FakeLakeFSObject]) -> dict[str, FakeLakeFSObject]:
        return {path: copy.deepcopy(obj) for path, obj in snapshot.items()}

    def _new_commit_id(self, repo_name: str) -> str:
        repo = self.repositories[repo_name]
        repo["next_commit"] += 1
        return f"{repo['next_commit']:040x}"

    def _initial_commit(self, repo_name: str) -> str:
        repo = self.repositories[repo_name]
        commit_id = self._new_commit_id(repo_name)
        repo["commits"][commit_id] = {
            "id": commit_id,
            "message": "Repository initialized",
            "creation_date": int(time.time()),
            "metadata": {},
            "parents": [],
            "snapshot": {},
        }
        return commit_id

    def _get_ref_snapshot(self, repo_name: str, ref: str) -> dict[str, FakeLakeFSObject]:
        repo = self.repositories[repo_name]
        if ref in repo["branches"]:
            commit_id = repo["branches"][ref]["commit_id"]
            return self._copy_snapshot(repo["commits"][commit_id]["snapshot"])
        if ref in repo["tags"]:
            commit_id = repo["tags"][ref]
            return self._copy_snapshot(repo["commits"][commit_id]["snapshot"])
        if ref in repo["commits"]:
            return self._copy_snapshot(repo["commits"][ref]["snapshot"])
        raise FileNotFoundError(f"404 ref not found: {repo_name}@{ref}")

    def _get_repo(self, repository: str) -> dict[str, Any]:
        if repository not in self.repositories:
            raise FileNotFoundError(f"404 repository not found: {repository}")
        return self.repositories[repository]

    def _branch(self, repository: str, branch: str) -> dict[str, Any]:
        repo = self._get_repo(repository)
        if branch not in repo["branches"]:
            raise FileNotFoundError(f"404 branch not found: {repository}/{branch}")
        return repo["branches"][branch]

    async def create_repository(
        self,
        name: str,
        storage_namespace: str,
        default_branch: str = "main",
    ) -> dict[str, Any]:
        if name in self.repositories:
            raise ValueError(f"409 repository already exists: {name}")
        self.repositories[name] = {
            "storage_namespace": storage_namespace,
            "branches": {},
            "tags": {},
            "commits": {},
            "next_commit": 0,
        }
        initial_commit = self._initial_commit(name)
        self.repositories[name]["branches"][default_branch] = {
            "commit_id": initial_commit,
            "staging": {},
        }
        return {"id": name, "default_branch": default_branch}

    async def delete_repository(self, repository: str) -> None:
        self.repositories.pop(repository, None)

    async def repository_exists(self, repository: str) -> bool:
        return repository in self.repositories

    async def get_branch(self, repository: str, branch: str) -> dict[str, Any]:
        branch_state = self._branch(repository, branch)
        return {"id": branch, "commit_id": branch_state["commit_id"]}

    async def create_branch(
        self,
        repository: str,
        name: str | None = None,
        source: str = "",
        branch: str | None = None,
    ) -> dict[str, Any]:
        repo = self._get_repo(repository)
        branch_name = name or branch
        if not branch_name:
            raise ValueError("Missing branch name")
        if branch_name in repo["branches"]:
            raise ValueError(f"409 branch already exists: {branch_name}")
        snapshot = self._get_ref_snapshot(repository, source)
        commit_id = repo["branches"][source]["commit_id"] if source in repo["branches"] else source
        repo["branches"][branch_name] = {"commit_id": commit_id, "staging": snapshot}
        return {"id": branch_name, "commit_id": commit_id}

    async def delete_branch(self, repository: str, branch: str) -> None:
        repo = self._get_repo(repository)
        if branch == "main":
            raise ValueError("Cannot delete main branch")
        repo["branches"].pop(branch, None)

    async def create_tag(
        self,
        repository: str,
        id: str | None = None,
        ref: str = "",
        tag: str | None = None,
    ) -> dict[str, Any]:
        repo = self._get_repo(repository)
        tag_name = id or tag
        if not tag_name:
            raise ValueError("Missing tag name")
        if tag_name in repo["tags"]:
            raise ValueError(f"409 tag already exists: {tag_name}")
        commit_id = ref
        if ref in repo["branches"]:
            commit_id = repo["branches"][ref]["commit_id"]
        elif ref not in repo["commits"]:
            raise FileNotFoundError(f"404 commit not found: {ref}")
        repo["tags"][tag_name] = commit_id
        return {"id": tag_name, "commit_id": commit_id}

    async def delete_tag(self, repository: str, tag: str) -> None:
        repo = self._get_repo(repository)
        repo["tags"].pop(tag, None)

    async def get_commit(self, repository: str, commit_id: str) -> dict[str, Any]:
        repo = self._get_repo(repository)
        if commit_id not in repo["commits"]:
            raise FileNotFoundError(f"404 commit not found: {commit_id}")
        commit = repo["commits"][commit_id]
        return {
            "id": commit["id"],
            "message": commit["message"],
            "creation_date": commit["creation_date"],
            "metadata": commit["metadata"],
            "parents": list(commit["parents"]),
        }

    async def upload_object(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        force: bool = False,
    ) -> dict[str, Any]:
        branch_state = self._branch(repository, branch)
        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        branch_state["staging"][path] = FakeLakeFSObject(
            path=path,
            content=content,
            physical_address=f"s3://{self.default_bucket}/{repository}/objects/{quote(path)}",
            checksum=checksum,
            size_bytes=len(content),
            mtime=time.time(),
        )
        return branch_state["staging"][path].to_stat()

    async def link_physical_address(
        self,
        repository: str,
        branch: str,
        path: str,
        staging_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        branch_state = self._branch(repository, branch)
        physical_address = staging_metadata["staging"]["physical_address"]
        checksum = staging_metadata.get("checksum", "")
        size = staging_metadata.get("size_bytes", 0)
        content_type = staging_metadata.get("content_type") or "application/octet-stream"
        content = None
        if physical_address.startswith("s3://"):
            bucket, key = physical_address[5:].split("/", 1)
            if self.s3_service.object_exists(bucket, key):
                content = self.s3_service.objects[(bucket, key)].body
                size = len(content)
        branch_state["staging"][path] = FakeLakeFSObject(
            path=path,
            content=content,
            physical_address=physical_address,
            checksum=checksum,
            size_bytes=size,
            mtime=time.time(),
            content_type=content_type,
        )
        return branch_state["staging"][path].to_stat()

    async def delete_object(self, repository: str, branch: str, path: str) -> None:
        branch_state = self._branch(repository, branch)
        branch_state["staging"].pop(path, None)

    async def commit(
        self,
        repository: str,
        branch: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repo = self._get_repo(repository)
        branch_state = self._branch(repository, branch)
        current_commit_id = branch_state["commit_id"]
        current_snapshot = self._copy_snapshot(repo["commits"][current_commit_id]["snapshot"])
        current_snapshot = self._copy_snapshot(branch_state["staging"])
        commit_id = self._new_commit_id(repository)
        repo["commits"][commit_id] = {
            "id": commit_id,
            "message": message,
            "creation_date": int(time.time()),
            "metadata": metadata or {},
            "parents": [current_commit_id],
            "snapshot": current_snapshot,
        }
        branch_state["commit_id"] = commit_id
        branch_state["staging"] = self._copy_snapshot(current_snapshot)
        return {
            "id": commit_id,
            "message": message,
            "creation_date": repo["commits"][commit_id]["creation_date"],
            "metadata": metadata or {},
            "parents": [current_commit_id],
        }

    def _sorted_paths(self, snapshot: dict[str, FakeLakeFSObject], prefix: str) -> list[str]:
        return sorted(path for path in snapshot if path.startswith(prefix))

    async def list_objects(
        self,
        repository: str,
        ref: str,
        prefix: str = "",
        delimiter: str = "",
        amount: int = 1000,
        after: str = "",
    ) -> dict[str, Any]:
        snapshot = self._get_ref_snapshot(repository, ref)
        paths = [path for path in self._sorted_paths(snapshot, prefix) if path > after]
        results = []
        seen_prefixes = set()

        for path in paths:
            obj = snapshot[path]
            if delimiter == "":
                results.append(obj.to_stat())
                continue

            relative = path[len(prefix) :]
            if delimiter in relative:
                first_segment = relative.split(delimiter, 1)[0]
                common_prefix = f"{prefix}{first_segment}/"
                if common_prefix in seen_prefixes:
                    continue
                seen_prefixes.add(common_prefix)
                results.append(
                    {
                        "path": common_prefix,
                        "path_type": "common_prefix",
                        "checksum": "",
                        "size_bytes": 0,
                        "mtime": obj.mtime,
                    }
                )
            else:
                results.append(obj.to_stat())

        page = results[:amount]
        has_more = len(results) > amount
        next_offset = page[-1]["path"] if has_more and page else ""
        return {
            "results": page,
            "pagination": {"has_more": has_more, "next_offset": next_offset},
        }

    async def stat_object(
        self, repository: str, ref: str, path: str, user_metadata: bool = True
    ) -> dict[str, Any]:
        snapshot = self._get_ref_snapshot(repository, ref)
        if path not in snapshot:
            raise FileNotFoundError(f"404 object not found: {path}")
        return snapshot[path].to_stat()

    async def get_object(
        self, repository: str, ref: str, path: str, range_header: str | None = None
    ) -> bytes:
        snapshot = self._get_ref_snapshot(repository, ref)
        if path not in snapshot:
            raise FileNotFoundError(f"404 object not found: {path}")
        content = snapshot[path].content
        if content is None:
            raise FileNotFoundError(f"Object content unavailable for path: {path}")
        if not range_header:
            return content
        if not range_header.startswith("bytes="):
            return content
        start_str, end_str = range_header[6:].split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else len(content) - 1
        return content[start : end + 1]

    async def log_commits(
        self, repository: str, ref: str, amount: int = 20, after: str = ""
    ) -> dict[str, Any]:
        repo = self._get_repo(repository)
        if ref in repo["branches"]:
            current = repo["branches"][ref]["commit_id"]
        elif ref in repo["commits"]:
            current = ref
        else:
            raise FileNotFoundError(f"404 ref not found: {ref}")

        commit_chain = []
        skip = bool(after)
        while current:
            commit = repo["commits"][current]
            if skip:
                if current == after:
                    skip = False
                current = commit["parents"][0] if commit["parents"] else ""
                continue
            commit_chain.append(
                {
                    "id": commit["id"],
                    "message": commit["message"],
                    "creation_date": commit["creation_date"],
                    "metadata": commit["metadata"],
                    "parents": commit["parents"],
                }
            )
            current = commit["parents"][0] if commit["parents"] else ""
            if len(commit_chain) >= amount:
                break

        return {
            "results": commit_chain,
            "pagination": {"has_more": False, "next_offset": ""},
        }

    async def diff_refs(
        self,
        repository: str,
        left_ref: str,
        right_ref: str,
        amount: int = 1000,
        after: str = "",
    ) -> dict[str, Any]:
        left_snapshot = self._get_ref_snapshot(repository, left_ref)
        right_snapshot = self._get_ref_snapshot(repository, right_ref)
        all_paths = sorted(set(left_snapshot) | set(right_snapshot))
        results = []

        for path in all_paths:
            if path <= after:
                continue
            left_obj = left_snapshot.get(path)
            right_obj = right_snapshot.get(path)
            if left_obj is None and right_obj is not None:
                results.append(
                    {
                        "path": path,
                        "type": "added",
                        "path_type": "object",
                        "size_bytes": right_obj.size_bytes,
                        "checksum": right_obj.checksum,
                    }
                )
            elif right_obj is None and left_obj is not None:
                results.append(
                    {
                        "path": path,
                        "type": "removed",
                        "path_type": "object",
                        "size_bytes": left_obj.size_bytes,
                        "checksum": left_obj.checksum,
                    }
                )
            elif left_obj and right_obj and (
                left_obj.checksum != right_obj.checksum
                or left_obj.size_bytes != right_obj.size_bytes
            ):
                results.append(
                    {
                        "path": path,
                        "type": "changed",
                        "path_type": "object",
                        "size_bytes": right_obj.size_bytes,
                        "checksum": right_obj.checksum,
                    }
                )

        page = results[:amount]
        has_more = len(results) > amount
        next_offset = page[-1]["path"] if has_more and page else ""
        return {
            "results": page,
            "pagination": {"has_more": has_more, "next_offset": next_offset},
        }

    def export_snapshot(self) -> dict[str, Any]:
        repositories = {}
        for repo_name, repo in self.repositories.items():
            repositories[repo_name] = {
                "storage_namespace": repo["storage_namespace"],
                "next_commit": repo["next_commit"],
                "tags": copy.deepcopy(repo["tags"]),
                "branches": {
                    branch: {
                        "commit_id": branch_state["commit_id"],
                        "staging": {
                            path: obj.to_dict()
                            for path, obj in branch_state["staging"].items()
                        },
                    }
                    for branch, branch_state in repo["branches"].items()
                },
                "commits": {
                    commit_id: {
                        "id": commit["id"],
                        "message": commit["message"],
                        "creation_date": commit["creation_date"],
                        "metadata": copy.deepcopy(commit["metadata"]),
                        "parents": list(commit["parents"]),
                        "snapshot": {
                            path: obj.to_dict()
                            for path, obj in commit["snapshot"].items()
                        },
                    }
                    for commit_id, commit in repo["commits"].items()
                },
            }
        return {"repositories": repositories}

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        repositories = {}
        for repo_name, repo in snapshot.get("repositories", {}).items():
            repositories[repo_name] = {
                "storage_namespace": repo["storage_namespace"],
                "next_commit": repo["next_commit"],
                "tags": copy.deepcopy(repo["tags"]),
                "branches": {
                    branch: {
                        "commit_id": branch_state["commit_id"],
                        "staging": {
                            path: FakeLakeFSObject.from_dict(payload)
                            for path, payload in branch_state["staging"].items()
                        },
                    }
                    for branch, branch_state in repo["branches"].items()
                },
                "commits": {
                    commit_id: {
                        "id": commit["id"],
                        "message": commit["message"],
                        "creation_date": commit["creation_date"],
                        "metadata": copy.deepcopy(commit["metadata"]),
                        "parents": list(commit["parents"]),
                        "snapshot": {
                            path: FakeLakeFSObject.from_dict(payload)
                            for path, payload in commit["snapshot"].items()
                        },
                    }
                    for commit_id, commit in repo["commits"].items()
                },
            }
        self.repositories = repositories
