from __future__ import annotations

from datetime import date, datetime, time

from kohakuhub.datasetviewer.parsers import make_json_safe
from kohakuhub.datasetviewer.sql_query import _make_json_safe


def test_make_json_safe_converts_bytes_nested_values_and_temporals():
    binary = b"\x00\x01demo"
    dt = datetime(2026, 4, 21, 20, 40, 0)
    d = date(2026, 4, 21)
    t = time(20, 40, 0)

    payload = {
        "binary": binary,
        "nested": [binary, {"when": dt, "day": d, "clock": t}],
    }

    result = make_json_safe(payload)

    assert result == {
        "binary": {
            "__type__": "bytes",
            "encoding": "base64",
            "size": 6,
            "data": "AAFkZW1v",
        },
        "nested": [
            {
                "__type__": "bytes",
                "encoding": "base64",
                "size": 6,
                "data": "AAFkZW1v",
            },
            {
                "when": "2026-04-21T20:40:00",
                "day": "2026-04-21",
                "clock": "20:40:00",
            },
        ],
    }


def test_sql_json_safe_matches_parser_behavior_for_bytes():
    assert _make_json_safe(b"abc") == {
        "__type__": "bytes",
        "encoding": "base64",
        "size": 3,
        "data": "YWJj",
    }
