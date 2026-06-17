import pytest

from src.factories.chunker_factory import create_chunker


@pytest.mark.unit
def test_router_routes_zotero_annotations_to_atomic():
    config = {"chunking": {"mode": "v0.6_single_grain", "router_enabled": True}}
    router = create_chunker(config)
    metadata = {"source_type": "zotero_annotation"}
    chunks = router.chunk_with_metadata("annotation text", metadata)

    assert chunks
    assert len(chunks) == 1
    assert chunks[0][1]["chunk_level"] == "atomic"
    assert "parent_id" not in chunks[0][1]


@pytest.mark.unit
def test_router_routes_obsidian_to_markdown():
    config = {
        "chunking": {
            "mode": "v0.6_single_grain",
            "router_enabled": True,
            "markdown": {"enabled": True, "header_levels": [1]},
            "defaults": {"chunk_size": 100, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = create_chunker(config)
    metadata = {"source_type": "obsidian"}
    text = "# Title\n\nParagraph text."
    chunks = router.chunk_with_metadata(text, metadata)

    assert chunks
    assert {meta["chunk_level"] for _, meta in chunks} == {"mid"}
    assert chunks[0][1]["heading_path"] == "Title"
    assert all("parent_id" not in meta for _, meta in chunks)


@pytest.mark.unit
def test_v06_router_routes_huge_docs_to_mid_not_hierarchical():
    config = {
        "chunking": {
            "mode": "v0.6_single_grain",
            "router_enabled": True,
            "huge_docs": {
                "enabled": True,
                "huge_doc_tokens": 10,
                "levels": {"coarse": {"chunk_size": 40, "chunk_overlap": 0}},
            },
            "defaults": {"chunk_size": 20, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = create_chunker(config)
    metadata = {"source_type": "pdf"}
    text = "This is a long document that should trigger hierarchical chunking." * 5
    chunks = router.chunk_with_metadata(text, metadata)

    assert chunks
    levels = {chunk_metadata.get("chunk_level") for _, chunk_metadata in chunks}
    assert levels == {"mid"}
    assert all("parent_id" not in chunk_metadata for _, chunk_metadata in chunks)


@pytest.mark.unit
def test_legacy_router_can_still_route_huge_docs_to_hierarchical():
    config = {
        "chunking": {
            "mode": "legacy_router",
            "router_enabled": True,
            "huge_docs": {
                "enabled": True,
                "huge_doc_tokens": 10,
                "levels": {
                    "coarse": {"chunk_size": 40, "chunk_overlap": 0},
                    "mid": {"chunk_size": 20, "chunk_overlap": 0},
                    "fine": {"chunk_size": 10, "chunk_overlap": 0},
                },
            },
            "defaults": {"chunk_size": 20, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = create_chunker(config)
    metadata = {"source_type": "pdf"}
    text = "This is a long document that should trigger hierarchical chunking." * 5
    chunks = router.chunk_with_metadata(text, metadata)

    assert any(chunk_metadata.get("chunk_level") == "coarse" for _, chunk_metadata in chunks)
    assert any(chunk_metadata.get("chunk_level") == "fine" for _, chunk_metadata in chunks)


@pytest.mark.unit
def test_router_fallback_to_default():
    """Router should fall back to default recursive chunker for unknown source types."""
    config = {
        "chunking": {
            "mode": "v0.6_single_grain",
            "router_enabled": True,
            "markdown": {"enabled": False},  # Disable markdown to avoid heading detection
            "huge_docs": {"enabled": False},  # Disable huge_docs to force default path
            "defaults": {"chunk_size": 500, "chunk_overlap": 0, "strategy": "recursive"},
        }
    }
    router = create_chunker(config)
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

    assert levels_found == {"mid"}
    assert all("parent_id" not in meta for _, meta in chunks)
