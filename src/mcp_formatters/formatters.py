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

        # Hierarchical chunking metadata
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
        if "indexed_at" in metadata:
            result["indexed_at"] = metadata["indexed_at"]
        if "source_mtime" in metadata:
            result["source_mtime"] = metadata["source_mtime"]

        # Parent context (added by expand_parents)
        if "parent_text" in metadata:
            result["parent_text"] = metadata["parent_text"]
            result["parent_metadata"] = metadata.get("parent_metadata", {})
        if "parent_contexts" in metadata:
            result["parent_contexts"] = metadata["parent_contexts"]

        formatted.append(result)

    return formatted


def _freshness_value(metadata: Dict[str, Any]) -> str:
    """Return the best available index/source freshness stamp."""
    return (
        metadata.get("indexed_at")
        or metadata.get("source_mtime")
        or metadata.get("source_hash")
        or "unknown"
    )


def _truncate_field(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def format_source_chunks(payload: Dict[str, Any]) -> str:
    """Format source chunk enumeration results for MCP response text."""
    source = payload.get("source", {})
    chunks = payload.get("chunks", [])
    page = payload.get("page", {})
    order = payload.get("ordering", {})

    identity_field = source.get("identity_field", "unknown")
    identity_value = source.get("identity_value", "unknown")

    parts = [
        "=== Source Chunks ===",
        f"Source: {identity_field}={identity_value}",
        f"Total Matching Chunks: {payload.get('total_matching', 0)}",
        (
            "Page: "
            f"offset={page.get('offset', 0)}, "
            f"limit={page.get('limit', 0)}, "
            f"returned={page.get('returned', len(chunks))}"
        ),
        (
            "Ordering: "
            f"{order.get('field', 'store order')}"
            f"{' (id tie-break)' if order.get('id_tiebreak') else ''}"
        ),
    ]

    if order.get("note"):
        parts.append(f"Ordering Note: {order['note']}")

    if not chunks:
        parts.append("\nNo chunks found.")
        return "\n".join(parts)

    for idx, chunk in enumerate(chunks, 1):
        metadata = chunk.get("metadata", {}) or {}
        parts.append(f"\n--- Chunk #{idx} ---")
        parts.append(f"Chunk ID: {chunk.get('chunk_id', 'unknown')}")
        parts.append(f"Chunk Level: {metadata.get('chunk_level', 'unknown')}")
        parts.append(f"Parent Chunk: {metadata.get('parent_id', 'none')}")
        parts.append(
            "Section: "
            f"{metadata.get('heading_path') or metadata.get('section') or 'unknown'}"
        )
        parts.append(f"Title: {metadata.get('title', 'Untitled')}")
        parts.append(f"Authors: {metadata.get('authors', 'Unknown')}")
        parts.append(f"Source Type: {metadata.get('source_type', 'unknown')}")
        parts.append(f"Freshness: {_freshness_value(metadata)}")

        if chunk.get("text") is not None:
            parts.append(f"\nText:\n{chunk.get('text', '')}")

    return "\n".join(parts)


def format_list_sources(payload: Dict[str, Any]) -> str:
    """Format source register results for MCP response text."""
    sources = payload.get("sources", [])
    page = payload.get("page", {})
    filters = payload.get("filters", {})

    parts = [
        "=== Source Register ===",
        f"Total Sources: {payload.get('total_sources', 0)}",
        (
            "Page: "
            f"offset={page.get('offset', 0)}, "
            f"limit={page.get('limit', 0)}, "
            f"returned={page.get('returned', len(sources))}"
        ),
        (
            "Identity Fields: "
            "zotero_key for Zotero sources; source_id for Obsidian/local sources"
        ),
    ]

    active_filters = {k: v for k, v in filters.items() if v}
    if active_filters:
        parts.append(f"Filters: {active_filters}")

    if not sources:
        parts.append("\nNo sources found.")
        return "\n".join(parts)

    for idx, source in enumerate(sources, 1):
        counts = source.get("chunk_counts", {}) or {}
        parts.append(f"\n--- Source #{idx} ---")
        parts.append(
            f"Identity: {source.get('identity_field', 'unknown')}={source.get('identity_value', 'unknown')}"
        )
        parts.append(f"Title: {source.get('title', 'Untitled')}")
        parts.append(f"Authors: {source.get('authors', 'Unknown')}")
        parts.append(f"Year: {source.get('year', 'unknown') or 'unknown'}")
        if source.get("item_type"):
            parts.append(f"Item Type: {source['item_type']}")
        parts.append(f"Source Type: {source.get('source_type', 'unknown')}")
        if source.get("venue"):
            parts.append(f"Venue: {source['venue']}")
        if source.get("doi"):
            parts.append(f"DOI: {source['doi']}")
        if source.get("language"):
            parts.append(f"Language: {source['language']}")
        if source.get("tags"):
            parts.append(f"Tags: {source['tags']}")
        if source.get("abstract"):
            parts.append(f"Abstract: {_truncate_field(source['abstract'])}")
        if source.get("extractor"):
            parts.append(f"Extractor: {source['extractor']}")
        if source.get("extract_quality"):
            parts.append(f"Extract Quality: {source['extract_quality']}")
        if source.get("extract_action"):
            parts.append(f"Extract Action: {source['extract_action']}")
        parts.append(f"Total Chunks: {source.get('total_chunks', 0)}")
        parts.append(
            "Chunk Counts: "
            f"coarse={counts.get('coarse', 0)}, "
            f"mid={counts.get('mid', 0)}, "
            f"fine={counts.get('fine', 0)}, "
            f"atomic={counts.get('atomic', 0)}, "
            f"unknown={counts.get('unknown', 0)}"
        )
        parts.append(f"Freshness: {source.get('freshness', 'unknown')}")
        if source.get("collections"):
            parts.append(f"Collections: {source['collections']}")
        if source.get("backlink"):
            parts.append(f"Link: {source['backlink']}")

    return "\n".join(parts)


def format_survey_sources(payload: Dict[str, Any]) -> str:
    """Format source-level survey results for MCP response text."""
    sources = payload.get("sources", [])
    page = payload.get("page", {}) or {}
    recall = payload.get("recall", {}) or {}
    parts = [
        "=== Source Survey ===",
        f"Query: {payload.get('query', '')}",
        f"Total Matching Sources: {payload.get('total_sources', 0)}",
        (
            "Page: "
            f"limit={page.get('limit', 0)}, "
            f"returned={page.get('returned', len(sources))}"
        ),
        (
            "Recall: "
            f"k_recall={recall.get('k_recall', 'unknown')}, "
            f"mode={recall.get('mode', 'unknown')}"
        ),
    ]

    active_filters = {
        key: value for key, value in (payload.get("filters", {}) or {}).items() if value
    }
    if active_filters:
        parts.append(f"Filters: {active_filters}")

    if not sources:
        parts.append("\nNo source survey results found.")
        return "\n".join(parts)

    for idx, source in enumerate(sources, 1):
        parts.append(f"\n--- Source #{idx} ---")
        parts.append(
            f"Identity: {source.get('identity_field', 'unknown')}={source.get('identity_value', 'unknown')}"
        )
        parts.append(
            f"Best Score: {float(source.get('best_score', 0.0)):.4f}; "
            f"Hits: {source.get('hit_count', 0)}"
        )
        parts.append(f"Title: {source.get('title', 'Untitled')}")
        parts.append(f"Authors: {source.get('authors', 'Unknown')}")
        if source.get("year"):
            parts.append(f"Year: {source['year']}")
        if source.get("item_type"):
            parts.append(f"Item Type: {source['item_type']}")
        if source.get("venue"):
            parts.append(f"Venue: {source['venue']}")
        if source.get("doi"):
            parts.append(f"DOI: {source['doi']}")
        if source.get("language"):
            parts.append(f"Language: {source['language']}")
        if source.get("tags"):
            parts.append(f"Tags: {source['tags']}")
        if source.get("abstract"):
            parts.append(f"Abstract: {_truncate_field(source['abstract'])}")
        if source.get("extractor"):
            parts.append(f"Extractor: {source['extractor']}")
        if source.get("extract_quality"):
            parts.append(f"Extract Quality: {source['extract_quality']}")
        if source.get("extract_action"):
            parts.append(f"Extract Action: {source['extract_action']}")
        if source.get("collections"):
            parts.append(f"Collections: {source['collections']}")
        if source.get("backlink"):
            parts.append(f"Link: {source['backlink']}")

        representatives = source.get("representative_chunks", []) or []
        if representatives:
            parts.append("Representative Chunks:")
            for chunk in representatives:
                parts.append(
                    f"- {chunk.get('chunk_id')} "
                    f"(score={float(chunk.get('score', 0.0)):.4f}, "
                    f"level={chunk.get('chunk_level', '')}, "
                    f"index={chunk.get('chunk_index')})"
                )
                if chunk.get("snippet"):
                    parts.append(f"  {chunk['snippet']}")

    return "\n".join(parts)


def format_index_status(payload: Dict[str, Any]) -> str:
    """Format index health status for MCP response text."""
    registry = payload.get("registry", {}) or {}
    ledger_drift = registry.get("ledger_drift", {}) or {}
    drift = payload.get("drift", 0)

    parts = [
        "=== Index Status ===",
        f"Collection: {payload.get('collection_name', 'unknown')}",
        f"Endpoint: {payload.get('endpoint', 'unknown')}",
        f"Server Build: {payload.get('git_sha', 'unknown')}",
        "",
        f"Chroma Chunks: {payload.get('chroma_chunk_count', 0):,}",
        f"Registry Chunks: {registry.get('chunk_count', 0):,}",
        f"Registry Sources: {registry.get('source_count', 0):,}",
        f"Index Ledger Units: {registry.get('index_unit_count', 0):,}",
        f"Drift (chroma - registry): {drift:+,}",
    ]

    if drift == 0:
        parts.append("Sync: OK (registry matches the vector store)")
    else:
        parts.append(
            "Sync: DRIFT DETECTED - registry and vector store disagree. "
            "Run 'python scripts/build_registry.py' to rebuild/verify, "
            "or re-run the routine index update."
        )

    parts.extend([
        "",
        "Ledger Drift:",
        (
            "  Chunkless text units: "
            f"{ledger_drift.get('chunkless_unit_count', 0):,} "
            f"(expected coverage-null: "
            f"{ledger_drift.get('expected_chunkless_unit_count', 0):,}; "
            f"unexpected: "
            f"{ledger_drift.get('unexpected_chunkless_unit_count', 0):,})"
        ),
        (
            "  Orphan chunk identities: "
            f"{ledger_drift.get('orphan_identity_count', 0):,} "
            f"({ledger_drift.get('orphan_chunk_count', 0):,} chunks)"
        ),
    ])
    if ledger_drift.get("ok", True):
        parts.append(
            "  Ledger Sync: OK (no orphan chunks or unexpected chunkless units)"
        )
    else:
        parts.append(
            "  Ledger Sync: DRIFT DETECTED - run the routine index update "
            "or rebuild the dev registry/collection."
        )
        chunkless_samples = (
            ledger_drift.get("unexpected_chunkless_unit_samples")
            or ledger_drift.get("chunkless_unit_samples")
            or []
        )
        orphan_samples = ledger_drift.get("orphan_identity_samples") or []
        if chunkless_samples:
            rendered = ", ".join(
                str(sample.get("unit_id", "unknown"))
                for sample in chunkless_samples[:3]
            )
            parts.append(f"  Unexpected chunkless sample: {rendered}")
        if orphan_samples:
            rendered = ", ".join(
                (
                    f"{sample.get('identity_field', 'identity')}="
                    f"{sample.get('identity_value', 'unknown')}"
                )
                for sample in orphan_samples[:3]
            )
            parts.append(f"  Orphan sample: {rendered}")

    parts.extend([
        "",
        f"Registry Backfill Complete: {'yes' if registry.get('backfill_complete') else 'no'}",
        f"Last Index Run: {registry.get('last_index_run_at') or 'unknown'}",
        f"Last Registry Refresh: {registry.get('last_refreshed_at') or 'unknown'}",
        f"Last Backfill: {registry.get('last_backfill_at') or 'never'}",
    ])

    return "\n".join(parts)


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
