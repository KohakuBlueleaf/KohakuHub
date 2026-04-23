"""API tests for admin database viewer routes."""


async def test_admin_database_routes_list_tables_templates_and_execute_queries(admin_client):
    tables_response = await admin_client.get("/admin/api/database/tables")
    assert tables_response.status_code == 200
    tables = tables_response.json()["tables"]
    table_names = {table["name"] for table in tables}
    assert "user" in table_names
    assert "repository" in table_names
    assert any(column["name"] == "username" for table in tables if table["name"] == "user" for column in table["columns"])

    templates_response = await admin_client.get("/admin/api/database/templates")
    assert templates_response.status_code == 200
    assert templates_response.json()["templates"]

    query_response = await admin_client.post(
        "/admin/api/database/query",
        json={
            "sql": 'SELECT username, is_org FROM "user" ORDER BY username LIMIT 3;'
        },
    )
    assert query_response.status_code == 200
    payload = query_response.json()
    assert payload["columns"] == ["username", "is_org"]
    assert payload["count"] >= 1
    assert isinstance(payload["rows"][0]["username"], str)
    assert payload["rows"][0]["is_org"] in {"True", "False"}


async def test_admin_database_query_rejects_invalid_or_failing_sql(admin_client):
    invalid_query = await admin_client.post(
        "/admin/api/database/query",
        json={"sql": "DELETE FROM repository"},
    )
    assert invalid_query.status_code == 400
    assert "Invalid query" in invalid_query.json()["detail"]["error"]

    failing_query = await admin_client.post(
        "/admin/api/database/query",
        json={"sql": "SELECT * FROM missing_table"},
    )
    assert failing_query.status_code == 400
    assert "Query execution failed" in failing_query.json()["detail"]["error"]
