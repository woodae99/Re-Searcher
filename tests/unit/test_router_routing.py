import pytest

from src.processing.router import ChunkerRouter


@pytest.mark.unit
def test_router_routes_zotero_annotations_to_atomic():
    config = {"chunking": {"router_enabled": True}}
    router = ChunkerRouter(config)
    metadata = {"source_type": "zotero_annotation"}
    chunks = router.chunk_with_metadata("annotation text", metadata)

    assert chunks
    assert chunks[0][1]["chunk_level"] == "atomic"


@pytest.mark.unit
def test_router_routes_obsidian_to_markdown():
    config = {
        "chunking": {
            "router_enabled": True,
            "markdown": {"enabled": True, "header_levels": [1]},
            "defaults": {"chunk_size": 100, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = ChunkerRouter(config)
    metadata = {"source_type": "obsidian"}
    text = "# Title\n\nParagraph text."
    chunks = router.chunk_with_metadata(text, metadata)

    assert chunks
    assert chunks[0][1]["heading_path"] == "Title"


@pytest.mark.unit
def test_router_routes_huge_docs_to_hierarchical():
    config = {
        "chunking": {
            "router_enabled": True,
            "huge_docs": {
                "enabled": True,
                "huge_doc_tokens": 10,
                "levels": {"coarse": {"chunk_size": 40, "chunk_overlap": 0}},
            },
            "defaults": {"chunk_size": 20, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = ChunkerRouter(config)
    metadata = {"source_type": "pdf"}
    text = "This is a long document that should trigger hierarchical chunking." * 5
    chunks = router.chunk_with_metadata(text, metadata)

    assert any(chunk_metadata.get("chunk_level") == "coarse" for _, chunk_metadata in chunks)


@pytest.mark.unit
def test_router_fallback_to_default():
    """Router should fall back to default recursive chunker for unknown source types."""
    config = {
        "chunking": {
            "router_enabled": True,
            "markdown": {"enabled": False},  # Disable markdown to avoid heading detection
            "huge_docs": {"enabled": False},  # Disable huge_docs to force default path
            "defaults": {"chunk_size": 500, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = ChunkerRouter(config)
    # Use a source type that doesn't match any special handler and no markdown headings
    metadata = {"source_type": "generic_document"}
    text = "This is a generic document that should use the default recursive chunker."
    chunks = router.chunk_with_metadata(text, metadata)

    # Should produce chunks
    assert chunks

    # Default chunker produces "mid" level chunks, not specialized atomic or hierarchical mix
    # It should NOT be atomic (annotations only) and should NOT produce coarse+fine mix
    levels_found = {meta.get("chunk_level") for _, meta in chunks}

    # Should not have atomic (only for annotations)
    assert "atomic" not in levels_found

    # If hierarchical was triggered, we'd have coarse AND fine levels together
    # Default chunker only produces "mid" level
    assert not ({"coarse", "fine"} <= levels_found), "Hierarchical chunker was triggered unexpectedly"
