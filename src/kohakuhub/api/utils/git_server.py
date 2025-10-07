"""Git protocol handler utilities for Smart HTTP protocol.

This module implements the Git Smart HTTP protocol for clone, fetch, pull, and push operations.
"""

import io
import struct
from typing import AsyncIterator

import pygit2

from kohakuhub.logger import get_logger

logger = get_logger("GIT")


def pkt_line(data: bytes | str | None) -> bytes:
    """Encode data as a git pkt-line.

    Args:
        data: Data to encode, or None for flush packet

    Returns:
        Encoded pkt-line bytes
    """
    if data is None:
        # Flush packet
        return b"0000"

    if isinstance(data, str):
        data = data.encode("utf-8")

    # Format: 4-byte hex length + data
    length = len(data) + 4
    return f"{length:04x}".encode("ascii") + data


def pkt_line_stream(lines: list[bytes | str | None]) -> bytes:
    """Encode multiple pkt-lines into a stream.

    Args:
        lines: List of data to encode, None for flush packet

    Returns:
        Concatenated pkt-line bytes
    """
    return b"".join(pkt_line(line) for line in lines)


def parse_pkt_line(data: bytes) -> tuple[bytes | None, bytes]:
    """Parse a single pkt-line from data.

    Args:
        data: Raw bytes to parse

    Returns:
        Tuple of (line_data, remaining_data)
        line_data is None for flush packet
    """
    if len(data) < 4:
        return None, data

    # Read length
    try:
        length_str = data[:4].decode("ascii")
        length = int(length_str, 16)
    except (ValueError, UnicodeDecodeError):
        logger.error(f"Invalid pkt-line length: {data[:4]}")
        return None, data[4:]

    if length == 0:
        # Flush packet
        return None, data[4:]

    if length < 4:
        logger.error(f"Invalid pkt-line length: {length}")
        return None, data[4:]

    # Extract line data
    line_data = data[4:length]
    remaining = data[length:]

    return line_data, remaining


def parse_pkt_lines(data: bytes) -> list[bytes | None]:
    """Parse multiple pkt-lines from data.

    Args:
        data: Raw bytes containing multiple pkt-lines

    Returns:
        List of parsed lines (None for flush packets)
    """
    lines = []
    remaining = data

    while remaining:
        line, remaining = parse_pkt_line(remaining)
        if line is None and not remaining:
            break
        lines.append(line)

    return lines


class GitServiceInfo:
    """Git service advertisement for info/refs."""

    def __init__(self, service: str, refs: dict[str, str], capabilities: list[str]):
        """Initialize Git service info.

        Args:
            service: Service name (upload-pack or receive-pack)
            refs: Dictionary of ref_name -> commit_sha
            capabilities: List of supported capabilities
        """
        self.service = service
        self.refs = refs
        self.capabilities = capabilities

    def to_bytes(self) -> bytes:
        """Encode service info as pkt-line stream.

        Returns:
            Encoded bytes for info/refs response
        """
        lines = []

        # Service header
        lines.append(f"# service=git-{self.service}\n")
        lines.append(None)  # Flush packet

        # Sort refs: HEAD first, then alphabetically
        def sort_refs(item):
            ref_name = item[0]
            if ref_name == "HEAD":
                return (0, ref_name)
            elif ref_name.startswith("refs/heads/"):
                return (1, ref_name)
            elif ref_name.startswith("refs/tags/"):
                return (2, ref_name)
            else:
                return (3, ref_name)

        sorted_refs = sorted(self.refs.items(), key=sort_refs)

        # First ref includes capabilities
        first_ref = True
        for ref_name, commit_sha in sorted_refs:
            if first_ref:
                caps = " ".join(self.capabilities)
                lines.append(f"{commit_sha} {ref_name}\x00{caps}\n")
                first_ref = False
            else:
                lines.append(f"{commit_sha} {ref_name}\n")

        # If no refs, send capabilities with zero-id
        if not self.refs:
            caps = " ".join(self.capabilities)
            lines.append(f"{'0' * 40} capabilities^{{}}\x00{caps}\n")

        lines.append(None)  # Flush packet

        return pkt_line_stream(lines)


class GitUploadPackHandler:
    """Handler for git-upload-pack (clone/fetch/pull)."""

    def __init__(self, repo_path: str, bridge=None):
        """Initialize upload-pack handler.

        Args:
            repo_path: Path to git repository
            bridge: Optional GitLakeFSBridge instance for pack generation
        """
        self.repo_path = repo_path
        self.bridge = bridge
        self.capabilities = [
            "multi_ack",
            "multi_ack_detailed",
            "side-band-64k",
            "thin-pack",
            "ofs-delta",
            "agent=kohakuhub/0.0.1",
        ]

    def get_service_info(self, refs: dict[str, str]) -> bytes:
        """Generate service advertisement for upload-pack.

        Args:
            refs: Dictionary of ref_name -> commit_sha

        Returns:
            Encoded service info
        """
        info = GitServiceInfo("upload-pack", refs, self.capabilities)
        return info.to_bytes()

    async def handle_upload_pack(self, request_body: bytes) -> bytes:
        """Handle upload-pack request (clone/fetch).

        Args:
            request_body: Raw request body from client

        Returns:
            Pack file with requested objects
        """
        # Parse want/have lines
        wants = []
        haves = []

        lines = parse_pkt_lines(request_body)

        for line in lines:
            if line is None:
                continue

            line_str = line.decode("utf-8").strip()

            if line_str.startswith("want "):
                # Extract SHA and capabilities
                parts = line_str.split()
                want_sha = parts[1]
                wants.append(want_sha)
            elif line_str.startswith("have "):
                # Extract SHA
                have_sha = line_str.split()[1]
                haves.append(have_sha)
            elif line_str == "done":
                break

        logger.info(f"Upload-pack: wants={len(wants)}, haves={len(haves)}")

        # Send NAK (no common commits)
        nak_response = pkt_line_stream([b"NAK\n"])

        # Generate pack file
        if self.bridge:
            # Use bridge to build pack from LakeFS
            pack_data = await self.bridge.build_pack_file(wants, haves, branch="main")
        else:
            # Fallback to empty pack
            pack_data = self._create_empty_pack()

        # Side-band protocol: prefix each chunk with band number
        # Band 1 = pack data, Band 2 = progress, Band 3 = error
        # Format: <band><data>

        # Send pack data on band 1
        pack_line = b'\x01' + pack_data

        response = nak_response + pkt_line(pack_line) + pkt_line(None)

        return response

    def _create_empty_pack(self) -> bytes:
        """Create an empty Git pack file.

        Returns:
            Empty pack file bytes
        """
        import hashlib
        import struct

        pack_header = b'PACK'  # Signature
        pack_header += struct.pack('>I', 2)  # Version 2
        pack_header += struct.pack('>I', 0)  # 0 objects
        pack_checksum = hashlib.sha1(pack_header).digest()

        return pack_header + pack_checksum


class GitReceivePackHandler:
    """Handler for git-receive-pack (push)."""

    def __init__(self, repo_path: str):
        """Initialize receive-pack handler.

        Args:
            repo_path: Path to git repository
        """
        self.repo_path = repo_path
        self.capabilities = [
            "report-status",
            "side-band-64k",
            "delete-refs",
            "ofs-delta",
            "agent=kohakuhub/0.0.1",
        ]

    def get_service_info(self, refs: dict[str, str]) -> bytes:
        """Generate service advertisement for receive-pack.

        Args:
            refs: Dictionary of ref_name -> commit_sha

        Returns:
            Encoded service info
        """
        info = GitServiceInfo("receive-pack", refs, self.capabilities)
        return info.to_bytes()

    async def handle_receive_pack(self, request_body: bytes) -> bytes:
        """Handle receive-pack request (push).

        Args:
            request_body: Raw request body from client (contains ref updates + pack)

        Returns:
            Status report
        """
        # Parse ref updates
        ref_updates = []

        lines = parse_pkt_lines(request_body)
        pack_data_start = None

        for i, line in enumerate(lines):
            if line is None:
                # Flush packet marks end of commands
                # Pack data follows
                pack_data_start = i + 1
                break

            line_str = line.decode("utf-8").strip()

            # Format: old-sha new-sha ref-name
            parts = line_str.split()
            if len(parts) >= 3:
                old_sha = parts[0]
                new_sha = parts[1]
                ref_name = parts[2]
                ref_updates.append((old_sha, new_sha, ref_name))

        logger.info(f"Receive-pack: {len(ref_updates)} ref updates")

        # TODO: Process pack file and update refs
        # This will be implemented in git_lakefs_bridge.py

        # Send success status
        status_lines = [
            None,  # Flush
            b"\x01unpack ok\n",
        ]

        for old_sha, new_sha, ref_name in ref_updates:
            status_lines.append(f"\x01ok {ref_name}\n".encode())

        status_lines.append(None)  # Flush

        return pkt_line_stream(status_lines)


def parse_git_credentials(authorization: str | None) -> tuple[str | None, str | None]:
    """Parse username and token from Basic Auth header.

    Args:
        authorization: Authorization header value

    Returns:
        Tuple of (username, token)
    """
    if not authorization:
        return None, None

    if not authorization.startswith("Basic "):
        return None, None

    import base64

    try:
        # Decode base64 credentials
        encoded = authorization[6:]  # Remove "Basic "
        decoded = base64.b64decode(encoded).decode("utf-8")

        # Split username:password
        if ":" in decoded:
            username, token = decoded.split(":", 1)
            return username, token

        return None, None
    except Exception as e:
        logger.error(f"Failed to parse git credentials: {e}")
        return None, None
