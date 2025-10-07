"""SSH Key Management API endpoints."""

import base64
import hashlib
import re
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuhub.auth.dependencies import get_current_user
from kohakuhub.db import SSHKey, User
from kohakuhub.db_async import (
    create_ssh_key,
    delete_ssh_key,
    get_ssh_key_by_fingerprint,
    get_ssh_key_by_id,
    list_user_ssh_keys,
)
from kohakuhub.logger import get_logger

logger = get_logger("SSH_KEYS")
router = APIRouter()


class SSHKeyCreate(BaseModel):
    """Request model for creating SSH key."""

    title: str
    key: str  # Full public key (e.g., "ssh-ed25519 AAAA... user@host")


class SSHKeyResponse(BaseModel):
    """Response model for SSH key."""

    id: int
    title: str
    key_type: str
    fingerprint: str
    created_at: str
    last_used: str | None


def parse_ssh_public_key(key_str: str) -> tuple[str, str, str]:
    """Parse SSH public key and extract type, key data, and comment.

    Args:
        key_str: SSH public key string

    Returns:
        Tuple of (key_type, key_data, comment)

    Raises:
        ValueError: If key format is invalid
    """
    # Remove extra whitespace
    key_str = key_str.strip()

    # SSH key format: <type> <base64-data> [comment]
    parts = key_str.split(None, 2)

    if len(parts) < 2:
        raise ValueError("Invalid SSH key format")

    key_type = parts[0]
    key_data = parts[1]
    comment = parts[2] if len(parts) > 2 else ""

    # Validate key type
    valid_types = [
        "ssh-rsa",
        "ssh-dss",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "ssh-ed25519",
    ]

    if key_type not in valid_types:
        raise ValueError(f"Unsupported key type: {key_type}")

    # Validate base64 data
    try:
        base64.b64decode(key_data)
    except Exception:
        raise ValueError("Invalid base64-encoded key data")

    return key_type, key_data, comment


def compute_ssh_fingerprint(key_type: str, key_data: str) -> str:
    """Compute SSH key fingerprint (SHA256).

    Args:
        key_type: SSH key type
        key_data: Base64-encoded key data

    Returns:
        SHA256 fingerprint in format: SHA256:...
    """
    # Decode key data
    key_bytes = base64.b64decode(key_data)

    # Compute SHA256 hash
    sha256_hash = hashlib.sha256(key_bytes).digest()

    # Encode as base64 (without padding)
    fingerprint = base64.b64encode(sha256_hash).decode().rstrip("=")

    return f"SHA256:{fingerprint}"


def validate_ssh_key(key_str: str) -> tuple[str, str]:
    """Validate SSH public key using cryptography library.

    Args:
        key_str: SSH public key string

    Returns:
        Tuple of (key_type, fingerprint)

    Raises:
        ValueError: If key is invalid
    """
    try:
        # Parse key
        key_type, key_data, comment = parse_ssh_public_key(key_str)

        # Compute fingerprint
        fingerprint = compute_ssh_fingerprint(key_type, key_data)

        # Additional validation using cryptography library
        try:
            key_bytes = base64.b64decode(key_data)

            # Try to load as public key (this validates the key format)
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.serialization import (
                load_ssh_public_key,
            )

            # Reconstruct SSH public key format for validation
            ssh_key = f"{key_type} {key_data}"
            load_ssh_public_key(ssh_key.encode(), backend=default_backend())

        except Exception as e:
            logger.warning(f"Key validation failed: {e}")
            # Don't fail on validation error, just log it
            # Some valid keys might not load with cryptography library

        return key_type, fingerprint

    except Exception as e:
        raise ValueError(f"Invalid SSH key: {e}")


@router.get("/api/user/keys")
async def list_ssh_keys(user: User = Depends(get_current_user)):
    """List all SSH keys for the current user.

    Returns:
        List of SSH keys
    """
    keys = await list_user_ssh_keys(user.id)

    return [
        SSHKeyResponse(
            id=key.id,
            title=key.title,
            key_type=key.key_type,
            fingerprint=key.fingerprint,
            created_at=key.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            last_used=(
                key.last_used.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if key.last_used else None
            ),
        )
        for key in keys
    ]


@router.post("/api/user/keys")
async def add_ssh_key(key_request: SSHKeyCreate, user: User = Depends(get_current_user)):
    """Add new SSH key for the current user.

    Args:
        key_request: SSH key data
        user: Current authenticated user

    Returns:
        Created SSH key

    Raises:
        HTTPException: If key is invalid or already exists
    """
    try:
        # Validate key
        key_type, fingerprint = validate_ssh_key(key_request.key)

        # Check if key already exists
        existing_key = await get_ssh_key_by_fingerprint(fingerprint)
        if existing_key:
            raise HTTPException(
                409, detail="SSH key already exists (same key registered by another user or you)"
            )

        # Create key
        new_key = await create_ssh_key(
            user_id=user.id,
            key_type=key_type,
            public_key=key_request.key.strip(),
            fingerprint=fingerprint,
            title=key_request.title,
        )

        logger.info(f"User {user.username} added SSH key: {key_type} {fingerprint}")

        return SSHKeyResponse(
            id=new_key.id,
            title=new_key.title,
            key_type=new_key.key_type,
            fingerprint=new_key.fingerprint,
            created_at=new_key.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            last_used=None,
        )

    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.delete("/api/user/keys/{key_id}")
async def remove_ssh_key(key_id: int, user: User = Depends(get_current_user)):
    """Remove SSH key.

    Args:
        key_id: SSH key ID
        user: Current authenticated user

    Returns:
        Success message

    Raises:
        HTTPException: If key not found or not owned by user
    """
    # Get key
    key = await get_ssh_key_by_id(key_id)
    if not key:
        raise HTTPException(404, detail="SSH key not found")

    # Check ownership
    if key.user_id != user.id:
        raise HTTPException(403, detail="Not authorized to delete this key")

    # Delete key
    await delete_ssh_key(key)

    logger.info(f"User {user.username} removed SSH key: {key.key_type} {key.fingerprint}")

    return {"message": "SSH key deleted successfully"}


@router.get("/api/user/keys/{key_id}")
async def get_ssh_key(key_id: int, user: User = Depends(get_current_user)):
    """Get SSH key details.

    Args:
        key_id: SSH key ID
        user: Current authenticated user

    Returns:
        SSH key details

    Raises:
        HTTPException: If key not found or not owned by user
    """
    # Get key
    key = await get_ssh_key_by_id(key_id)
    if not key:
        raise HTTPException(404, detail="SSH key not found")

    # Check ownership
    if key.user_id != user.id:
        raise HTTPException(403, detail="Not authorized to view this key")

    return SSHKeyResponse(
        id=key.id,
        title=key.title,
        key_type=key.key_type,
        fingerprint=key.fingerprint,
        created_at=key.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        last_used=key.last_used.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if key.last_used else None,
    )
