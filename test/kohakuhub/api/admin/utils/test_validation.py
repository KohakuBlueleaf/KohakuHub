"""Tests for admin SQL validation helpers."""

import pytest

from kohakuhub.api.admin.utils.validation import get_query_templates, validate_readonly_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM repository",
        "SELECT * FROM repository;",
        "WITH recent AS (SELECT * FROM repository) SELECT * FROM recent",
        "SELECT commit FROM metrics",
    ],
)
def test_validate_readonly_sql_accepts_safe_select_queries(sql):
    assert validate_readonly_sql(sql) == (True, None)


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("", "Query cannot be empty"),
        ("  ", "Query cannot be empty"),
        ("DELETE FROM repository", "Only SELECT queries are allowed"),
        ("SELECT * FROM repository; DELETE FROM repository", "Multiple statements are not allowed"),
        ("SELECT * FROM repository -- hidden", "SQL comments are not allowed"),
        ("SELECT load_extension('x')", "Function 'LOAD_EXTENSION' is not allowed"),
        ("SELECT * FROM repository; COMMIT", "Keyword 'COMMIT' is not allowed"),
        ("PRAGMA table_info(repository)", "Only SELECT queries are allowed"),
    ],
)
def test_validate_readonly_sql_rejects_dangerous_patterns(sql, message):
    valid, error = validate_readonly_sql(sql)

    assert valid is False
    assert message in error


def test_get_query_templates_returns_named_safe_queries():
    templates = get_query_templates()

    assert len(templates) >= 6
    for template in templates:
        assert template["name"]
        assert template["description"]
        assert template["sql"].lstrip().upper().startswith("SELECT")
