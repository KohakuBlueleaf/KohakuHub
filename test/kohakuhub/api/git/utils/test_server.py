"""Tests for Git Smart HTTP utility helpers."""

from __future__ import annotations

import base64
import struct

import pytest

import kohakuhub.api.git.utils.server as git_server


def test_create_empty_pack_matches_pack_header_contract():
    pack = git_server.create_empty_pack()

    assert pack.startswith(b"PACK")
    assert pack[4:8] == struct.pack(">I", 2)
    assert pack[8:12] == struct.pack(">I", 0)


def test_pkt_line_and_stream_encode_expected_frames():
    assert git_server.pkt_line(None) == b"0000"
    assert git_server.pkt_line("hello") == b"0009hello"
    assert git_server.pkt_line(b"ok") == b"0006ok"
    assert git_server.pkt_line_stream([b"hi", None, "ok"]) == b"0006hi00000006ok"


def test_parse_pkt_line_and_parse_pkt_lines_cover_valid_and_invalid_inputs(monkeypatch):
    errors = []
    monkeypatch.setattr(git_server.logger, "error", errors.append)

    assert git_server.parse_pkt_line(b"0009hello") == (b"hello", b"")
    assert git_server.parse_pkt_line(b"0000rest") == (None, b"rest")
    assert git_server.parse_pkt_line(b"xyz1abc")[0] is None
    assert git_server.parse_pkt_line(b"0003abc")[0] is None
    assert errors == ["Invalid pkt-line length: b'xyz1'", "Invalid pkt-line length: 3"]
    assert git_server.parse_pkt_lines(b"0009hello0000") == [b"hello"]


def test_git_service_info_encodes_refs_and_capabilities():
    info = git_server.GitServiceInfo(
        "upload-pack",
        {
            "refs/tags/v1": "3" * 40,
            "HEAD": "1" * 40,
            "refs/heads/main": "2" * 40,
        },
        ["side-band-64k", "agent=test"],
    )
    payload = info.to_bytes()

    assert b"# service=git-upload-pack\n" in payload
    assert payload.count(b"0000") >= 2
    assert b"1" * 40 + b" HEAD\x00side-band-64k agent=test\n" in payload
    assert payload.index(b" HEAD\x00") < payload.index(b" refs/heads/main\n")
    assert payload.index(b" refs/heads/main\n") < payload.index(b" refs/tags/v1\n")


def test_git_service_info_without_refs_emits_capability_stub():
    info = git_server.GitServiceInfo("receive-pack", {}, ["report-status"])

    assert b"capabilities^{}\x00report-status\n" in info.to_bytes()


@pytest.mark.asyncio
async def test_upload_pack_handler_uses_bridge_and_chunks_large_packs():
    class FakeBridge:
        def __init__(self):
            self.calls = []

        async def build_pack_file(self, wants, haves, branch="main"):
            self.calls.append((wants, haves, branch))
            return b"x" * 70000

    bridge = FakeBridge()
    handler = git_server.GitUploadPackHandler("/tmp/repo", bridge=bridge)
    request_body = git_server.pkt_line_stream(
        [
            "want " + "1" * 40 + " side-band-64k\n",
            "have " + "2" * 40 + "\n",
            "done\n",
        ]
    )

    response = await handler.handle_upload_pack(request_body)

    assert bridge.calls == [(["1" * 40], ["2" * 40], "main")]
    assert response.startswith(git_server.pkt_line_stream([b"NAK\n"]))
    assert response.endswith(b"0000")
    assert response.count(b"\x01") == 2


@pytest.mark.asyncio
async def test_upload_pack_handler_without_bridge_uses_empty_pack():
    handler = git_server.GitUploadPackHandler("/tmp/repo")

    response = await handler.handle_upload_pack(git_server.pkt_line_stream(["done\n"]))

    assert b"PACK" in response
    assert response.endswith(b"0000")


def test_upload_and_receive_pack_handlers_generate_service_info():
    upload = git_server.GitUploadPackHandler("/tmp/repo")
    receive = git_server.GitReceivePackHandler("/tmp/repo")

    assert b"# service=git-upload-pack\n" in upload.get_service_info({"HEAD": "1" * 40})
    assert b"# service=git-receive-pack\n" in receive.get_service_info({"HEAD": "1" * 40})


@pytest.mark.asyncio
async def test_receive_pack_handler_reports_updated_refs():
    handler = git_server.GitReceivePackHandler("/tmp/repo")
    request_body = git_server.pkt_line_stream(
        [
            f"{'0' * 40} {'1' * 40} refs/heads/main\n",
            f"{'1' * 40} {'2' * 40} refs/tags/v1\n",
            None,
        ]
    )

    response = await handler.handle_receive_pack(request_body)

    assert b"\x01unpack ok\n" in response
    assert b"\x01ok refs/heads/main\n" in response
    assert b"\x01ok refs/tags/v1\n" in response


def test_parse_git_credentials_handles_valid_invalid_and_malformed_headers(monkeypatch):
    errors = []
    monkeypatch.setattr(git_server.logger, "error", errors.append)

    good = "Basic " + base64.b64encode(b"owner:token-123").decode("ascii")
    bad = "Basic " + "!!!not-base64!!!"

    assert git_server.parse_git_credentials(None) == (None, None)
    assert git_server.parse_git_credentials("Bearer abc") == (None, None)
    assert git_server.parse_git_credentials(good) == ("owner", "token-123")
    assert git_server.parse_git_credentials("Basic " + base64.b64encode(b"owner-only").decode("ascii")) == (
        None,
        None,
    )
    assert git_server.parse_git_credentials(bad) == (None, None)
    assert errors
