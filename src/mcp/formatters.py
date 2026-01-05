"""Result formatters for MCP server.

This module handles formatting pipeline results into MCP-compatible formats.
Keeping formatting logic separate makes it easy to update when:
- Pipeline result structure changes
- Metadata schema evolves
- New fields are added to results
"""

from typing import Any, Dict, List, Tuple


def format_search_results(
    results: List[Tuple[str, str, float, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Format pipeline search results for MCP response.

    Args:
        results: List of (doc_id, text, score, metadata) tuples from pipeline

    Returns:
        List of formatted result dictionaries
    """
    formatted = []

    for rank, (doc_id, text, score, metadata) in enumerate(results, 1):
        # Build result with guaranteed fields
        result = {
            "rank": rank,
            "id": doc_id,
            "text": text,
            "score": round(score, 4),
        }

        # Add metadata fields - use .get() for resilience to schema changes
        # This approach means new metadata fields automatically appear in results
        # and missing fields don't cause errors
        result["title"] = metadata.get("title", "Untitled")
        result["authors"] = metadata.get("authors", "Unknown")
        result["source_type"] = metadata.get("source_type", "unknown")

        # Optional fields - only include if present
        if "backlink" in metadata:
            result["backlink"] = metadata["backlink"]

        if "doi" in metadata:
            result["doi"] = metadata["doi"]

        if "year" in metadata:
            result["year"] = metadata["year"]

        # Include any additional metadata fields not explicitly handled above
        # This future-proofs against new metadata being added to the pipeline
        for key, value in metadata.items():
            if key not in result and key not in ["title", "authors", "source_type",
                                                   "backlink", "doi", "year"]:
                result[key] = value

        formatted.append(result)

    return formatted


def format_error_response(error: Exception) -> Dict[str, Any]:
    """
    Format error information for MCP response.

    Args:
        error: Exception that occurred

    Returns:
        Error dictionary with type and message
    """
    return {
        "error": type(error).__name__,
        "message": str(error),
    }


def format_collection_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format collection statistics for MCP response.

    Args:
        stats: Statistics dictionary from vector store

    Returns:
        Formatted statistics dictionary
    """
    # Pass through stats as-is, but could add formatting/filtering here
    # if needed in the future
    return stats
