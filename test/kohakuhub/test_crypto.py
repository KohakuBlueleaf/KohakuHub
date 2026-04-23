"""Tests for token encryption helpers."""

import pytest

from kohakuhub.crypto import decrypt_token, encrypt_token, mask_token


def test_encrypt_and_decrypt_token_roundtrip():
    encrypted = encrypt_token("hf_secret_token")

    assert encrypted != "hf_secret_token"
    assert decrypt_token(encrypted) == "hf_secret_token"


def test_encrypt_and_decrypt_empty_token_return_empty_string():
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""


def test_decrypt_invalid_token_raises_value_error():
    with pytest.raises(ValueError):
        decrypt_token("not-a-valid-token")


def test_mask_token_hides_suffix():
    assert mask_token("hf_abcdefgh") == "hf_a***"
    assert mask_token("abc", show_chars=4) == "***"
