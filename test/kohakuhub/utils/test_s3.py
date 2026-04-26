"""Tests for S3 utility helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

import kohakuhub.utils.s3 as s3_module
from test.kohakuhub.support.fakes import FakeS3Client, FakeS3Service


@pytest.fixture(autouse=True)
def _configure_s3_settings(monkeypatch):
    monkeypatch.setattr(s3_module.cfg.s3, "endpoint", "https://internal-s3.local")
    monkeypatch.setattr(s3_module.cfg.s3, "public_endpoint", "https://public-s3.local")
    monkeypatch.setattr(s3_module.cfg.s3, "access_key", "access-key")
    monkeypatch.setattr(s3_module.cfg.s3, "secret_key", "secret-key")
    monkeypatch.setattr(s3_module.cfg.s3, "region", "us-east-1")
    monkeypatch.setattr(s3_module.cfg.s3, "bucket", "kohaku-test")
    monkeypatch.setattr(s3_module.cfg.s3, "signature_version", None)
    monkeypatch.setattr(s3_module.cfg.s3, "force_path_style", False)
    monkeypatch.setattr(s3_module.cfg.app, "lfs_multipart_threshold_bytes", 1024)
    monkeypatch.setattr(s3_module.cfg.app, "lfs_multipart_chunk_size_bytes", 256)
    monkeypatch.setattr(s3_module, "_presigned_url_process_pool", None)


def test_get_presigned_url_process_pool_is_cached(monkeypatch):
    registrations = []

    class FakePool:
        def __init__(self, max_workers: int):
            self.max_workers = max_workers

        def shutdown(self):
            return None

    monkeypatch.setattr(s3_module, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(s3_module.atexit, "register", lambda func: registrations.append(func))

    pool1 = s3_module.get_presigned_url_process_pool()
    pool2 = s3_module.get_presigned_url_process_pool()

    assert pool1 is pool2
    assert pool1.max_workers == 8
    assert registrations == [pool1.shutdown]


def test_generate_single_part_url_builds_client_and_rewrites_public_endpoint(monkeypatch):
    captured = {}

    class RecordingClient:
        def generate_presigned_url(self, operation_name, Params, ExpiresIn):
            captured["operation_name"] = operation_name
            captured["params"] = Params
            captured["expires_in"] = ExpiresIn
            return "https://internal-s3.local/bucket/object.bin?upload=1"

    monkeypatch.setattr(s3_module, "BotoConfig", lambda **kwargs: kwargs)

    def fake_boto3_client(service_name, **kwargs):
        captured["client_kwargs"] = kwargs
        return RecordingClient()

    monkeypatch.setattr(s3_module.boto3, "client", fake_boto3_client)

    payload = s3_module._generate_single_part_url(
        (
            "bucket",
            "object.bin",
            "upload-1",
            3,
            900,
            {
                "endpoint": "https://internal-s3.local",
                "public_endpoint": "https://public-s3.local",
                "access_key": "access",
                "secret_key": "secret",
                "region": "us-east-1",
                "signature_version": "s3v4",
                "force_path_style": True,
            },
        )
    )

    assert payload == {
        "part_number": 3,
        "url": "https://public-s3.local/bucket/object.bin?upload=1",
    }
    assert captured["operation_name"] == "upload_part"
    assert captured["params"]["UploadId"] == "upload-1"
    assert captured["client_kwargs"]["config"] == {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path"},
    }


def test_get_multipart_limits_read_current_config():
    assert s3_module.get_multipart_threshold() == 1024
    assert s3_module.get_multipart_chunk_size() == 256


def test_get_s3_client_supports_signature_version_and_path_style(monkeypatch):
    captured = {}
    monkeypatch.setattr(s3_module.cfg.s3, "endpoint", "https://r2.example.com/account/bucket")
    monkeypatch.setattr(s3_module.cfg.s3, "signature_version", "s3v4")
    monkeypatch.setattr(s3_module.cfg.s3, "force_path_style", True)
    monkeypatch.setattr(s3_module, "BotoConfig", lambda **kwargs: kwargs)

    def fake_boto3_client(service_name, **kwargs):
        captured["kwargs"] = kwargs
        return "client"

    monkeypatch.setattr(s3_module.boto3, "client", fake_boto3_client)

    client = s3_module.get_s3_client()

    assert client == "client"
    assert captured["kwargs"]["config"] == {
        "signature_version": "s3v4",
        "s3": {
            "addressing_style": "path",
            "use_accelerate_endpoint": False,
        },
    }


def test_get_s3_client_uses_default_signature_when_not_configured(monkeypatch):
    captured = {}
    monkeypatch.setattr(s3_module.cfg.s3, "endpoint", "https://s3.example.com")
    monkeypatch.setattr(s3_module.cfg.s3, "signature_version", None)
    monkeypatch.setattr(s3_module, "BotoConfig", lambda **kwargs: kwargs)

    def fake_boto3_client(service_name, **kwargs):
        captured["kwargs"] = kwargs
        return "client"

    monkeypatch.setattr(s3_module.boto3, "client", fake_boto3_client)

    s3_module.get_s3_client()

    assert captured["kwargs"]["config"] == {"s3": {}}


def test_init_storage_handles_existing_and_missing_buckets(monkeypatch):
    existing_service = FakeS3Service()
    existing_service.init_storage("kohaku-test")
    monkeypatch.setattr(s3_module, "get_s3_client", existing_service.get_client)
    s3_module.init_storage()

    class RecordingClient(FakeS3Client):
        def __init__(self, service):
            super().__init__(service)
            self.created = []

        def create_bucket(self, Bucket: str, CreateBucketConfiguration: dict | None = None) -> None:
            self.created.append((Bucket, CreateBucketConfiguration))
            super().create_bucket(Bucket=Bucket, CreateBucketConfiguration=CreateBucketConfiguration)

    missing_service = FakeS3Service()
    missing_client = RecordingClient(missing_service)
    monkeypatch.setattr(s3_module.cfg.s3, "region", "eu-west-1")
    monkeypatch.setattr(s3_module, "get_s3_client", lambda: missing_client)

    s3_module.init_storage()

    assert missing_client.created == [
        ("kohaku-test", {"LocationConstraint": "eu-west-1"})
    ]

    class DefaultRegionClient(RecordingClient):
        pass

    us_east_service = FakeS3Service()
    us_east_client = DefaultRegionClient(us_east_service)
    monkeypatch.setattr(s3_module.cfg.s3, "region", "us-east-1")
    monkeypatch.setattr(s3_module, "get_s3_client", lambda: us_east_client)

    s3_module.init_storage()

    assert us_east_client.created == [("kohaku-test", None)]


def test_init_storage_raises_on_non_404_or_create_failure(monkeypatch):
    class HeadFailureClient:
        def head_bucket(self, Bucket: str):
            error = RuntimeError("forbidden")
            error.response = {"Error": {"Code": "403"}}
            raise error

    monkeypatch.setattr(s3_module, "get_s3_client", lambda: HeadFailureClient())
    with pytest.raises(RuntimeError, match="forbidden"):
        s3_module.init_storage()

    class CreateFailureClient:
        def head_bucket(self, Bucket: str):
            error = RuntimeError("missing")
            error.response = {"Error": {"Code": "404"}}
            raise error

        def create_bucket(self, Bucket: str, CreateBucketConfiguration: dict | None = None):
            raise RuntimeError("cannot create")

    monkeypatch.setattr(s3_module, "get_s3_client", lambda: CreateFailureClient())
    with pytest.raises(RuntimeError, match="cannot create"):
        s3_module.init_storage()


def test_generate_download_and_upload_presigned_urls(monkeypatch):
    captured = {}

    class RecordingClient:
        def generate_presigned_url(self, operation_name, Params, ExpiresIn, HttpMethod=None):
            captured.setdefault("calls", []).append(
                {
                    "operation": operation_name,
                    "params": Params,
                    "expires_in": ExpiresIn,
                    "http_method": HttpMethod,
                }
            )
            return f"https://internal-s3.local/{Params['Bucket']}/{Params['Key']}?op={operation_name}"

    monkeypatch.setattr(s3_module, "get_s3_client", lambda: RecordingClient())

    download_url = s3_module._generate_download_presigned_url_sync(
        "bucket",
        "path/file.txt",
        expires_in=600,
        filename="file.txt",
    )
    upload_payload = s3_module._generate_upload_presigned_url_sync(
        "bucket",
        "path/file.txt",
        expires_in=300,
        content_type="text/plain",
    )

    assert download_url == "https://public-s3.local/bucket/path/file.txt?op=get_object"
    assert upload_payload["url"] == "https://public-s3.local/bucket/path/file.txt?op=put_object"
    assert upload_payload["method"] == "PUT"
    assert upload_payload["headers"] == {"Content-Type": "text/plain"}
    assert datetime.fromisoformat(upload_payload["expires_at"].replace("Z", "+00:00"))
    assert captured["calls"][0]["params"]["ResponseContentDisposition"] == 'attachment; filename="file.txt";'
    assert captured["calls"][1]["http_method"] == "PUT"
    assert captured["calls"][1]["params"]["ContentType"] == "text/plain"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wrapper_name", "expected_func_name", "args"),
    [
        ("generate_download_presigned_url", "_generate_download_presigned_url_sync", ("bucket", "file.bin", 600, None)),
        ("generate_upload_presigned_url", "_generate_upload_presigned_url_sync", ("bucket", "file.bin", 600, None, None)),
        ("generate_multipart_upload_urls", "_generate_multipart_upload_urls_sync", ("bucket", "file.bin", 2, None, 600)),
        ("complete_multipart_upload", "_complete_multipart_upload_sync", ("bucket", "file.bin", "upload-1", [])),
        ("abort_multipart_upload", "_abort_multipart_upload_sync", ("bucket", "file.bin", "upload-1")),
        ("get_object_metadata", "_get_object_metadata_sync", ("bucket", "file.bin")),
        ("object_exists", "_object_exists_sync", ("bucket", "file.bin")),
        ("delete_objects_with_prefix", "_delete_objects_with_prefix_sync", ("bucket", "prefix/")),
        ("copy_s3_folder", "_copy_s3_folder_sync", ("bucket", "from/", "to/", None)),
    ],
)
async def test_async_s3_wrappers_use_shared_executor(monkeypatch, wrapper_name, expected_func_name, args):
    captured = []

    async def fake_run(func, *run_args):
        captured.append((func.__name__, run_args))
        return {"wrapped": func.__name__}

    monkeypatch.setattr(s3_module, "run_in_s3_executor", fake_run)

    result = await getattr(s3_module, wrapper_name)(*args)

    assert captured == [(expected_func_name, args)]
    if wrapper_name == "abort_multipart_upload":
        assert result is None
    else:
        assert result == {"wrapped": expected_func_name}


def test_generate_multipart_upload_urls_supports_sequential_and_parallel_modes(monkeypatch):
    service = FakeS3Service()
    
    class RecordingMultipartClient(FakeS3Client):
        def generate_presigned_url(
            self,
            operation_name: str,
            Params: dict,
            ExpiresIn: int = 3600,
            HttpMethod: str | None = None,
        ) -> str:
            if operation_name == "upload_part":
                return f"https://internal-s3.local/{Params['Bucket']}/{Params['Key']}?part={Params['PartNumber']}"
            return super().generate_presigned_url(
                operation_name,
                Params=Params,
                ExpiresIn=ExpiresIn,
                HttpMethod=HttpMethod,
            )

    client = RecordingMultipartClient(service)
    monkeypatch.setattr(s3_module, "get_s3_client", lambda: client)

    sequential = s3_module._generate_multipart_upload_urls_sync("bucket", "big.bin", 2, expires_in=120)

    assert sequential["upload_id"]
    assert [item["part_number"] for item in sequential["part_urls"]] == [1, 2]
    assert all(url["url"].startswith("https://public-s3.local/") for url in sequential["part_urls"])
    assert datetime.fromisoformat(sequential["expires_at"].replace("Z", "+00:00"))

    @dataclass
    class FakePool:
        args_list: list[tuple] | None = None

        def map(self, func, args_list):
            self.args_list = list(args_list)
            return [
                {"part_number": index, "url": f"https://public-s3.local/upload/{index}"}
                for index in range(1, 12)
            ]

    fake_pool = FakePool()
    monkeypatch.setattr(s3_module, "get_presigned_url_process_pool", lambda: fake_pool)

    parallel = s3_module._generate_multipart_upload_urls_sync(
        "bucket",
        "huge.bin",
        11,
        upload_id="existing-upload",
        expires_in=90,
    )

    assert parallel["upload_id"] == "existing-upload"
    assert len(parallel["part_urls"]) == 11
    assert fake_pool.args_list is not None
    assert fake_pool.args_list[0][:4] == ("bucket", "huge.bin", "existing-upload", 1)


def test_complete_abort_metadata_and_exists_helpers(monkeypatch):
    service = FakeS3Service()
    client = service.get_client()
    monkeypatch.setattr(s3_module, "get_s3_client", lambda: client)

    upload = client.create_multipart_upload(Bucket="bucket", Key="big.bin")
    service.multipart_uploads[upload["UploadId"]]["parts"] = {1: b"part-a", 2: b"part-b"}

    response = s3_module._complete_multipart_upload_sync(
        "bucket",
        "big.bin",
        upload["UploadId"],
        [{"PartNumber": 1, "ETag": "etag-1"}],
    )
    assert response == {"Bucket": "bucket", "Key": "big.bin"}
    assert service.object_exists("bucket", "big.bin") is True

    upload_to_abort = client.create_multipart_upload(Bucket="bucket", Key="abort.bin")
    s3_module._abort_multipart_upload_sync("bucket", "abort.bin", upload_to_abort["UploadId"])
    assert upload_to_abort["UploadId"] not in service.multipart_uploads

    service.put_object(Bucket="bucket", Key="meta.txt", Body=b"hello", ContentType="text/plain")
    metadata = s3_module._get_object_metadata_sync("bucket", "meta.txt")
    assert metadata["size"] == 5
    assert metadata["content_type"] == "text/plain"
    assert s3_module._object_exists_sync("bucket", "meta.txt") is True
    assert s3_module._object_exists_sync("bucket", "missing.txt") is False


def test_parse_s3_uri_requires_expected_scheme():
    assert s3_module.parse_s3_uri("s3://bucket/path/file.bin") == ("bucket", "path/file.bin")
    assert s3_module.parse_s3_uri("s3://bucket") == ("bucket", "")

    with pytest.raises(ValueError, match="Invalid S3 URI"):
        s3_module.parse_s3_uri("https://bucket/path/file.bin")


def test_delete_objects_with_prefix_handles_success_empty_and_errors(monkeypatch):
    service = FakeS3Service()
    service.put_object(Bucket="bucket", Key="prefix/a.txt", Body=b"a")
    service.put_object(Bucket="bucket", Key="prefix/b.txt", Body=b"b")
    monkeypatch.setattr(s3_module, "get_s3_client", service.get_client)

    assert s3_module._delete_objects_with_prefix_sync("bucket", "prefix/") == 2
    assert s3_module._delete_objects_with_prefix_sync("bucket", "prefix/") == 0

    class BrokenClient:
        def get_paginator(self, operation_name: str):
            raise RuntimeError("list failed")

    monkeypatch.setattr(s3_module, "get_s3_client", lambda: BrokenClient())
    assert s3_module._delete_objects_with_prefix_sync("bucket", "prefix/") == 0


def test_copy_s3_folder_supports_exclusions_and_partial_failures(monkeypatch):
    service = FakeS3Service()
    service.put_object(Bucket="bucket", Key="from/a.txt", Body=b"a")
    service.put_object(Bucket="bucket", Key="from/_lakefs/skip.txt", Body=b"skip")
    service.put_object(Bucket="bucket", Key="from/fail.txt", Body=b"fail")

    class CopyClient(FakeS3Client):
        def copy_object(self, Bucket: str, CopySource: dict[str, str], Key: str):
            if CopySource["Key"].endswith("fail.txt"):
                raise RuntimeError("copy failed")
            return super().copy_object(Bucket=Bucket, CopySource=CopySource, Key=Key)

    monkeypatch.setattr(s3_module, "get_s3_client", lambda: CopyClient(service))

    copied = s3_module._copy_s3_folder_sync(
        "bucket",
        "from/",
        "to/",
        exclude_prefix="_lakefs/",
    )

    assert copied == 1
    assert service.object_exists("bucket", "to/a.txt") is True
    assert service.object_exists("bucket", "to/_lakefs/skip.txt") is False
    assert service.object_exists("bucket", "to/fail.txt") is False

    class MismatchPaginatorClient(CopyClient):
        def get_paginator(self, operation_name: str):
            class Paginator:
                def paginate(self, Bucket: str, Prefix: str = ""):
                    yield {"Contents": [{"Key": "mismatch.txt"}]}

            return Paginator()

    monkeypatch.setattr(s3_module, "get_s3_client", lambda: MismatchPaginatorClient(service))
    assert s3_module._copy_s3_folder_sync("bucket", "from/", "to/") == 0
