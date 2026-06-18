"""Tests for MCP formatters."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mcp_formatters.formatters import (
    format_search_results,
    format_error_response,
    format_source_chunks,
    format_list_sources,
    format_index_status,
)


def test_format_search_results():
    """Test formatting of search results."""
    # Mock pipeline results
    mock_results = [
        (
            "doc1-chunk-0",
            "This is the first chunk of text about process philosophy.",
            0.8523,
            {
                "title": "Process and Reality",
                "authors": "Whitehead, A.N.",
                "source_type": "zotero",
                "backlink": "zotero://select/items/123",
                "doi": "10.1234/example",
                "year": 1929,
            },
        ),
        (
            "doc2-chunk-1",
            "This discusses coaching theory and practice.",
            0.7891,
            {
                "title": "Coaching Notes",
                "authors": "Unknown",
                "source_type": "obsidian",
                "backlink": "obsidian://vault/LitNotes/coaching.md",
            },
        ),
    ]

    formatted = format_search_results(mock_results)

    # Verify structure
    assert len(formatted) == 2

    # Check first result
    result1 = formatted[0]
    assert result1["rank"] == 1
    assert result1["id"] == "doc1-chunk-0"
    assert result1["score"] == 0.8523
    assert result1["title"] == "Process and Reality"
    assert result1["authors"] == "Whitehead, A.N."
    assert result1["source_type"] == "zotero"
    assert result1["backlink"] == "zotero://select/items/123"
    assert result1["doi"] == "10.1234/example"
    assert result1["year"] == 1929

    # Check second result
    result2 = formatted[1]
    assert result2["rank"] == 2
    assert result2["id"] == "doc2-chunk-1"
    assert result2["score"] == 0.7891
    assert result2["title"] == "Coaching Notes"
    assert "doi" not in result2  # Should not include missing fields

    print("✅ All formatter tests passed!")


def test_format_search_results_surfaces_freshness_stamp():
    formatted = format_search_results(
        [
            (
                "doc1",
                "text",
                0.5,
                {"title": "T", "indexed_at": "2026-06-10T12:00:00Z"},
            )
        ]
    )

    assert formatted[0]["indexed_at"] == "2026-06-10T12:00:00Z"


def test_format_source_chunks_missing_fields_and_empty_page():
    text = format_source_chunks(
        {
            "source": {"identity_field": "source_id", "identity_value": "obsidian-note"},
            "total_matching": 0,
            "page": {"offset": 0, "limit": 50, "returned": 0},
            "ordering": {"field": "chunk_id", "id_tiebreak": True},
            "chunks": [],
        }
    )

    assert "Source: source_id=obsidian-note" in text
    assert "No chunks found." in text


def test_format_source_chunks_pagination_header_and_unknown_freshness():
    text = format_source_chunks(
        {
            "source": {"identity_field": "zotero_key", "identity_value": "ABC123"},
            "total_matching": 3,
            "page": {"offset": 1, "limit": 1, "returned": 1},
            "ordering": {"field": "chunk_index", "id_tiebreak": True},
            "chunks": [
                {
                    "chunk_id": "chunk-2",
                    "metadata": {"chunk_level": "mid", "title": "Example"},
                    "text": "Chunk text",
                }
            ],
        }
    )

    assert "Page: offset=1, limit=1, returned=1" in text
    assert "Freshness: unknown" in text
    assert "Chunk text" in text


def test_format_list_sources_missing_fields_empty_and_pagination():
    empty = format_list_sources(
        {
            "total_sources": 0,
            "page": {"offset": 0, "limit": 100, "returned": 0},
            "filters": {},
            "sources": [],
        }
    )

    assert "No sources found." in empty

    text = format_list_sources(
        {
            "total_sources": 1,
            "page": {"offset": 0, "limit": 1, "returned": 1},
            "filters": {"source_type": "obsidian"},
            "sources": [
                {
                    "identity_field": "source_id",
                    "identity_value": "obsidian-A.md",
                    "title": "A",
                    "item_type": "book",
                    "doi": "10.1234/example",
                    "abstract": "A" * 300,
                    "tags": "Process, Theory",
                    "venue": "Coaching Studies",
                    "language": "en",
                    "chunk_counts": {"mid": 2},
                    "total_chunks": 2,
                    "freshness": "2026-06-10T12:00:00Z",
                }
            ],
        }
    )

    assert "Page: offset=0, limit=1, returned=1" in text
    assert "Identity: source_id=obsidian-A.md" in text
    assert "Item Type: book" in text
    assert "DOI: 10.1234/example" in text
    assert "Tags: Process, Theory" in text
    assert "Abstract: " in text
    assert "A" * 300 not in text
    assert "Freshness: 2026-06-10T12:00:00Z" in text


def test_format_error_response():
    """Test error formatting."""
    error = ValueError("Test error message")
    formatted = format_error_response(error)

    assert formatted["error"] == "ValueError"
    assert formatted["message"] == "Test error message"

    print("✅ Error formatter test passed!")


def test_format_index_status_includes_ledger_drift():
    text = format_index_status(
        {
            "collection_name": "test",
            "endpoint": "memory",
            "git_sha": "abc123",
            "chroma_chunk_count": 4,
            "drift": 0,
            "registry": {
                "chunk_count": 4,
                "source_count": 2,
                "index_unit_count": 3,
                "backfill_complete": False,
                "ledger_drift": {
                    "ok": False,
                    "chunkless_unit_count": 1,
                    "expected_chunkless_unit_count": 0,
                    "unexpected_chunkless_unit_count": 1,
                    "unexpected_chunkless_unit_samples": [
                        {"unit_id": "zotero:Z1:note:MISSING"}
                    ],
                    "orphan_identity_count": 1,
                    "orphan_chunk_count": 2,
                    "orphan_identity_samples": [
                        {"identity_field": "zotero_key", "identity_value": "Z2"}
                    ],
                },
            },
        }
    )

    assert "Index Ledger Units: 3" in text
    assert "Ledger Drift:" in text
    assert "Chunkless text units: 1 (expected coverage-null: 0; unexpected: 1)" in text
    assert "Orphan chunk identities: 1 (2 chunks)" in text
    assert "Ledger Sync: DRIFT DETECTED" in text
    assert "zotero:Z1:note:MISSING" in text
    assert "zotero_key=Z2" in text


if __name__ == "__main__":
    test_format_search_results()
    test_format_error_response()
    print("\n✅ All tests passed successfully!")
