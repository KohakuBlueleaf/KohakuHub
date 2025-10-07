"""Git-LakeFS bridge for translating Git operations to LakeFS REST API.

This module provides a bridge between Git protocol operations and LakeFS REST API,
allowing Git clients to inte                    commit_info = await self.lakefs_client.get_commit(
                        repository=self.lakefs_repo, commit_id=commit_id
                    )

                    # Create commit
                    author_name = commit_info.get("committer", "KohakuHub")
                    message = commit_info.get("message", "Initial commit")
                    timestamp = commit_info.get("creation_date", 0)

                # Handle both integer timestamps and ISO date strings
                if isinstance(timestamp, str):epositories stored in LakeFS.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

import pygit2

from kohakuhub.api.utils.lakefs import get_lakefs_client, lakefs_repo_name
from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("GIT_LAKEFS")


class GitLakeFSBridge:
    """Bridge between Git operations and LakeFS REST API."""

    def __init__(self, repo_type: str, namespace: str, name: str):
        """Initialize Git-LakeFS bridge.

        Args:
            repo_type: Repository type (model/dataset/space)
            namespace: Repository namespace
            name: Repository name
        """
        self.repo_type = repo_type
        self.namespace = namespace
        self.name = name
        self.repo_id = f"{namespace}/{name}"
        self.lakefs_repo = lakefs_repo_name(repo_type, self.repo_id)
        self.lakefs_client = get_lakefs_client()

    async def get_refs(self, branch: str = "main") -> Dict[str, str]:
        """Get Git refs from LakeFS branch.

        Args:
            branch: Branch name to get refs from

        Returns:
            Dictionary mapping ref names to commit SHAs (Git SHA-1 format)
        """
        refs = {}

        try:
            # Create temporary git repository to build commit
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_repo_path = Path(temp_dir) / "repo"
                temp_repo_path.mkdir()

                # Initialize bare repository
                repo = pygit2.init_repository(str(temp_repo_path), bare=True)

                # Fetch all objects from LakeFS and build Git tree
                commit_oid = await self._populate_git_repo(repo, branch)

                if commit_oid:
                    # Use the Git commit SHA we created
                    git_commit_sha = str(commit_oid)

                    # Map Git commit to refs
                    refs[f"refs/heads/{branch}"] = git_commit_sha
                    refs["HEAD"] = git_commit_sha

        except Exception as e:
            logger.exception("Failed to get refs for {}/{}/{}".format(self.repo_type, self.namespace, self.name), e)

        return refs

    async def build_pack_file(
        self, wants: List[str], haves: List[str], branch: str = "main"
    ) -> bytes:
        """Build Git pack file with requested objects.

        Args:
            wants: List of commit SHAs client wants
            haves: List of commit SHAs client already has
            branch: Branch name

        Returns:
            Pack file bytes
        """
        try:
            # Create temporary git repository
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_repo_path = Path(temp_dir) / "repo"
                temp_repo_path.mkdir()

                # Initialize bare repository
                repo = pygit2.init_repository(str(temp_repo_path), bare=True)

                # Fetch all objects from LakeFS and build Git tree
                commit_oid = await self._populate_git_repo(repo, branch)

                if not commit_oid:
                    logger.warning("No commit created, returning empty pack")
                    return self._create_empty_pack()

                # Create pack file
                pack_data = self._create_pack_file(repo, wants, haves)

                return pack_data

        except Exception as e:
            logger.exception("Failed to build pack file", e)
            return self._create_empty_pack()

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

    async def _populate_git_repo(self, repo: pygit2.Repository, branch: str) -> pygit2.Oid | None:
        """Populate Git repository with objects from LakeFS.

        Args:
            repo: pygit2 Repository object
            branch: Branch name to fetch from

        Returns:
            Commit OID if successful, None otherwise
        """
        # List all objects in LakeFS branch
        try:
            all_objects = []
            after = ""
            has_more = True

            while has_more:
                result = await self.lakefs_client.list_objects(
                    repository=self.lakefs_repo,
                    ref=branch,
                    prefix="",
                    after=after,
                    amount=1000,
                )

                all_objects.extend(result.get("results", []))

                pagination = result.get("pagination", {})
                has_more = pagination.get("has_more", False)
                if has_more:
                    after = pagination.get("next_offset", "")

            logger.info(f"Found {len(all_objects)} objects in LakeFS")

            if not all_objects:
                logger.warning("No objects found in LakeFS repository")
                return None

            # Build nested tree structure
            tree_oid = await self._build_tree_from_objects(repo, all_objects, branch)

            if not tree_oid:
                return None

            # Get branch commit info
            branch_info = await self.lakefs_client.get_branch(
                repository=self.lakefs_repo, branch=branch
            )

            commit_id = branch_info.get("commit_id")

            # Get commit details
            try:
                if not commit_id:
                    logger.warning("No commit_id found, using default commit info")
                    author_name = "KohakuHub"
                    message = "Initial commit"
                    timestamp = 0
                else:
                    commit_info = await self.lakefs_client.get_commit(
                        repository=self.lakefs_repo, commit_id=commit_id
                    )

                # Create commit
                author_name = commit_info.get("committer", "KohakuHub")
                message = commit_info.get("message", "Initial commit")
                timestamp = commit_info.get("creation_date", 0)

                # Handle both integer timestamps and ISO date strings
                if isinstance(timestamp, str):
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        timestamp = int(dt.timestamp())
                    except:
                        timestamp = 0

                author = pygit2.Signature(author_name, "noreply@kohakuhub.local", int(timestamp))
                committer = author

                commit_oid = repo.create_commit(
                    f"refs/heads/{branch}",
                    author,
                    committer,
                    message,
                    tree_oid,
                    [],  # No parents for now
                )

                logger.success(f"Created commit: {commit_oid}")
                return commit_oid

            except Exception as e:
                logger.exception("Failed to create commit", e)
                return None

        except Exception as e:
            logger.exception("Failed to populate git repo", e)
            return None

    async def _build_tree_from_objects(
        self, repo: pygit2.Repository, objects: List[dict], branch: str
    ) -> pygit2.Oid | None:
        """Build nested Git tree from flat list of LakeFS objects.

        Args:
            repo: pygit2 Repository
            objects: List of LakeFS object metadata
            branch: Branch name

        Returns:
            Tree OID if successful, None otherwise
        """
        # Build directory tree structure
        tree_entries = {}  # path -> (oid, mode)

        for obj in objects:
            if obj.get("path_type") != "object":
                continue

            path = obj["path"]

            # Download object content
            try:
                content = await self.lakefs_client.get_object(
                    repository=self.lakefs_repo, ref=branch, path=path
                )

                # Create blob
                blob_oid = repo.create_blob(content)
                tree_entries[path] = (blob_oid, pygit2.GIT_FILEMODE_BLOB)

                logger.debug(f"Created blob for {path}: {blob_oid}")

            except Exception as e:
                logger.warning(f"Failed to download {path}: {e}")

        if not tree_entries:
            return None

        # Build nested tree structure from flat paths
        return self._create_nested_tree(repo, tree_entries)

    def _create_nested_tree(
        self, repo: pygit2.Repository, entries: Dict[str, Tuple[pygit2.Oid, int]]
    ) -> pygit2.Oid:
        """Create nested Git tree from flat path entries.

        Args:
            repo: pygit2 Repository
            entries: Dict mapping paths to (oid, mode) tuples

        Returns:
            Root tree OID
        """
        # Organize entries by directory
        dir_contents = {}  # dir_path -> {name: (oid, mode)}

        for path, (oid, mode) in entries.items():
            parts = path.split("/")

            # Add entry to each parent directory
            for i in range(len(parts)):
                if i == len(parts) - 1:
                    # This is the file itself
                    dir_path = "/".join(parts[:i]) if i > 0 else ""
                    name = parts[i]
                    if dir_path not in dir_contents:
                        dir_contents[dir_path] = {}
                    dir_contents[dir_path][name] = (oid, mode)

        # Build trees bottom-up (deepest first)
        sorted_dirs = sorted(dir_contents.keys(), key=lambda x: x.count("/"), reverse=True)

        dir_oids = {}  # dir_path -> tree_oid

        for dir_path in sorted_dirs:
            tree_builder = repo.TreeBuilder()

            for name, (entry_oid, entry_mode) in dir_contents[dir_path].items():
                # Check if this is a subdirectory
                subdir_path = f"{dir_path}/{name}" if dir_path else name
                if subdir_path in dir_oids:
                    # This is a directory - use its tree OID
                    tree_builder.insert(name, dir_oids[subdir_path], pygit2.GIT_FILEMODE_TREE)
                else:
                    # This is a file - use the blob OID
                    tree_builder.insert(name, entry_oid, entry_mode)

            tree_oid = tree_builder.write()
            dir_oids[dir_path] = tree_oid

        # Return root tree - fallback to last directory if root is empty
        if "" in dir_oids:
            return dir_oids[""]
        elif sorted_dirs:
            return dir_oids[sorted_dirs[-1]]
        else:
            # Create an empty tree if no directories
            tree_builder = repo.TreeBuilder()
            return tree_builder.write()

    def _create_pack_file(
        self, repo: pygit2.Repository, wants: List[str], haves: List[str]
    ) -> bytes:
        """Create Git pack file.

        Args:
            repo: pygit2 Repository
            wants: List of wanted commit SHAs
            haves: List of existing commit SHAs

        Returns:
            Pack file bytes
        """
        try:
            # Get the branch ref (we use main)
            try:
                ref = repo.references["refs/heads/main"]
                head_commit_oid = ref.target
            except KeyError:
                # Fallback to first want if branch not found
                if wants:
                    head_commit_oid = pygit2.Oid(hex=wants[0])
                else:
                    logger.warning("No ref or wants found, returning empty pack")
                    return self._create_empty_pack()

            # Walk through all objects reachable from wanted commits
            # Use pygit2.SortMode.TOPOLOGICAL instead of the integer constant
            walker = repo.walk(head_commit_oid, pygit2.enums.SortMode.TOPOLOGICAL)

            # Collect all objects to pack
            oids_to_pack = set()

            for commit in walker:
                # Stop if we've reached a commit the client already has
                if str(commit.id) in haves:
                    break

                # Add commit
                oids_to_pack.add(commit.id)

                # Add tree and all blobs
                self._collect_tree_objects(repo, commit.tree_id, oids_to_pack)

            logger.info(f"Packing {len(oids_to_pack)} objects")

            # Use pygit2 PackBuilder to create pack
            # Write pack to a temporary directory, then read it back
            import os
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                # Create pack builder and add all objects
                pack_builder = pygit2.PackBuilder(repo)

                for oid in oids_to_pack:
                    pack_builder.add(oid)

                # Write pack to directory (pygit2 will create pack file inside)
                pack_builder.write(temp_dir)

                # Find the generated pack file
                pack_files = [f for f in os.listdir(temp_dir) if f.endswith(".pack")]
                if not pack_files:
                    logger.error("No pack file generated")
                    return self._create_empty_pack()

                pack_path = os.path.join(temp_dir, pack_files[0])

                # Read pack file
                with open(pack_path, "rb") as f:
                    pack_bytes = f.read()

                logger.success(f"Created pack file: {len(pack_bytes)} bytes")
                return pack_bytes

        except Exception as e:
            logger.exception("Failed to create pack file", e)
            return self._create_empty_pack()

    def _collect_tree_objects(
        self, repo: pygit2.Repository, tree_oid: pygit2.Oid, oids: set
    ):
        """Recursively collect all objects in a tree.

        Args:
            repo: pygit2 Repository
            tree_oid: Tree OID to collect from
            oids: Set to add collected OIDs to
        """
        if tree_oid in oids:
            return

        oids.add(tree_oid)
        tree = repo[tree_oid]

        # Type assertion to help type checker - tree should be a Tree object
        if not isinstance(tree, pygit2.Tree):
            return

        for entry in tree:
            oids.add(entry.id)

            # Recursively collect subtrees
            if entry.filemode == pygit2.GIT_FILEMODE_TREE:
                self._collect_tree_objects(repo, entry.id, oids)

    async def unpack_and_push(
        self, pack_data: bytes, ref_updates: List[Tuple[str, str, str]], branch: str
    ) -> bool:
        """Unpack Git pack file and push to LakeFS.

        Args:
            pack_data: Pack file bytes
            ref_updates: List of (old_sha, new_sha, ref_name) tuples
            branch: Target branch name

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create temporary repository to unpack
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_repo_path = Path(temp_dir) / "repo"
                temp_repo_path.mkdir()

                repo = pygit2.init_repository(str(temp_repo_path), bare=True)

                # Write pack file
                pack_path = Path(temp_dir) / "incoming.pack"
                pack_path.write_bytes(pack_data)

                # Index pack (this extracts objects)
                # Note: pygit2 doesn't have direct pack indexing API
                # In production, we'd use git index-pack via subprocess
                logger.warning("Pack unpacking not fully implemented yet")

                # For each ref update, push objects to LakeFS
                for old_sha, new_sha, ref_name in ref_updates:
                    logger.info(f"Processing ref update: {ref_name} {old_sha} -> {new_sha}")

                    # TODO: Extract objects from pack and upload to LakeFS
                    # 1. Walk commit tree from new_sha
                    # 2. Upload blobs to S3 or LakeFS
                    # 3. Update LakeFS branch

                return True

        except Exception as e:
            logger.exception("Failed to unpack and push", e)
            return False

    async def create_lakefs_commit(
        self, branch: str, message: str, author: str, tree_data: dict
    ) -> str:
        """Create commit in LakeFS from Git tree data.

        Args:
            branch: Branch name
            message: Commit message
            author: Author name
            tree_data: Dictionary of path -> content

        Returns:
            Commit ID
        """
        # Upload all files
        for path, content in tree_data.items():
            if isinstance(content, str):
                content = content.encode("utf-8")

            # Upload to LakeFS
            await self.lakefs_client.upload_object(
                repository=self.lakefs_repo, branch=branch, path=path, content=content
            )

        # Create commit
        commit_result = await self.lakefs_client.commit(
            repository=self.lakefs_repo,
            branch=branch,
            message=message,
            metadata={"author": author},
        )

        return commit_result["id"]


def compute_git_blob_sha(content: bytes) -> str:
    """Compute Git blob SHA-1.

    Args:
        content: File content

    Returns:
        SHA-1 hash in hex format
    """
    size = len(content)
    header = f"blob {size}\x00".encode()
    sha = hashlib.sha1()
    sha.update(header + content)
    return sha.hexdigest()


def compute_git_tree_sha(entries: List[Tuple[str, str, str]]) -> str:
    """Compute Git tree SHA-1.

    Args:
        entries: List of (mode, name, sha) tuples

    Returns:
        SHA-1 hash in hex format
    """
    # Sort entries
    sorted_entries = sorted(entries, key=lambda x: x[1])

    # Build tree content
    tree_content = b""
    for mode, name, sha_hex in sorted_entries:
        sha_bytes = bytes.fromhex(sha_hex)
        tree_content += f"{mode} {name}\x00".encode() + sha_bytes

    # Compute hash
    size = len(tree_content)
    header = f"tree {size}\x00".encode()
    sha = hashlib.sha1()
    sha.update(header + tree_content)
    return sha.hexdigest()
