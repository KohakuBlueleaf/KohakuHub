"""Tests for pure Git object construction helpers."""

from __future__ import annotations

import hashlib
import struct
import zlib

from kohakuhub.api.git.utils.objects import (
    build_nested_trees,
    compute_git_object_sha1,
    create_blob_object,
    create_commit_object,
    create_empty_pack_file,
    create_pack_file,
    create_tree_object,
    encode_pack_object_header,
)


def test_compute_git_object_sha1_matches_blob_builder():
    content = b"hello world\n"

    sha1 = compute_git_object_sha1("blob", content)
    blob_sha1, blob_data = create_blob_object(content)

    assert sha1 == blob_sha1
    assert blob_data == b"blob 12\x00hello world\n"


def test_create_tree_object_sorts_directories_with_git_semantics():
    entries = [
        ("100644", "z-last.txt", "3" * 40),
        ("40000", "docs", "1" * 40),
        ("100644", "docs.txt", "2" * 40),
    ]

    sha1, tree_data = create_tree_object(entries)
    payload = tree_data.split(b"\0", 1)[1]

    assert sha1 == hashlib.sha1(tree_data).hexdigest()
    assert payload.index(b"100644 docs.txt\x00") < payload.index(b"40000 docs\x00")
    assert payload.index(b"40000 docs\x00") < payload.index(b"100644 z-last.txt\x00")


def test_create_commit_object_includes_parents_and_metadata():
    sha1, commit_data = create_commit_object(
        tree_sha1="1" * 40,
        parent_sha1s=["2" * 40, "3" * 40],
        author_name="Alice",
        author_email="alice@example.com",
        committer_name="Bob",
        committer_email="bob@example.com",
        author_timestamp=1700000000,
        committer_timestamp=1700000100,
        timezone="+0800",
        message="Initial commit",
    )
    content = commit_data.split(b"\0", 1)[1].decode("utf-8")

    assert sha1 == hashlib.sha1(commit_data).hexdigest()
    assert "tree " + "1" * 40 in content
    assert "parent " + "2" * 40 in content
    assert "parent " + "3" * 40 in content
    assert "author Alice <alice@example.com> 1700000000 +0800" in content
    assert "committer Bob <bob@example.com> 1700000100 +0800" in content
    assert content.endswith("\nInitial commit")


def test_encode_pack_object_header_supports_small_and_large_sizes():
    assert encode_pack_object_header(3, 10) == bytes([0x3A])
    assert encode_pack_object_header(1, 0x1234) == bytes([0x94, 0xA3, 0x02])


def test_create_pack_file_and_empty_pack_file_have_valid_git_structure():
    blob_sha1, blob_data = create_blob_object(b"payload")
    pack_bytes = create_pack_file([(3, blob_data)])
    empty_pack = create_empty_pack_file()

    assert pack_bytes.startswith(b"PACK" + struct.pack(">I", 2) + struct.pack(">I", 1))
    assert hashlib.sha1(pack_bytes[:-20]).digest() == pack_bytes[-20:]
    compressed_payload = pack_bytes[12 + 1 : -20]
    assert zlib.decompress(compressed_payload) == b"payload"

    assert empty_pack == b"PACK" + struct.pack(">I", 2) + struct.pack(">I", 0) + hashlib.sha1(
        b"PACK" + struct.pack(">I", 2) + struct.pack(">I", 0)
    ).digest()


def test_build_nested_trees_creates_root_and_nested_directories():
    root_sha1, tree_objects = build_nested_trees(
        [
            ("100644", "README.md", "a" * 40),
            ("100644", "docs/guide.md", "b" * 40),
            ("100644", "docs/images/logo.png", "c" * 40),
        ]
    )

    assert root_sha1
    assert len(tree_objects) == 3
    assert all(obj_type == 2 for obj_type, _tree_data in tree_objects)


def test_build_nested_trees_handles_single_root_file():
    root_sha1, tree_objects = build_nested_trees([("100644", "file.txt", "d" * 40)])

    assert root_sha1
    assert len(tree_objects) == 1
