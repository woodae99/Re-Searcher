"""Smoke tests for the Re-Searcher indexing pipeline.

These tests verify basic end-to-end functionality with small inputs.
They are designed to catch obvious regressions quickly.
"""

import pytest

from src.processing.router import ChunkerRouter
from src.processing.oversize_guard import OversizeGuard
from src.processing.id_utils import attach_parent_ids, stable_chunk_id


@pytest.mark.unit
def test_small_run_with_v06_router_produces_mid_chunks():
    """
    Smoke test: v0.6 router should produce mid chunks even for huge documents.

    This test verifies the core chunking pipeline works with:
    - Router enabled in v0.6 single-grain mode
    - Huge-doc settings do not route production chunks to hierarchy
    """
    config = {
        "chunking": {
            "mode": "v0.6_single_grain",
            "router_enabled": True,
            "markdown": {"enabled": False},  # Disable to force huge_docs path
            "huge_docs": {
                "enabled": True,
                "huge_doc_tokens": 50,  # Very low threshold to trigger hierarchical
                "levels": {
                    "coarse": {"chunk_size": 200, "chunk_overlap": 20},
                    "mid": {"chunk_size": 100, "chunk_overlap": 10},
                    "fine": {"chunk_size": 50, "chunk_overlap": 5},
                },
            },
            "defaults": {"chunk_size": 100, "chunk_overlap": 10, "strategy": "recursive"},
        }
    }

    router = ChunkerRouter(config)

    # Create a document that exceeds huge_doc_tokens threshold (50 tokens = ~200 chars)
    documents = [
        {
            "doc_id": f"test-doc-{i}",
            "source_id": f"test-doc-{i}",
            "source_type": "pdf",
            "text": f"Document {i} content. " * 100,  # ~2000 chars = ~500 tokens
        }
        for i in range(3)
    ]

    all_chunks = []
    for doc in documents:
        metadata = {
            "doc_id": doc["doc_id"],
            "source_id": doc["source_id"],
            "source_type": doc["source_type"],
        }
        chunks = router.chunk_with_metadata(doc["text"], metadata)
        all_chunks.extend(chunks)

    # Should have produced chunks
    assert len(all_chunks) > 0

    levels_found = {meta.get("chunk_level") for _, meta in all_chunks}

    assert levels_found == {"mid"}
    assert all("parent_id" not in meta for _, meta in all_chunks)


@pytest.mark.unit
def test_small_run_with_legacy_router_produces_hierarchical_chunks():
    """Legacy router mode may still produce hierarchical chunks for experiments."""
    config = {
        "chunking": {
            "mode": "legacy_router",
            "router_enabled": True,
            "markdown": {"enabled": False},
            "huge_docs": {
                "enabled": True,
                "huge_doc_tokens": 50,
                "levels": {
                    "coarse": {"chunk_size": 200, "chunk_overlap": 20},
                    "mid": {"chunk_size": 100, "chunk_overlap": 10},
                    "fine": {"chunk_size": 50, "chunk_overlap": 5},
                },
            },
            "defaults": {"chunk_size": 100, "chunk_overlap": 10, "strategy": "recursive"},
        }
    }

    router = ChunkerRouter(config)
    metadata = {"doc_id": "legacy-doc", "source_id": "legacy-doc", "source_type": "pdf"}
    chunks = router.chunk_with_metadata("Document content. " * 100, metadata)
    levels_found = {meta.get("chunk_level") for _, meta in chunks}

    assert "coarse" in levels_found
    assert "fine" in levels_found


@pytest.mark.unit
def test_smoke_oversize_guard_with_router():
    """
    Smoke test: Oversize guard should work after router processing.

    This tests the full flow: Router -> Oversize Guard -> valid chunks.
    """
    config = {
        "chunking": {
            "mode": "v0.6_single_grain",
            "router_enabled": True,
            "huge_docs": {"enabled": False},
            "markdown": {"enabled": False},
            "defaults": {"chunk_size": 50, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }

    router = ChunkerRouter(config)
    guard = OversizeGuard(max_tokens=100, policy="split")

    # Create text that will be chunked by default chunker
    text = "This is a test document with enough content to be chunked. " * 20
    metadata = {"source_id": "test-doc", "source_type": "text"}

    # Route through chunker
    chunks = router.chunk_with_metadata(text, metadata)
    assert len(chunks) > 0

    # Pass through oversize guard
    guarded_chunks = guard.process(chunks)

    # Guard should pass all chunks (none should be oversize with our settings)
    assert len(guarded_chunks) >= len(chunks) or guard.stats.split > 0
    assert guard.stats.skipped == 0


@pytest.mark.unit
def test_smoke_id_generation_and_parent_attachment():
    """
    Smoke test: ID generation and parent attachment should work together.

    This tests the full ID flow: generate IDs -> attach parent_ids.
    """
    source_id = "smoke-test-doc"

    # Simulate hierarchical chunks from router
    chunks = [
        ("Coarse content covering the whole section.", {"chunk_level": "coarse", "chunk_index": 0}),
        ("Mid content paragraph one.", {"chunk_level": "mid", "chunk_index": 0, "parent_level": "coarse", "parent_ordinal": 0}),
        ("Mid content paragraph two.", {"chunk_level": "mid", "chunk_index": 1, "parent_level": "coarse", "parent_ordinal": 0}),
        ("Fine content detail one.", {"chunk_level": "fine", "chunk_index": 0, "parent_level": "mid", "parent_ordinal": 0}),
        ("Fine content detail two.", {"chunk_level": "fine", "chunk_index": 1, "parent_level": "mid", "parent_ordinal": 1}),
    ]

    # Add source_id to all metadatas
    for text, meta in chunks:
        meta["source_id"] = source_id

    # Generate stable IDs
    ids = [
        stable_chunk_id(source_id, meta["chunk_level"], meta["chunk_index"], text)
        for text, meta in chunks
    ]

    # IDs should be unique
    assert len(set(ids)) == len(ids)

    # IDs should be deterministic (same call = same result)
    ids_again = [
        stable_chunk_id(source_id, meta["chunk_level"], meta["chunk_index"], text)
        for text, meta in chunks
    ]
    assert ids == ids_again

    # Attach parent IDs
    metadatas = [meta for _, meta in chunks]
    attach_parent_ids(metadatas, ids)

    # Check parent relationships
    # Mid chunks should have parent_id pointing to coarse
    assert metadatas[1]["parent_id"] == ids[0]  # mid-0 -> coarse-0
    assert metadatas[2]["parent_id"] == ids[0]  # mid-1 -> coarse-0

    # Fine chunks should have parent_id pointing to their respective mid
    assert metadatas[3]["parent_id"] == ids[1]  # fine-0 -> mid-0
    assert metadatas[4]["parent_id"] == ids[2]  # fine-1 -> mid-1


@pytest.mark.unit
def test_smoke_full_v06_chunking_pipeline():
    """
    Smoke test: Full chunking pipeline from text to final chunks.

    Tests: Router -> Chunks -> IDs -> Oversize guard.
    """
    config = {
        "chunking": {
            "mode": "v0.6_single_grain",
            "router_enabled": True,
            "huge_docs": {
                "enabled": True,
                "huge_doc_tokens": 20,  # Very low to trigger hierarchical
                "levels": {
                    "coarse": {"chunk_size": 200, "chunk_overlap": 20},
                    "mid": {"chunk_size": 80, "chunk_overlap": 10},
                    "fine": {"chunk_size": 40, "chunk_overlap": 5},
                },
            },
            "markdown": {"enabled": False},
            "defaults": {"chunk_size": 100, "chunk_overlap": 10, "strategy": "recursive"},
        }
    }

    router = ChunkerRouter(config)
    guard = OversizeGuard(max_tokens=500, policy="split")

    # Create a substantial document
    text = (
        "Introduction to the topic. This section provides background information.\n\n"
        "First major point with supporting details and examples.\n\n"
        "Second major point building on the previous content.\n\n"
        "Conclusion summarizing the key findings."
    ) * 5  # Repeat to ensure it's "huge"

    metadata = {"source_id": "smoke-doc", "doc_id": "smoke-doc", "source_type": "pdf"}

    # Step 1: Route and chunk
    chunks = router.chunk_with_metadata(text, metadata)
    assert len(chunks) > 0
    assert {chunk_meta.get("chunk_level") for _, chunk_meta in chunks} == {"mid"}

    # Step 2: Generate IDs
    ids = []
    for i, (chunk_text, chunk_meta) in enumerate(chunks):
        level = chunk_meta.get("chunk_level", "default")
        chunk_index = chunk_meta.get("chunk_index", i)
        chunk_id = stable_chunk_id(metadata["source_id"], level, chunk_index, chunk_text)
        ids.append(chunk_id)

    assert len(ids) == len(chunks)
    assert len(set(ids)) == len(ids)  # All unique

    # Step 3: v0.6 chunks do not carry parent metadata
    metadatas = [meta for _, meta in chunks]
    assert all("parent_id" not in metadata for metadata in metadatas)

    # Step 4: Oversize guard
    guarded_chunks = guard.process(chunks)
    assert len(guarded_chunks) > 0

    # Final verification
    assert guard.stats.passed + guard.stats.split > 0
    assert guard.stats.skipped == 0
