"""
File parsers for various dataset formats.

All parsers accept URLs (including S3 presigned URLs) and stream data efficiently.
"""

import asyncio
import base64
import tarfile
from datetime import date, datetime, time
from typing import Any, Optional

import duckdb
import httpx
from fsspec.implementations.http import HTTPFileSystem

from kohakuhub.config import cfg
from kohakuhub.datasetviewer.logger import get_logger

logger = get_logger("Parser")


async def resolve_url_redirects(url: str, auth_headers: dict[str, str] = None) -> str:
    """
    Resolve URL redirects by following 302 responses with authentication.

    For /resolve URLs that return 302, we need to follow the redirect
    and use the final S3 presigned URL for fsspec/DuckDB.

    Uses HEAD with manual redirect handling to get Location header
    without downloading file content.

    Args:
        url: Original URL (e.g., /datasets/.../resolve/main/file.csv or http://localhost:5173/datasets/...)
        auth_headers: Optional auth headers (Authorization, Cookie) from user request

    Returns:
        Final S3 presigned URL after following redirects (or original if external URL)
    """
    # Extract path from URL
    if url.startswith("http://") or url.startswith("https://"):
        # Parse URL to get path component
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path
    else:
        # Already a path
        path = url

    # Check if this is a /resolve path (internal)
    # Pattern: /{repo_type}s/{namespace}/{name}/resolve/{revision}/{file_path}
    if "/resolve/" not in path:
        # Not a resolve URL, return as-is (external URL or S3 presigned URL)
        return url

    # This is a resolve path - make authenticated request to backend
    try:
        # Use an internal backend URL when one is configured for local reverse-proxy setups.
        internal_base_url = (cfg.app.internal_base_url or cfg.app.base_url).rstrip("/")
        backend_url = f"{internal_base_url}{path}"

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            # Send GET request with auth headers to get redirect
            # Use stream() to only read headers, not content
            headers = auth_headers or {}
            async with client.stream("GET", backend_url, headers=headers) as response:
                # Check for any 3xx redirect with Location header
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if location:
                        logger.debug(
                            f"Resolved /resolve path: {path[:60]}... -> S3 presigned URL"
                        )
                        return location

                # For other status codes, log warning and return original
                logger.warning(
                    f"Expected redirect for {path}, got {response.status_code}"
                )
                return url

    except Exception as e:
        # If request fails, fall back to original URL
        logger.error(f"Could not resolve /resolve path {path[:60]}...: {e}")
        return url


class ParserError(Exception):
    """Base exception for parser errors."""

    pass


def make_json_safe(value: Any) -> Any:
    """Convert parser output values into JSON-safe primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return {
            "__type__": "bytes",
            "encoding": "base64",
            "size": len(value),
            "data": base64.b64encode(value).decode("ascii"),
        }

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, (list, tuple)):
        return [make_json_safe(item) for item in value]

    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}

    return str(value)


class CSVParser:
    """Parse CSV files using DuckDB (non-blocking, efficient)."""

    @staticmethod
    def _parse_sync(url: str, max_rows: int, delimiter: str) -> dict[str, Any]:
        """Synchronous CSV parsing with DuckDB (runs in thread pool)."""
        conn = duckdb.connect(":memory:")
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")

        # Read CSV with DuckDB
        query = f"""
        SELECT * FROM read_csv(
            '{url}',
            delim='{delimiter}',
            header=true,
            auto_detect=true
        )
        LIMIT {max_rows}
        """

        result = conn.execute(query).fetchall()
        columns = [desc[0] for desc in conn.description]

        # Get total row count
        try:
            count_query = f"SELECT COUNT(*) FROM read_csv('{url}', delim='{delimiter}', header=true)"
            total_rows = conn.execute(count_query).fetchone()[0]
        except Exception:
            total_rows = len(result)

        conn.close()

        return {
            "columns": columns,
            "rows": [[make_json_safe(value) for value in row] for row in result],
            "total_rows": total_rows,
            "truncated": len(result) >= max_rows,
            "file_size": None,
        }

    @staticmethod
    async def parse(
        url: str,
        max_rows: int = 1000,
        delimiter: str = ",",
        auth_headers: dict[str, str] = None,
    ) -> dict[str, Any]:
        """
        Parse CSV file from URL using DuckDB.

        Args:
            url: File URL (internal /resolve path or S3 presigned URL)
            max_rows: Maximum rows to return
            delimiter: CSV delimiter
            auth_headers: Optional auth headers for internal /resolve URLs

        Returns:
            Dict with columns, rows, total_rows, truncated, file_size
        """
        # Resolve redirects first (handles internal /resolve URLs)
        resolved_url = await resolve_url_redirects(url, auth_headers)

        # Run in thread pool to avoid blocking event loop
        return await asyncio.to_thread(
            CSVParser._parse_sync, resolved_url, max_rows, delimiter
        )


class JSONLParser:
    """Parse JSONL files using DuckDB (non-blocking, efficient)."""

    @staticmethod
    def _parse_sync(url: str, max_rows: int) -> dict[str, Any]:
        """Synchronous JSONL parsing with DuckDB (runs in thread pool)."""
        conn = duckdb.connect(":memory:")
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute("INSTALL json")
        conn.execute("LOAD json")

        # Read JSONL with DuckDB (newline-delimited JSON)
        query = f"SELECT * FROM read_ndjson('{url}') LIMIT {max_rows}"

        result = conn.execute(query).fetchall()
        columns = [desc[0] for desc in conn.description]

        # Get total row count
        try:
            count_query = f"SELECT COUNT(*) FROM read_ndjson('{url}')"
            total_rows = conn.execute(count_query).fetchone()[0]
        except Exception:
            total_rows = len(result)

        conn.close()

        return {
            "columns": columns,
            "rows": [[make_json_safe(value) for value in row] for row in result],
            "total_rows": total_rows,
            "truncated": len(result) >= max_rows,
            "file_size": None,
        }

    @staticmethod
    async def parse(
        url: str, max_rows: int = 1000, auth_headers: dict[str, str] = None
    ) -> dict[str, Any]:
        """
        Parse JSONL file from URL using DuckDB.

        Args:
            url: File URL (internal /resolve path or S3 presigned URL)
            max_rows: Maximum rows to return
            auth_headers: Optional auth headers for internal /resolve URLs

        Returns:
            Dict with columns, rows, total_rows, truncated, file_size
        """
        # Resolve redirects first (handles internal /resolve URLs)
        resolved_url = await resolve_url_redirects(url, auth_headers)

        # Run in thread pool to avoid blocking event loop
        return await asyncio.to_thread(JSONLParser._parse_sync, resolved_url, max_rows)


class JSONParser:
    """Parse JSON array files using DuckDB (non-blocking)."""

    @staticmethod
    def _parse_sync(url: str, max_rows: int) -> dict[str, Any]:
        """Synchronous JSON parsing with DuckDB (runs in thread pool)."""
        conn = duckdb.connect(":memory:")
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute("INSTALL json")
        conn.execute("LOAD json")

        # Read JSON array with DuckDB
        query = f"SELECT * FROM read_json('{url}', format='array') LIMIT {max_rows}"

        result = conn.execute(query).fetchall()
        columns = [desc[0] for desc in conn.description]

        # Get total row count
        try:
            count_query = f"SELECT COUNT(*) FROM read_json('{url}', format='array')"
            total_rows = conn.execute(count_query).fetchone()[0]
        except Exception:
            total_rows = len(result)

        conn.close()

        return {
            "columns": columns,
            "rows": [[make_json_safe(value) for value in row] for row in result],
            "total_rows": total_rows,
            "truncated": len(result) >= max_rows,
            "file_size": None,
        }

    @staticmethod
    async def parse(
        url: str, max_rows: int = 1000, auth_headers: dict[str, str] = None
    ) -> dict[str, Any]:
        """
        Parse JSON array file from URL using DuckDB.

        Args:
            url: File URL (internal /resolve path or S3 presigned URL)
            max_rows: Maximum rows to return
            auth_headers: Optional auth headers for internal /resolve URLs

        Returns:
            Dict with columns, rows, total_rows, truncated, file_size
        """
        # Resolve redirects first (handles internal /resolve URLs)
        resolved_url = await resolve_url_redirects(url, auth_headers)

        # Run in thread pool to avoid blocking event loop
        return await asyncio.to_thread(JSONParser._parse_sync, resolved_url, max_rows)


class ParquetParser:
    """Parse Parquet files using DuckDB (non-blocking, most efficient)."""

    @staticmethod
    def _parse_sync(url: str, max_rows: int) -> dict[str, Any]:
        """Synchronous Parquet parsing with DuckDB (runs in thread pool)."""
        conn = duckdb.connect(":memory:")
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")

        # Read Parquet with DuckDB (uses HTTP range requests automatically!)
        query = f"SELECT * FROM read_parquet('{url}') LIMIT {max_rows}"

        result = conn.execute(query).fetchall()
        columns = [desc[0] for desc in conn.description]

        # Get total row count (efficient - reads only metadata)
        try:
            count_query = f"SELECT COUNT(*) FROM read_parquet('{url}')"
            total_rows = conn.execute(count_query).fetchone()[0]
        except Exception:
            total_rows = len(result)

        conn.close()

        return {
            "columns": columns,
            "rows": [[make_json_safe(value) for value in row] for row in result],
            "total_rows": total_rows,
            "truncated": len(result) >= max_rows or total_rows > max_rows,
            "file_size": None,
        }

    @staticmethod
    async def parse(
        url: str, max_rows: int = 1000, auth_headers: dict[str, str] = None
    ) -> dict[str, Any]:
        """
        Parse Parquet file from URL using DuckDB.

        Args:
            url: File URL (internal /resolve path or S3 presigned URL)
            max_rows: Maximum rows to return
            auth_headers: Optional auth headers for internal /resolve URLs

        Returns:
            Dict with columns, rows, total_rows, truncated, file_size
        """
        # Resolve redirects first (handles internal /resolve URLs)
        # This prevents DuckDB from repeatedly hitting our backend
        resolved_url = await resolve_url_redirects(url, auth_headers)

        # Run in thread pool to avoid blocking event loop
        return await asyncio.to_thread(
            ParquetParser._parse_sync, resolved_url, max_rows
        )


class TARParser:
    """Parse TAR archives from URL using fsspec (streaming, memory efficient)."""

    @staticmethod
    def _list_files_sync(url: str, max_files: int = 10000) -> dict[str, Any]:
        """Synchronous TAR listing using fsspec (runs in thread pool)."""
        fs = HTTPFileSystem()

        # Open TAR file with fsspec (streaming, no full download)
        with fs.open(url, mode="rb") as f:
            files = []
            total_size = 0

            with tarfile.open(fileobj=f, mode="r|*") as tar:
                for member in tar:
                    if member.isfile():
                        files.append(
                            {
                                "name": member.name,
                                "size": member.size,
                                "type": "file",
                            }
                        )
                        total_size += member.size

                        # Limit number of files listed
                        if len(files) >= max_files:
                            break

            return {
                "files": files,
                "total_files": len(files),
                "total_size": total_size,
                "truncated": len(files) >= max_files,
            }

    @staticmethod
    async def list_files(url: str, max_files: int = 10000) -> dict[str, Any]:
        """
        List files in TAR archive using streaming (no full download).

        Args:
            url: TAR file URL
            max_files: Maximum number of files to list

        Returns:
            {
                "files": [{"name": "...", "size": N, "type": "file"}, ...],
                "total_files": N,
                "total_size": N,
                "truncated": bool
            }
        """
        # Resolve redirects first
        resolved_url = await resolve_url_redirects(url)

        # Run in thread pool to avoid blocking
        return await asyncio.to_thread(
            TARParser._list_files_sync, resolved_url, max_files
        )

    @staticmethod
    def _extract_file_sync(url: str, file_name: str) -> bytes:
        """Synchronous TAR file extraction using fsspec (runs in thread pool)."""
        fs = HTTPFileSystem()

        with fs.open(url, mode="rb") as f:
            with tarfile.open(fileobj=f, mode="r|*") as tar:
                for member in tar:
                    if member.isfile() and member.name == file_name:
                        file_obj = tar.extractfile(member)
                        if file_obj is None:
                            raise ParserError(f"Cannot extract {file_name}")
                        return file_obj.read()

                # File not found
                raise ParserError(f"File not found in archive: {file_name}")

    @staticmethod
    async def extract_file(
        url: str, file_name: str, auth_headers: dict[str, str] = None
    ) -> bytes:
        """
        Extract single file from TAR archive using streaming.

        Args:
            url: TAR file URL (internal /resolve path or S3 presigned URL)
            file_name: Name of file to extract
            auth_headers: Optional auth headers for internal /resolve URLs

        Returns:
            File content as bytes
        """
        # Resolve redirects first (handles internal /resolve URLs)
        resolved_url = await resolve_url_redirects(url, auth_headers)

        # Run in thread pool to avoid blocking
        return await asyncio.to_thread(
            TARParser._extract_file_sync, resolved_url, file_name
        )

    @staticmethod
    async def parse(url: str, max_rows: int = 1000) -> dict[str, Any]:
        """
        Parse TAR archive as a table (list of files).

        For dataset viewer compatibility - shows TAR contents as rows.
        Each row represents a file in the archive.

        Args:
            url: TAR file URL
            max_rows: Maximum number of files to show

        Returns:
            {
                "columns": ["name", "size", "type"],
                "rows": [["file1.txt", 1234, "file"], ...],
                "total_rows": N,
                "truncated": bool
            }
        """
        result = await TARParser.list_files(url, max_files=max_rows)

        # Convert to table format for dataset viewer
        rows = [[f["name"], f["size"], f["type"]] for f in result["files"]]

        return {
            "columns": ["name", "size", "type"],
            "rows": rows,
            "total_rows": result["total_files"],
            "truncated": result["truncated"],
            "file_size": result["total_size"],
        }


def detect_format(filename: str) -> Optional[str]:
    """
    Detect file format from filename.

    Returns:
        "csv", "jsonl", "parquet", "tar", or None

    Note: JSON format is NOT supported (requires loading entire file).
    Use JSONL instead for streaming support.
    """
    filename_lower = filename.lower()

    if filename_lower.endswith(".csv"):
        return "csv"
    elif filename_lower.endswith(".jsonl") or filename_lower.endswith(".ndjson"):
        return "jsonl"
    elif filename_lower.endswith(".parquet"):
        return "parquet"
    elif filename_lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2")):
        return "tar"
    elif filename_lower.endswith(".tsv"):
        return "csv"  # TSV is CSV with tab delimiter
    # JSON format deliberately excluded - requires full file download

    return None
