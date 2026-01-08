"""Integration tests for parent chunk fetching.

Tests that the parent lookup mechanism works correctly with ChromaDB.
"""

import pytest

from src.processing.id_utils import attach_parent_ids, stable_chunk_id
from src.storage.chroma import ChromaVectorStore


@pytest.mark.integration
@pytest.mark.requires_chromadb
def test_chroma_parent_fetch(integration_config):
    """Test that parent chunks can be fetched by ID after indexing."""
    config = dict(integration_config)
    storage_config = dict(config.get("storage", {}))
    storage_config["collection_name"] = "test_parent_fetch"
    config["storage"] = storage_config

    try:
        store = ChromaVectorStore(config)
    except Exception as exc:
        pytest.skip(f"ChromaDB not available: {exc}")

    # Create hierarchical chunks: one mid (parent), two fine (children)
    mid_text = "This is the parent mid-level chunk with broader context."
    fine_text_1 = "First child fine chunk with specific detail."
    fine_text_2 = "Second child fine chunk with more detail."

    source_id = "test-doc-1"

    # Generate stable IDs
    mid_id = stable_chunk_id(source_id, "mid", 0, mid_text)
    fine_id_1 = stable_chunk_id(source_id, "fine", 0, fine_text_1)
    fine_id_2 = stable_chunk_id(source_id, "fine", 1, fine_text_2)

    # Create metadata with parent references
    metadatas = [
        {"source_id": source_id, "chunk_level": "mid", "chunk_index": 0},
        {"source_id": source_id, "chunk_level": "fine", "chunk_index": 0, "parent_level": "mid", "parent_ordinal": 0},
        {"source_id": source_id, "chunk_level": "fine", "chunk_index": 1, "parent_level": "mid", "parent_ordinal": 0},
    ]
    ids = [mid_id, fine_id_1, fine_id_2]

    # Attach parent IDs
    attach_parent_ids(metadatas, ids)

    # Verify parent_id was attached to fine chunks
    assert metadatas[1]["parent_id"] == mid_id
    assert metadatas[2]["parent_id"] == mid_id

    # Add to ChromaDB (using dummy embeddings for this test)
    texts = [mid_text, fine_text_1, fine_text_2]
    embeddings = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]

    store.add_documents(
        texts=texts,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids,
    )

    # Now test that we can fetch the parent by ID
    parent_results = store.get_by_ids([mid_id])

    assert len(parent_results) == 1
    fetched_id, fetched_text, fetched_metadata = parent_results[0]
    assert fetched_id == mid_id
    assert fetched_text == mid_text
    assert fetched_metadata["chunk_level"] == "mid"

    # Test that we can fetch parent from child's parent_id
    child_results = store.get_by_ids([fine_id_1])
    assert len(child_results) == 1
    child_id, child_text, child_metadata = child_results[0]

    # Fetch parent using the parent_id from child
    parent_id_from_child = child_metadata["parent_id"]
    parent_via_child = store.get_by_ids([parent_id_from_child])

    assert len(parent_via_child) == 1
    assert parent_via_child[0][0] == mid_id

    # Cleanup
    store.delete_collection()


@pytest.mark.integration
@pytest.mark.requires_chromadb
def test_reindex_stable_count(integration_config):
    """Test that reindexing the same documents doesn't balloon the collection."""
    config = dict(integration_config)
    storage_config = dict(config.get("storage", {}))
    storage_config["collection_name"] = "test_reindex_stable"
    config["storage"] = storage_config

    try:
        store = ChromaVectorStore(config)
    except Exception as exc:
        pytest.skip(f"ChromaDB not available: {exc}")

    source_id = "test-doc-1"
    texts = ["Chunk one content.", "Chunk two content.", "Chunk three content."]

    # Generate stable IDs
    ids = [stable_chunk_id(source_id, "default", i, text) for i, text in enumerate(texts)]
    metadatas = [{"source_id": source_id, "chunk_index": i} for i in range(len(texts))]
    embeddings = [[0.1 * i, 0.2 * i] for i in range(len(texts))]

    # First indexing
    store.add_documents(texts=texts, embeddings=embeddings, metadatas=metadatas, ids=ids)
    count_after_first = store.collection.count()
    assert count_after_first == 3

    # Second indexing with same IDs (simulating reindex)
    # ChromaDB's upsert behavior: same ID replaces existing document
    store.add_documents(texts=texts, embeddings=embeddings, metadatas=metadatas, ids=ids)
    count_after_second = store.collection.count()

    # Count should remain stable (3), not balloon to 6
    assert count_after_second == count_after_first

    # Third indexing
    store.add_documents(texts=texts, embeddings=embeddings, metadatas=metadatas, ids=ids)
    count_after_third = store.collection.count()

    assert count_after_third == count_after_first

    # Cleanup
    store.delete_collection()
