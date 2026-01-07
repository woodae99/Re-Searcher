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
