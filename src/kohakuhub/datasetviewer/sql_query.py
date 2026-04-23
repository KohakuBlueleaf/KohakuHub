"""
SQL query execution on remote datasets using DuckDB + httpfs.

DuckDB can query CSV, Parquet, JSONL files directly from HTTP URLs
using range requests (no full download required!).

Key features:
- Queries Parquet with range requests (only reads needed row groups)
- Queries CSV with streaming (stops after reading needed rows)
- No full file download required
- Uses DuckDB's native httpfs extension
"""

import asyncio
import base64
import re
from datetime import date, datetime, time
from typing import Any

import duckdb

from kohakuhub.datasetviewer.logger import get_logger
from kohakuhub.datasetviewer.parsers import resolve_url_redirects

logger = get_logger("SQLQuery")


class SQLQueryError(Exception):
    """SQL query execution error."""

    pass


def _make_json_safe(value: Any) -> Any:
    """Convert DuckDB values into JSON-safe primitives for API responses."""
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
        return [_make_json_safe(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}

    return str(value)


def _execute_query_sync(url: str, query: str, file_format: str, max_rows: int):
    """Synchronous SQL execution (runs in thread pool)."""
    # Create DuckDB connection
    conn = duckdb.connect(":memory:")

    # Install and load httpfs extension for HTTP range requests
    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")

    # Disable ETag checks (S3 presigned URLs have changing ETags)
    conn.execute("SET unsafe_disable_etag_checks = true")

    # Sanitize query (basic safety)
    query_upper = query.upper()
    if any(
        keyword in query_upper
        for keyword in ["DROP", "DELETE", "INSERT", "UPDATE", "CREATE", "ALTER"]
    ):
        raise SQLQueryError("Only SELECT queries are allowed")

    # Build read function based on format
    # DuckDB's httpfs extension handles HTTP URLs directly with range requests
    if file_format == "csv":
        read_func = f"read_csv('{url}', delim=',', header=true, auto_detect=true)"
    elif file_format == "tsv":
        read_func = f"read_csv('{url}', delim='\\t', header=true, auto_detect=true)"
    elif file_format == "parquet":
        read_func = f"read_parquet('{url}')"
    elif file_format == "jsonl":
        read_func = f"read_ndjson('{url}')"
    elif file_format == "json":
        read_func = f"read_json('{url}', format='array')"
    else:
        raise SQLQueryError(
            f"Format {file_format} not supported. Use CSV, Parquet, JSONL, or JSON."
        )

    # Replace 'dataset' placeholder with actual read function
    # Use case-insensitive replace for both "dataset" and "DATASET"
    if re.search(r"\bdataset\b", query, re.IGNORECASE):
        # Replace whole word 'dataset' with read function (without extra parens)
        actual_query = re.sub(r"\bdataset\b", read_func, query, flags=re.IGNORECASE)
    elif "FROM" not in query_upper:
        # No FROM clause - assume they want to query the file
        actual_query = f"{query} FROM {read_func}"
    else:
        # User specified their own FROM clause - use as is
        actual_query = query

    # Add LIMIT if not present (safety)
    if "LIMIT" not in query_upper:
        actual_query = f"{actual_query} LIMIT {max_rows}"

    # Execute query (DuckDB uses range requests via fsspec!)
    logger.debug(f"Executing SQL query on {file_format}: {actual_query[:100]}...")
    result = conn.execute(actual_query).fetchall()

    # Get column names
    columns = [desc[0] for desc in conn.description]

    # Get row count
    total_rows = len(result)

    logger.info(f"Query returned {total_rows} rows, {len(columns)} columns")

    conn.close()

    return {
        "columns": columns,
        "rows": [[_make_json_safe(value) for value in row] for row in result],
        "total_rows": total_rows,
        "truncated": total_rows >= max_rows,
        "query": actual_query,
    }


async def execute_sql_query(
    url: str,
    query: str,
    file_format: str = "parquet",
    max_rows: int = 10,
    auth_headers: dict[str, str] = None,
) -> dict[str, Any]:
    """
    Execute SQL query on remote dataset using DuckDB.

    DuckDB automatically uses HTTP range requests via fsspec,
    so it doesn't download the entire file!

    Args:
        url: File URL (internal /resolve path or S3 presigned URL)
        query: SQL query to execute
        file_format: File format (csv, parquet, jsonl, json)
        max_rows: Maximum rows to return (default: 10 for wide tables)
        auth_headers: Optional auth headers for internal /resolve URLs

    Returns:
        {
            "columns": ["col1", "col2", ...],
            "rows": [[val1, val2, ...], ...],
            "total_rows": N,
            "truncated": bool,
            "query": str  # The executed query
        }

    Example queries:
        SELECT * FROM dataset LIMIT 10
        SELECT age, COUNT(*) as count FROM dataset GROUP BY age
        SELECT * FROM dataset WHERE salary > 100000 ORDER BY salary DESC
    """
    # Resolve redirects first (handles internal /resolve URLs with auth)
    # This prevents DuckDB from repeatedly hitting our backend for range requests
    resolved_url = await resolve_url_redirects(url, auth_headers)

    # Run in thread pool (DuckDB is synchronous)
    # Use resolved_url (not original url) to avoid repeated backend hits
    return await asyncio.to_thread(
        _execute_query_sync, resolved_url, query, file_format, max_rows
    )
