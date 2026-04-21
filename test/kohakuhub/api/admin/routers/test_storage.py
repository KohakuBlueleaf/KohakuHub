"""Unit tests for admin storage routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.api.admin.routers.storage as storage_router


class _FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.pages)


class _FakeS3:
    def __init__(
        self,
        *,
        head_bucket_result=None,
        head_bucket_error=None,
        list_objects_results=None,
        list_objects_error_indexes=None,
        list_buckets_result=None,
        list_buckets_error=None,
        paginator_pages=None,
        delete_objects_result=None,
    ):
        self.head_bucket_result = head_bucket_result or {"ResponseMetadata": {"RequestId": "req-1"}}
        self.head_bucket_error = head_bucket_error
        self.list_objects_results = list_objects_results or []
        self.list_objects_error_indexes = set(list_objects_error_indexes or [])
        self.list_buckets_result = list_buckets_result
        self.list_buckets_error = list_buckets_error
        self.paginator = _FakePaginator(paginator_pages or [])
        self.delete_objects_result = delete_objects_result or {"Deleted": []}
        self.list_calls = []
        self.deleted = []
        self.deleted_batches = []

    def head_bucket(self, **kwargs):
        if self.head_bucket_error:
            raise self.head_bucket_error
        return self.head_bucket_result

    def list_objects_v2(self, **kwargs):
        call_index = len(self.list_calls)
        self.list_calls.append(kwargs)
        if call_index in self.list_objects_error_indexes:
            raise RuntimeError(f"list failure {call_index}")
        return self.list_objects_results[call_index]

    def list_buckets(self):
        if self.list_buckets_error:
            raise self.list_buckets_error
        return self.list_buckets_result

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self.paginator

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)

    def delete_objects(self, **kwargs):
        self.deleted_batches.append(kwargs)
        return self.delete_objects_result


async def _run_sync(callable_obj):
    return callable_obj()


def _set_standard_s3(monkeypatch):
    monkeypatch.setattr(storage_router.cfg.s3, "endpoint", "https://minio.example.com")
    monkeypatch.setattr(storage_router.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(storage_router.cfg.s3, "region", "us-east-1")
    monkeypatch.setattr(storage_router.cfg.s3, "force_path_style", True)
    monkeypatch.setattr(storage_router.cfg.s3, "access_key", "key")
    monkeypatch.setattr(storage_router.cfg.s3, "secret_key", "secret")


@pytest.mark.asyncio
async def test_debug_s3_config_reports_success_and_failures(monkeypatch):
    fake_s3 = _FakeS3(
        list_objects_results=[
            {"Contents": [{"Key": "a.txt"}], "CommonPrefixes": [], "KeyCount": 1},
            {},
            {"Contents": [], "CommonPrefixes": [{"Prefix": "folder/"}], "KeyCount": 0},
        ],
        list_objects_error_indexes={1},
    )
    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router, "get_s3_client", lambda: fake_s3)
    _set_standard_s3(monkeypatch)

    result = await storage_router.debug_s3_config()

    assert result["bucket_accessible"] is True
    assert result["test_Standard"]["success"] is True
    assert result["test_With Delimiter"]["success"] is False
    assert "list failure 1" in result["test_With Delimiter"]["error"]


@pytest.mark.asyncio
async def test_list_s3_buckets_falls_back_when_listing_is_unsupported(monkeypatch):
    fake_s3 = _FakeS3(list_buckets_error=RuntimeError("unsupported"))
    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router, "get_s3_client", lambda: fake_s3)
    _set_standard_s3(monkeypatch)

    result = await storage_router.list_s3_buckets()

    assert result["buckets"][0]["name"] == "hub-storage"
    assert "not supported" in result["buckets"][0]["note"]


@pytest.mark.asyncio
async def test_list_s3_buckets_aggregates_sizes_and_handles_per_bucket_errors(monkeypatch):
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    fake_s3 = _FakeS3(
        list_buckets_result={
            "Buckets": [
                {"Name": "good-bucket", "CreationDate": created},
                {"Name": "bad-bucket", "CreationDate": created},
            ]
        },
        paginator_pages=[
            {"Contents": [{"Size": 3}, {"Size": 7}]},
            RuntimeError("boom"),
        ],
    )

    def fake_paginate(**kwargs):
        if kwargs["Bucket"] == "good-bucket":
            return [{"Contents": [{"Size": 3}, {"Size": 7}]}]
        raise RuntimeError("boom")

    fake_s3.paginator.paginate = fake_paginate
    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router, "get_s3_client", lambda: fake_s3)
    _set_standard_s3(monkeypatch)

    result = await storage_router.list_s3_buckets()

    good, bad = result["buckets"]
    assert good["total_size"] == 10
    assert good["object_count"] == 2
    assert bad["total_size"] == 0
    assert "boom" in bad["error"]


@pytest.mark.asyncio
async def test_list_s3_objects_supports_standard_endpoint(monkeypatch):
    fake_s3 = _FakeS3(
        list_objects_results=[
            {
                "Contents": [
                    {
                        "Key": "models/a.bin",
                        "Size": 12,
                        "LastModified": datetime(2025, 1, 1, tzinfo=timezone.utc),
                        "StorageClass": "STANDARD",
                    }
                ],
                "IsTruncated": False,
            }
        ]
    )
    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router, "get_s3_client", lambda: fake_s3)
    _set_standard_s3(monkeypatch)

    result = await storage_router.list_s3_objects(prefix="models", limit=5)

    assert result["bucket"] == "hub-storage"
    assert result["objects"][0]["key"] == "models/a.bin"
    assert fake_s3.list_calls[0]["Prefix"] == "models"


@pytest.mark.asyncio
async def test_list_s3_objects_supports_r2_style_endpoints(monkeypatch):
    fake_s3 = _FakeS3(
        list_objects_results=[
                {
                    "Contents": [
                        {
                            "Key": "hub-storage/models/a.bin",
                            "Size": 12,
                            "LastModified": datetime(2025, 1, 1, tzinfo=timezone.utc),
                        }
                    ],
                "IsTruncated": False,
                "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "req"},
            }
        ]
    )
    seen = {}

    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router.cfg.s3, "endpoint", "https://r2.example.com/r2-root")
    monkeypatch.setattr(storage_router.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(storage_router.cfg.s3, "region", "auto")
    monkeypatch.setattr(storage_router.cfg.s3, "force_path_style", True)
    monkeypatch.setattr(storage_router.cfg.s3, "access_key", "key")
    monkeypatch.setattr(storage_router.cfg.s3, "secret_key", "secret")
    def fake_boto_client(service_name, **kwargs):
        seen["client_kwargs"] = kwargs
        return fake_s3

    monkeypatch.setattr(storage_router.boto3, "client", fake_boto_client)

    result = await storage_router.list_s3_objects(prefix="models", limit=5)

    assert seen["client_kwargs"]["endpoint_url"] == "https://r2.example.com"
    assert result["objects"][0]["key"] == "models/a.bin"
    assert result["objects"][0]["full_key"] == "hub-storage/models/a.bin"


@pytest.mark.asyncio
async def test_delete_object_and_prepare_delete_prefix(monkeypatch):
    fake_s3 = _FakeS3()
    token = SimpleNamespace(token="confirm-1")
    seen = {}

    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router, "get_s3_client", lambda: fake_s3)
    def fake_create_confirmation_token(**kwargs):
        seen["token_kwargs"] = kwargs
        return token

    monkeypatch.setattr(storage_router, "create_confirmation_token", fake_create_confirmation_token)
    monkeypatch.setattr(storage_router, "cleanup_expired_confirmation_tokens", lambda: seen.setdefault("cleanup", True))
    _set_standard_s3(monkeypatch)
    fake_s3.paginator.paginate = lambda **kwargs: [
        {"Contents": [{"Key": "models/a.bin"}, {"Key": "models/b.bin"}]}
    ]

    delete_result = await storage_router.delete_s3_object("models/a.bin")
    prepare_result = await storage_router.prepare_delete_prefix("models")

    assert delete_result["success"] is True
    assert fake_s3.deleted == [{"Bucket": "hub-storage", "Key": "models/a.bin"}]
    assert prepare_result["estimated_objects"] == 2
    assert seen["token_kwargs"]["action_data"]["actual_prefix"] == "models"


@pytest.mark.asyncio
async def test_prepare_delete_prefix_supports_r2_endpoints(monkeypatch):
    fake_s3 = _FakeS3()
    token = SimpleNamespace(token="confirm-2")
    seen = {}

    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router.cfg.s3, "endpoint", "https://r2.example.com/base-prefix")
    monkeypatch.setattr(storage_router.cfg.s3, "bucket", "hub-storage")
    monkeypatch.setattr(storage_router.cfg.s3, "region", "auto")
    monkeypatch.setattr(storage_router.cfg.s3, "force_path_style", True)
    monkeypatch.setattr(storage_router.cfg.s3, "access_key", "key")
    monkeypatch.setattr(storage_router.cfg.s3, "secret_key", "secret")
    def fake_boto_client(service_name, **kwargs):
        seen["client_kwargs"] = kwargs
        return fake_s3

    def fake_create_confirmation_token(**kwargs):
        seen["token_kwargs"] = kwargs
        return token

    monkeypatch.setattr(storage_router.boto3, "client", fake_boto_client)
    monkeypatch.setattr(storage_router, "create_confirmation_token", fake_create_confirmation_token)
    monkeypatch.setattr(storage_router, "cleanup_expired_confirmation_tokens", lambda: None)
    fake_s3.paginator.paginate = lambda **kwargs: [{"Contents": [{"Key": "base-prefix/hub-storage/models/a.bin"}]}]

    result = await storage_router.prepare_delete_prefix("models")

    assert result["estimated_objects"] == 1
    assert seen["client_kwargs"]["endpoint_url"] == "https://r2.example.com"
    assert seen["token_kwargs"]["action_data"]["actual_bucket"] == "base-prefix"
    assert seen["token_kwargs"]["action_data"]["actual_prefix"] == "hub-storage/models"


@pytest.mark.asyncio
async def test_delete_s3_prefix_validates_tokens_and_deletes_batches(monkeypatch):
    fake_s3 = _FakeS3(delete_objects_result={"Deleted": [{"Key": "models/a.bin"}], "Errors": [{"Key": "models/b.bin", "Message": "denied"}]})
    warnings = []

    monkeypatch.setattr(storage_router, "run_in_s3_executor", _run_sync)
    monkeypatch.setattr(storage_router, "get_s3_client", lambda: fake_s3)
    monkeypatch.setattr(storage_router.logger, "warning", lambda message: warnings.append(message))
    _set_standard_s3(monkeypatch)
    fake_s3.paginator.paginate = lambda **kwargs: [
        {"Contents": [{"Key": "models/a.bin"}, {"Key": "models/b.bin"}]}
    ]

    monkeypatch.setattr(storage_router, "consume_confirmation_token", lambda token: None)
    with pytest.raises(HTTPException) as invalid:
        await storage_router.delete_s3_prefix("models", "bad-token")

    assert invalid.value.status_code == 400

    monkeypatch.setattr(
        storage_router,
        "consume_confirmation_token",
        lambda token: {
            "display_prefix": "other",
            "actual_prefix": "models",
            "actual_bucket": "hub-storage",
        },
    )
    with pytest.raises(HTTPException) as mismatch:
        await storage_router.delete_s3_prefix("models", "bad-token")

    assert mismatch.value.status_code == 400

    monkeypatch.setattr(
        storage_router,
        "consume_confirmation_token",
        lambda token: {
            "display_prefix": "models",
            "actual_prefix": "models",
            "actual_bucket": "hub-storage",
        },
    )
    result = await storage_router.delete_s3_prefix("models", "good-token")

    assert result == {"success": True, "deleted_count": 1, "prefix": "models"}
    assert fake_s3.deleted_batches[0]["Delete"]["Objects"] == [
        {"Key": "models/a.bin"},
        {"Key": "models/b.bin"},
    ]
    assert any("Failed to delete models/b.bin: denied" in warning for warning in warnings)
