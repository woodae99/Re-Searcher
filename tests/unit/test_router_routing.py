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
