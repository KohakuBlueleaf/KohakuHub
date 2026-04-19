"""Tests for external token Authorization header parsing."""

from kohakuhub.auth.external_token_parser import format_auth_header, parse_auth_header


def test_parse_auth_header_without_external_tokens():
    assert parse_auth_header("Bearer hf_abc123") == ("hf_abc123", {})


def test_parse_auth_header_with_external_tokens():
    auth_token, external = parse_auth_header(
        "Bearer hf_main|https://huggingface.co,hf_ext|https://mirror.local,mirror-token"
    )

    assert auth_token == "hf_main"
    assert external == {
        "https://huggingface.co": "hf_ext",
        "https://mirror.local": "mirror-token",
    }


def test_parse_auth_header_ignores_invalid_fragments():
    auth_token, external = parse_auth_header("Bearer hf_main|invalid|,empty")

    assert auth_token == "hf_main"
    assert external == {}


def test_format_auth_header_roundtrips():
    header = format_auth_header(
        "hf_main", {"https://huggingface.co": "hf_ext", "https://mirror.local": ""}
    )

    assert header.startswith("Bearer ")
    assert parse_auth_header(header) == (
        "hf_main",
        {"https://huggingface.co": "hf_ext", "https://mirror.local": ""},
    )
