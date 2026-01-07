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

        # Core metadata fields
        result["title"] = metadata.get("title", "Untitled")
        result["authors"] = metadata.get("authors", "Unknown")
        result["source_type"] = metadata.get("source_type", "unknown")

        # Hierarchical chunking metadata (vNext)
        result["chunk_level"] = metadata.get("chunk_level", "unknown")
        if "parent_id" in metadata:
            result["parent_id"] = metadata["parent_id"]
        if "source_id" in metadata:
            result["source_id"] = metadata["source_id"]

        # Markdown/Obsidian-specific metadata
        if "heading_path" in metadata:
            result["heading_path"] = metadata["heading_path"]
        if "contains_code" in metadata:
            result["contains_code"] = metadata["contains_code"]

        # Reference metadata
        if "backlink" in metadata:
            result["backlink"] = metadata["backlink"]
        if "doi" in metadata:
            result["doi"] = metadata["doi"]
        if "year" in metadata:
            result["year"] = metadata["year"]
        if "url" in metadata:
            result["url"] = metadata["url"]

        formatted.append(result)

    return formatted


def format_chunk_context(
    chunk: Dict[str, Any],
    parent: Dict[str, Any] = None,
    siblings: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Format a chunk with its hierarchical context.

    Args:
        chunk: The main chunk data
        parent: Optional parent chunk for context
        siblings: Optional sibling chunks at the same level

    Returns:
        Formatted context dictionary
    """
    context = {
        "chunk": {
            "id": chunk.get("id"),
            "text": chunk.get("text"),
            "level": chunk.get("chunk_level", "unknown"),
            "title": chunk.get("title", "Untitled"),
            "source_type": chunk.get("source_type", "unknown"),
        }
    }

    if parent:
        context["parent"] = {
            "id": parent.get("id"),
            "text": parent.get("text"),
            "level": parent.get("chunk_level", "unknown"),
        }

    if siblings:
        context["siblings"] = [
            {
                "id": s.get("id"),
                "text_preview": s.get("text", "")[:200] + "..." if len(s.get("text", "")) > 200 else s.get("text", ""),
                "level": s.get("chunk_level", "unknown"),
            }
            for s in siblings
        ]

    return context


def format_hierarchy_info(metadata: Dict[str, Any]) -> str:
    """
    Format chunk hierarchy information as a readable string.

    Args:
        metadata: Chunk metadata dictionary

    Returns:
        Human-readable hierarchy description
    """
    level = metadata.get("chunk_level", "unknown")
    parts = [f"Level: {level}"]

    if "heading_path" in metadata:
        parts.append(f"Section: {metadata['heading_path']}")

    if "parent_id" in metadata:
        parts.append(f"Parent: {metadata['parent_id'][:40]}...")

    if "source_id" in metadata:
        parts.append(f"Document: {metadata['source_id']}")

    return " | ".join(parts)


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
    return stats
