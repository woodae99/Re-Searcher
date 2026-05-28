import pytest

from src.processing.id_utils import attach_parent_ids, stable_chunk_id


@pytest.mark.unit
def test_stable_chunk_id_is_deterministic():
    chunk_id_1 = stable_chunk_id("doc-1", "mid", 0, "hello world")
    chunk_id_2 = stable_chunk_id("doc-1", "mid", 0, "hello world")

    assert chunk_id_1 == chunk_id_2


@pytest.mark.unit
def test_stable_chunk_id_changes_with_text():
    base_id = stable_chunk_id("doc-1", "mid", 0, "hello world")
    changed_id = stable_chunk_id("doc-1", "mid", 0, "hello world!")

    assert base_id != changed_id


def test_stable_chunk_id_changes_with_variant():
    base_id = stable_chunk_id("doc-1", "mid", 0, "hello world", variant="0")
    changed_id = stable_chunk_id("doc-1", "mid", 0, "hello world", variant="1")

    assert base_id != changed_id


@pytest.mark.unit
def test_attach_parent_ids_sets_parent_id():
    # source_id is required for document-scoped parent lookups
    metadatas = [
        {"source_id": "doc-1", "chunk_level": "mid", "chunk_index": 0},
        {"source_id": "doc-1", "chunk_level": "fine", "chunk_index": 0, "parent_level": "mid", "parent_ordinal": 0},
    ]
    ids = [
        stable_chunk_id("doc-1", "mid", 0, "mid text"),
        stable_chunk_id("doc-1", "fine", 1, "fine text"),
    ]

    attach_parent_ids(metadatas, ids)

    assert metadatas[1]["parent_id"] == ids[0]


@pytest.mark.unit
def test_attach_parent_ids_scoping_respects_source_id():
    """Parent lookup should be scoped by source_id to prevent cross-document collisions."""
    # Two documents, each with a mid chunk at ordinal 0
    metadatas = [
        {"source_id": "doc-A", "chunk_level": "mid", "chunk_index": 0},
        {"source_id": "doc-B", "chunk_level": "mid", "chunk_index": 0},
        # Fine chunk from doc-A should link to doc-A's mid, not doc-B's
        {"source_id": "doc-A", "chunk_level": "fine", "chunk_index": 0, "parent_level": "mid", "parent_ordinal": 0},
        # Fine chunk from doc-B should link to doc-B's mid, not doc-A's
        {"source_id": "doc-B", "chunk_level": "fine", "chunk_index": 0, "parent_level": "mid", "parent_ordinal": 0},
    ]
    ids = [
        stable_chunk_id("doc-A", "mid", 0, "doc-A mid text"),
        stable_chunk_id("doc-B", "mid", 0, "doc-B mid text"),
        stable_chunk_id("doc-A", "fine", 0, "doc-A fine text"),
        stable_chunk_id("doc-B", "fine", 0, "doc-B fine text"),
    ]

    attach_parent_ids(metadatas, ids)

    # doc-A's fine chunk should link to doc-A's mid chunk
    assert metadatas[2]["parent_id"] == ids[0]
    # doc-B's fine chunk should link to doc-B's mid chunk
    assert metadatas[3]["parent_id"] == ids[1]


@pytest.mark.unit
def test_attach_parent_ids_no_overwrite():
    """attach_parent_ids should not overwrite existing parent_id."""
    existing_parent_id = "existing-parent-id"
    metadatas = [
        {"source_id": "doc-1", "chunk_level": "mid", "chunk_index": 0},
        {
            "source_id": "doc-1",
            "chunk_level": "fine",
            "chunk_index": 0,
            "parent_level": "mid",
            "parent_ordinal": 0,
            "parent_id": existing_parent_id,  # Already set
        },
    ]
    ids = [
        stable_chunk_id("doc-1", "mid", 0, "mid text"),
        stable_chunk_id("doc-1", "fine", 0, "fine text"),
    ]

    attach_parent_ids(metadatas, ids)

    # Existing parent_id should be preserved, not overwritten
    assert metadatas[1]["parent_id"] == existing_parent_id
