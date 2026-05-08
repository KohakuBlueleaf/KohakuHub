"""Health probes for the admin dependency dashboard.

Each probe verifies a single external dependency with a tight timeout and
returns a small dict describing the result. Probes never raise: any failure
is captured as a ``down`` entry so the aggregator can surface partial
outages without one slow component blocking the rest.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import smtplib
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from kohakuhub.config import cfg
from kohakuhub.db import db
from kohakuhub.logger import get_logger
from kohakuhub.utils.s3 import get_s3_client

logger = get_logger("ADMIN")

DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0

# PostgreSQL "version()" output is verbose ("PostgreSQL 15.5 on x86_64-pc-linux-gnu, ...").
# We only surface the leading name + numeric version to keep the UI compact.
_PG_VERSION_PATTERN = re.compile(r"^(PostgreSQL\s+\d+(?:\.\d+)*)")


def _strip_password(url: str) -> str:
    """Return ``url`` with any embedded password removed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.password:
        return url

    user = parts.username or ""
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{user}@{host}" if user else host
    return urlunsplit(parts._replace(netloc=netloc))


def _short_pg_version(raw: str) -> str:
    match = _PG_VERSION_PATTERN.match(raw)
    return match.group(1) if match else raw[:80]


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _ok(
    name: str,
    *,
    start: float,
    version: str | None,
    endpoint: str | None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "ok",
        "latency_ms": _ms_since(start),
        "version": version,
        "endpoint": endpoint,
        "detail": detail,
    }


def _down(
    name: str,
    *,
    endpoint: str | None,
    detail: str,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "down",
        "latency_ms": latency_ms,
        "version": None,
        "endpoint": endpoint,
        "detail": detail,
    }


def _disabled(name: str, *, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "disabled",
        "latency_ms": None,
        "version": None,
        "endpoint": None,
        "detail": detail,
    }


def _query_postgres_version() -> str | None:
    """Run a liveness query plus a backend-aware version lookup."""
    db.execute_sql("SELECT 1").fetchone()
    if cfg.app.db_backend == "sqlite":
        row = db.execute_sql("SELECT sqlite_version()").fetchone()
        return f"SQLite {row[0]}" if row and row[0] else None
    row = db.execute_sql("SELECT version()").fetchone()
    if row and row[0]:
        return _short_pg_version(row[0])
    return None


async def probe_postgres(
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe the configured database backend."""
    start = time.perf_counter()
    endpoint = _strip_password(cfg.app.database_url)
    try:
        version = await asyncio.wait_for(
            asyncio.to_thread(_query_postgres_version),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _down(
            "postgres",
            endpoint=endpoint,
            detail=f"timeout after {int(timeout * 1000)}ms",
            latency_ms=_ms_since(start),
        )
    except Exception as exc:
        logger.warning(f"postgres probe failed: {exc}")
        return _down(
            "postgres",
            endpoint=endpoint,
            detail=str(exc) or exc.__class__.__name__,
            latency_ms=_ms_since(start),
        )

    return _ok(
        "postgres",
        start=start,
        version=version,
        endpoint=endpoint,
    )


def _list_buckets_sync() -> str | None:
    s3 = get_s3_client()
    response = s3.list_buckets()
    server = (
        response.get("ResponseMetadata", {})
        .get("HTTPHeaders", {})
        .get("server")
    )
    return server


def _sign_minio_admin_get(
    *,
    endpoint: str,
    path: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> tuple[str, dict[str, str]]:
    """Build a signed AWS SigV4 GET request for the MinIO admin API.

    The MinIO admin API uses the same SigV4 service name ("s3") as the
    S3 data plane, so this implementation matches what `minio-py`'s
    ``MinioAdmin`` produces (see ``minio/signer.py:sign_v4_s3``). Signing
    is implemented inline with stdlib ``hmac`` / ``hashlib`` to avoid a
    new runtime dependency.

    Compatible with MinIO admin API v3 (RELEASE.2019-* and newer); earlier
    server releases never exposed ``/minio/admin/v3/*`` and are not
    supported here.
    """
    parts = urlsplit(endpoint)
    host = parts.netloc or parts.path
    scheme = parts.scheme or "http"
    url = f"{scheme}://{host}{path}"

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    content_sha = hashlib.sha256(b"").hexdigest()

    signed_headers_map = {
        "content-type": "application/octet-stream",
        "host": host,
        "x-amz-content-sha256": content_sha,
        "x-amz-date": amz_date,
    }
    signed_header_keys = sorted(signed_headers_map.keys())
    canonical_headers = "".join(
        f"{key}:{signed_headers_map[key]}\n" for key in signed_header_keys
    )
    signed_headers = ";".join(signed_header_keys)
    canonical_request = (
        f"GET\n{path}\n\n{canonical_headers}\n{signed_headers}\n{content_sha}"
    )

    scope = f"{short_date}/{region}/s3/aws4_request"
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{amz_date}\n"
        f"{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret_key).encode(), short_date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, "s3")
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(
        k_signing, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Host": host,
        "Content-Type": "application/octet-stream",
        "x-amz-content-sha256": content_sha,
        "x-amz-date": amz_date,
        "Authorization": authorization,
    }
    return url, headers


def _extract_minio_release(payload: Any) -> str | None:
    """Pull a release tag out of a MinIO ``/admin/v3/info`` payload.

    The response shape has shifted across MinIO releases: pre-2020 builds
    surfaced ``mode`` and ``deploymentID`` only, the 2020-2021 line added
    ``servers[].version`` plus ``servers[].commitID``, and modern builds
    additionally carry ``servers[].edition``. Look in every documented spot
    so the probe stays stable across upgrades, and return ``None`` when
    none of them are present (e.g. on AWS / R2 / Ceph endpoints that 403
    or 404 the admin path).
    """
    if not isinstance(payload, dict):
        return None
    servers = payload.get("servers") or payload.get("Servers")
    if isinstance(servers, list) and servers:
        first = servers[0] if isinstance(servers[0], dict) else None
        if first:
            for key in ("version", "Version", "build", "Build"):
                value = first.get(key)
                if value:
                    return str(value)
    for key in ("version", "Version"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


async def _fetch_minio_admin_version(timeout: float) -> str | None:
    """Best-effort lookup of the running MinIO release via the admin API.

    Returns the server-reported release tag (e.g. ``2025-09-07T16:13:09Z``)
    or ``None`` for non-MinIO S3 endpoints, auth failures, version
    mismatches, or unexpected payloads. The caller decides whether to fall
    back to the ``Server`` header from the data-plane response.
    """
    region = cfg.s3.region or "us-east-1"
    try:
        url, headers = _sign_minio_admin_get(
            endpoint=cfg.s3.endpoint,
            path="/minio/admin/v3/info",
            access_key=cfg.s3.access_key,
            secret_key=cfg.s3.secret_key,
            region=region,
        )
    except Exception as exc:
        logger.debug(f"minio admin signing skipped: {exc}")
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug(f"minio admin call failed: {exc}")
        return None

    if response.status_code != 200:
        # Non-MinIO endpoints return 403/404 here; older MinIO returns 426
        # when the admin API version doesn't match. All non-fatal — we
        # simply skip the lookup and let the Server header speak.
        logger.debug(
            f"minio admin call returned {response.status_code}: {response.text[:120]}"
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        return None
    return _extract_minio_release(payload)


async def probe_minio(
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe S3 / MinIO via list_buckets, with a best-effort version lookup.

    The S3 ``list_buckets`` call doubles as a liveness check and gives us the
    ``Server`` header (e.g. ``MinIO``). When the backend identifies itself as
    MinIO we additionally hit ``/minio/admin/v3/info`` to surface the actual
    release tag; non-MinIO endpoints (AWS, R2, …) keep the header value as
    their version string.
    """
    start = time.perf_counter()
    endpoint = cfg.s3.endpoint
    try:
        server = await asyncio.wait_for(
            asyncio.to_thread(_list_buckets_sync),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _down(
            "minio",
            endpoint=endpoint,
            detail=f"timeout after {int(timeout * 1000)}ms",
            latency_ms=_ms_since(start),
        )
    except Exception as exc:
        logger.warning(f"minio probe failed: {exc}")
        return _down(
            "minio",
            endpoint=endpoint,
            detail=str(exc) or exc.__class__.__name__,
            latency_ms=_ms_since(start),
        )

    version: str | None = server
    if server and server.lower().startswith("minio"):
        release = await _fetch_minio_admin_version(timeout=timeout)
        if release:
            version = f"MinIO {release}"

    return _ok(
        "minio",
        start=start,
        version=version,
        endpoint=endpoint,
    )


async def probe_lakefs(
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe LakeFS via /healthcheck plus an optional version lookup.

    The healthcheck endpoint is unauthenticated and authoritative for liveness;
    the version endpoint requires Basic Auth. If the version call fails but
    healthcheck succeeded, the probe still reports ``ok`` with an unknown
    version so a misconfigured admin token does not mask a healthy LakeFS.
    """
    start = time.perf_counter()
    endpoint = cfg.lakefs.endpoint.rstrip("/")
    base = f"{endpoint}/api/v1"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            health = await client.get(f"{base}/healthcheck")
            if not health.is_success:
                return _down(
                    "lakefs",
                    endpoint=endpoint,
                    detail=(
                        f"healthcheck returned "
                        f"{health.status_code} {health.reason_phrase}"
                    ),
                    latency_ms=_ms_since(start),
                )

            version: str | None = None
            try:
                version_resp = await client.get(
                    f"{base}/config/version",
                    auth=(cfg.lakefs.access_key, cfg.lakefs.secret_key),
                )
                if version_resp.is_success:
                    payload = version_resp.json()
                    version = (
                        payload.get("version")
                        or payload.get("Version")
                        or None
                    )
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug(f"lakefs version lookup skipped: {exc}")
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        return _down(
            "lakefs",
            endpoint=endpoint,
            detail=str(exc) or exc.__class__.__name__,
            latency_ms=_ms_since(start),
        )

    return _ok(
        "lakefs",
        start=start,
        version=version,
        endpoint=endpoint,
    )


def _smtp_probe_sync(timeout: float) -> str | None:
    """Open a TCP+EHLO session against the configured SMTP host."""
    smtp = smtplib.SMTP(
        host=cfg.smtp.host,
        port=cfg.smtp.port,
        timeout=timeout,
    )
    try:
        code, response = smtp.ehlo()
        text: str | None
        if isinstance(response, bytes):
            text = response.decode("utf-8", errors="replace")
        else:
            text = str(response) if response is not None else None
        if text:
            text = text.splitlines()[0].strip()[:120] or None
        if code and code >= 400:
            raise smtplib.SMTPResponseException(code, text or "")
        return text
    finally:
        try:
            smtp.quit()
        except smtplib.SMTPException:
            smtp.close()


async def probe_smtp(
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe SMTP only when ``smtp.enabled`` is true.

    The probe is intentionally TCP+EHLO only — it never authenticates or sends
    mail, so it cannot trigger lockouts on misconfigured providers.
    """
    if not cfg.smtp.enabled:
        return _disabled(
            "smtp",
            detail="SMTP is disabled in configuration (smtp.enabled = false)",
        )

    start = time.perf_counter()
    endpoint = f"{cfg.smtp.host}:{cfg.smtp.port}"
    try:
        banner = await asyncio.wait_for(
            asyncio.to_thread(_smtp_probe_sync, timeout),
            timeout=timeout + 0.5,
        )
    except asyncio.TimeoutError:
        return _down(
            "smtp",
            endpoint=endpoint,
            detail=f"timeout after {int(timeout * 1000)}ms",
            latency_ms=_ms_since(start),
        )
    except Exception as exc:
        logger.warning(f"smtp probe failed: {exc}")
        return _down(
            "smtp",
            endpoint=endpoint,
            detail=str(exc) or exc.__class__.__name__,
            latency_ms=_ms_since(start),
        )

    return _ok(
        "smtp",
        start=start,
        version=banner,
        endpoint=endpoint,
    )


async def probe_redis(
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe the L2 cache (Redis-protocol; works with Valkey too).

    The probe is a single PING + INFO server, with a tight timeout so a
    flaky cache never blocks the dependency dashboard. The cache itself
    is allowed to fail silently (the API stays correct without it), but
    operators still want a glanceable signal here when they suspect the
    cache is the cause of a latency spike.

    Surfaces the underlying server identity in ``version`` ("Valkey
    8.0.1" vs "Redis 7.4.0") because that's the one place the admin UI
    distinguishes them — everywhere else we call it "Redis" since the
    protocol is what matters operationally.
    """
    if not cfg.cache.enabled:
        return _disabled(
            "redis",
            detail="Cache is disabled in configuration (cache.enabled = false)",
        )

    endpoint = _strip_password(cfg.cache.url)
    start = time.perf_counter()

    # Local import keeps the cache layer optional for downstream consumers
    # that vendor this codebase without redis-py installed.
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        cfg.cache.url,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        decode_responses=True,
    )
    try:
        try:
            pong = await asyncio.wait_for(client.ping(), timeout=timeout)
        except asyncio.TimeoutError:
            return _down(
                "redis",
                endpoint=endpoint,
                detail=f"timeout after {int(timeout * 1000)}ms",
                latency_ms=_ms_since(start),
            )
        except Exception as exc:
            logger.warning(f"redis probe failed (PING): {exc}")
            return _down(
                "redis",
                endpoint=endpoint,
                detail=str(exc) or exc.__class__.__name__,
                latency_ms=_ms_since(start),
            )

        if not pong:
            return _down(
                "redis",
                endpoint=endpoint,
                detail="server did not return PONG",
                latency_ms=_ms_since(start),
            )

        try:
            info = await asyncio.wait_for(
                client.info("server"), timeout=timeout
            )
        except Exception as exc:
            # Server is reachable but INFO failed — still ``ok``, just
            # without version metadata. Surface the cause in detail.
            return _ok(
                "redis",
                start=start,
                version=None,
                endpoint=endpoint,
                detail=f"INFO server failed: {exc}",
            )

        if isinstance(info, dict):
            redis_version = info.get("redis_version")
            # Valkey >= 7.2 advertises itself in ``server_name`` (e.g. "valkey").
            # Pre-rename Valkey forks and Redis itself report ``redis_version``
            # only — fall back to that when ``server_name`` is missing.
            server_name = info.get("server_name") or "redis"
            label = "Valkey" if "valkey" in str(server_name).lower() else "Redis"
            version = f"{label} {redis_version}" if redis_version else label
        else:
            version = None

        return _ok(
            "redis",
            start=start,
            version=version,
            endpoint=endpoint,
        )
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


PROBES = (
    probe_postgres,
    probe_minio,
    probe_lakefs,
    probe_redis,
    probe_smtp,
)


async def run_all_probes(
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Run every probe concurrently and return their results in stable order."""
    results = await asyncio.gather(
        *(probe(timeout) for probe in PROBES),
        return_exceptions=False,
    )
    return list(results)
