"""Tests for MCP formatters."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mcp.formatters import format_search_results, format_error_response


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


def test_format_error_response():
    """Test error formatting."""
    error = ValueError("Test error message")
    formatted = format_error_response(error)

    assert formatted["error"] == "ValueError"
    assert formatted["message"] == "Test error message"

    print("✅ Error formatter test passed!")


if __name__ == "__main__":
    test_format_search_results()
    test_format_error_response()
    print("\n✅ All tests passed successfully!")
