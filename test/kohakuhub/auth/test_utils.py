"""Tests for authentication helpers."""

from datetime import datetime, timezone

from kohakuhub.auth.utils import (
    generate_session_secret,
    generate_token,
    get_expiry_time,
    hash_password,
    hash_token,
    verify_password,
)


def test_hash_password_and_verify_password_roundtrip():
    password = "kohaku-password"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash) is True
    assert verify_password("wrong-password", password_hash) is False


def test_generate_token_and_session_secret_have_expected_lengths():
    assert len(generate_token()) == 64
    assert len(generate_session_secret()) == 32


def test_hash_token_is_stable():
    token = "sample-token"
    assert hash_token(token) == hash_token(token)


def test_get_expiry_time_returns_future_timezone_aware_datetime():
    expiry = get_expiry_time(2)

    assert expiry.tzinfo == timezone.utc
    assert expiry > datetime.now(timezone.utc)
