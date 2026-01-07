import pytest

from src.processing.chunkers.hierarchical import HierarchicalChunker
from src.processing.id_utils import attach_parent_ids, stable_chunk_id


@pytest.mark.unit
def test_hierarchical_chunking_sets_parent_ids():
    config = {
        "chunking": {
            "defaults": {"chunk_size": 50, "chunk_overlap": 0, "strategy": "recursive"},
            "huge_docs": {
                "enabled": True,
                "levels": {
                    "coarse": {"chunk_size": 120, "chunk_overlap": 0},
                    "mid": {"chunk_size": 60, "chunk_overlap": 0},
                    "fine": {"chunk_size": 30, "chunk_overlap": 0},
                },
            },
        }
    }
    chunker = HierarchicalChunker(config)
    text = "Sentence one. Sentence two. Sentence three. Sentence four. " * 5
    chunks = chunker.chunk_with_metadata(text, {"source_type": "pdf"})

    # Add source_id to all chunks for document-scoped parent lookups
    metadatas = []
    for _, metadata in chunks:
        metadata["source_id"] = "doc-1"
        metadatas.append(metadata)

    ids = [
        stable_chunk_id("doc-1", metadata.get("chunk_level", "mid"), idx, chunk_text)
        for idx, (chunk_text, metadata) in enumerate(chunks)
    ]

    attach_parent_ids(metadatas, ids)

    fine_chunks = [metadata for metadata in metadatas if metadata.get("chunk_level") == "fine"]
    assert fine_chunks
    assert all(metadata.get("parent_id") for metadata in fine_chunks)
