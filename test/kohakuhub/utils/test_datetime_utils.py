"""Tests for datetime utility helpers."""

from datetime import datetime, timezone

import pytest

from kohakuhub.utils.datetime_utils import ensure_datetime, safe_isoformat, safe_strftime


def test_safe_isoformat_handles_common_input_types():
    moment = datetime(2026, 4, 20, 12, 34, 56, tzinfo=timezone.utc)

    assert safe_isoformat(None) is None
    assert safe_isoformat(moment) == moment.isoformat()
    assert safe_isoformat("2026-04-20T12:34:56Z") == "2026-04-20T12:34:56+00:00"
    assert safe_isoformat("not-a-datetime") == "not-a-datetime"
    assert safe_isoformat(123) == "123"


def test_ensure_datetime_parses_strings_and_preserves_datetime_instances():
    moment = datetime(2026, 4, 20, 12, 34, 56, tzinfo=timezone.utc)

    assert ensure_datetime(None) is None
    assert ensure_datetime(moment) is moment
    assert ensure_datetime("2026-04-20T12:34:56Z") == moment


def test_ensure_datetime_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="Cannot parse datetime string"):
        ensure_datetime("definitely-not-a-date")

    with pytest.raises(TypeError, match="Expected datetime or str"):
        ensure_datetime(1.5)


def test_safe_strftime_formats_string_and_datetime_inputs():
    fmt = "%Y/%m/%d %H:%M"
    moment = datetime(2026, 4, 20, 12, 34, 56, tzinfo=timezone.utc)

    assert safe_strftime(None, fmt) is None
    assert safe_strftime(moment, fmt) == "2026/04/20 12:34"
    assert safe_strftime("2026-04-20T12:34:56Z", fmt) == "2026/04/20 12:34"
