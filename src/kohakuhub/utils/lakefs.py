"""LakeFS client utilities and helper functions."""

import hashlib
import re

import numpy as np

from kohakuhub.lakefs_rest_client import LakeFSRestClient, get_lakefs_rest_client


def get_lakefs_client() -> LakeFSRestClient:
    """Get configured LakeFS REST client.

    Returns:
        Configured LakeFSRestClient instance.
    """
    return get_lakefs_rest_client()


async def resolve_revision(
    client: LakeFSRestClient, lakefs_repo: str, revision: str
) -> tuple[str, dict | None]:
    """Resolve a revision (branch name or commit hash) to commit ID and info.

    HuggingFace datasets library and other clients may use either branch names
    (e.g., "main") or commit hashes as revision identifiers. This function
    handles both cases by first trying to resolve as a branch, then as a commit.

    Args:
        client: LakeFS REST client instance
        lakefs_repo: LakeFS repository name
        revision: Branch name or commit hash

    Returns:
        Tuple of (commit_id, commit_info dict or None)

    Raises:
        ValueError: If revision cannot be resolved as either branch or commit
    """
    # Try resolving as a branch first
    try:
        branch = await client.get_branch(repository=lakefs_repo, branch=revision)
        commit_id = branch["commit_id"]
        # Get commit details
        try:
            commit_info = await client.get_commit(
                repository=lakefs_repo, commit_id=commit_id
            )
        except Exception:
            commit_info = None
        return commit_id, commit_info
    except Exception as branch_error:
        # Check if it's a "not found" error (branch doesn't exist)
        error_str = str(branch_error).lower()
        if "404" not in error_str and "not found" not in error_str:
            # Some other error, re-raise
            raise branch_error

    # Branch not found, try resolving as a commit hash
    try:
        commit_info = await client.get_commit(
            repository=lakefs_repo, commit_id=revision
        )
        return commit_info["id"], commit_info
    except Exception as commit_error:
        # Neither branch nor commit found
        raise ValueError(
            f"Revision '{revision}' not found as branch or commit"
        ) from commit_error


def _base36_encode(num: int) -> str:
    """Encode integer to base36 using numpy (C-optimized).

    Alphabet: 0-9, a-z (36 chars - standard base36, LakeFS-compatible)
    Each char carries ~5.17 bits (log2(36) = 5.170)
    22 chars can hold ~113.7 bits (sufficient for 112-bit hash)

    Args:
        num: Integer to encode

    Returns:
        Base36 encoded string (lowercase)
    """
    # Use numpy's optimized base_repr (C implementation)
    encoded = np.base_repr(num, base=36).lower()
    return encoded


def _hash_to_112bit(data: str) -> int:
    """Hash string to 112-bit integer using SHA3-224 with XOR folding.

    SHA3-224 produces 224 bits (28 bytes), we fold it to 112 bits by XORing the two halves.
    This preserves entropy while reducing size to fit base37 encoding.

    Args:
        data: String to hash

    Returns:
        112-bit integer (0 to 2^112-1)
    """
    # Use SHA3-224 (produces exactly 224 bits = 28 bytes)
    hash_bytes = hashlib.sha3_224(data.encode()).digest()  # 28 bytes = 224 bits

    # Split into two 112-bit (14 byte) halves and XOR them
    half1 = int.from_bytes(hash_bytes[:14], "big")  # First 14 bytes = 112 bits
    half2 = int.from_bytes(hash_bytes[14:], "big")  # Last 14 bytes = 112 bits

    return half1 ^ half2  # XOR folding: 224 bits → 112 bits


def _sanitize_repo_id(repo_id: str) -> str:
    """Sanitize repo ID to LakeFS-safe characters.

    Allowed: a-z, 0-9, hyphen
    Replace: /, _, ., and any other special chars with hyphen

    Args:
        repo_id: Raw repo ID (e.g., "org/repo_name.v2")

    Returns:
        Sanitized ID (e.g., "org-repo-name-v2")
    """
    # Replace common separators
    safe = repo_id.replace("/", "-").replace("_", "-").replace(".", "-")

    # Remove any remaining non-alphanumeric/hyphen characters
    safe = re.sub(r"[^a-z0-9-]", "-", safe.lower())

    # Collapse consecutive hyphens
    safe = re.sub(r"-+", "-", safe)

    # Strip leading/trailing hyphens
    safe = safe.strip("-")

    return safe


def lakefs_repo_name(repo_type: str, repo_id: str) -> str:
    """Generate LakeFS repository name from HuggingFace repo ID.

    LakeFS naming requirements: ^[a-z0-9][a-z0-9-]{2,62}$
    - Only lowercase letters, numbers, and hyphens
    - Must start with letter or number
    - Length: 3-63 characters

    GLOBAL format (ALL repos use this):
    Format: {type}-{safe_id[:38]}-{hash[:22]}
    - 1 char: Repo type (m=model, d=dataset, s=space)
    - 38 chars: Sanitized repo_id (human-readable, may be truncated)
    - 22 chars: Base36-encoded 112-bit hash of ORIGINAL repo_id (for uniqueness)
    Total: <= 63 chars

    Why hash ORIGINAL repo_id?
    - Sanitization can make different names identical (e.g., "my_repo" vs "my.repo" → "my-repo")
    - Hash ensures uniqueness even after sanitization
    - Hash is ALWAYS included (no escaping)

    Note:
        Base36 encoding (0-9, a-z) provides ~5.17 bits/char
        22 chars × 5.17 = ~113.7 bits (fits 112-bit hash perfectly)
        Hash: SHA3-224 (224 bits) → XOR folding → 112 bits
        Uses numpy.base_repr for C-optimized performance

    Args:
        repo_type: Repository type (model/dataset/space)
        repo_id: Full repository ID (e.g., "org/repo-name")

    Returns:
        LakeFS-safe repository name (always 63 chars)

    Examples:
        - "model", "org/simple" → "m" + "-" + "org-simple" (38 chars) + "-" + hash(22 chars)
        - "dataset", "org/my_data.v2" → "d" + "-" + "org-my-data-v2" (38 chars) + "-" +  hash(22 chars)
        - "model", "org/very-long-repository-name-with-version-v2.3.4-final"
          → "m" + "-" + "org-very-long-repository-name-with-ver" (38 chars) + "-" +  hash(22 chars)
    """
    # Map repo type to single character
    type_char = {"model": "m", "dataset": "d", "space": "s"}.get(repo_type, "m")

    # Sanitize repo_id for human-readable part (truncate to 38 chars max)
    safe_id = _sanitize_repo_id(repo_id)[:38]

    # ALWAYS generate hash of ORIGINAL repo_id (before sanitization)
    # This ensures uniqueness even when sanitization causes collisions
    hash_int = _hash_to_112bit(repo_id)  # Hash ORIGINAL, not sanitized!

    # Encode to base36 using numpy (C-optimized)
    hash_b36 = _base36_encode(hash_int)

    # Pad to exactly 22 chars (left-pad with '0')
    hash_suffix = hash_b36.zfill(22)

    # Build final name: type(1) + safe_id(<=38) + hash(22)
    basename = f"{type_char}-{safe_id}-{hash_suffix}"

    return basename


if __name__ == "__main__":
    print(
        lakefs_repo_name(
            "model", "org/very-long-repository-name-with-version-v2.3.4-final"
        )
    )
