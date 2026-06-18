"""Aggregate chunk retrieval hits into source-level survey rows."""

from __future__ import annotations

import textwrap
from typing import Any, Dict, List, Tuple

from src.registry import SourceRegistry, source_identity_for_metadata
from src.retrieval.filters import ResultTuple


def _snippet(text: str, width: int) -> str:
    clean = " ".join(str(text or "").split())
    if width <= 0:
        return clean
    return textwrap.shorten(clean, width=width, placeholder="...")


def aggregate_hits_by_source(
    results: List[ResultTuple],
    registry: SourceRegistry,
    *,
    limit: int = 20,
    representative_limit: int = 3,
    snippet_chars: int = 360,
    source_type: str | None = None,
    title_contains: str | None = None,
    author: str | None = None,
    collection: str | None = None,
    item_type: str | None = None,
    doi: str | None = None,
    language: str | None = None,
    tag: str | None = None,
) -> Dict[str, Any]:
    """Group retrieval results by registry identity and attach source metadata."""
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for doc_id, text, score, metadata in results:
        identity_field, identity_value = source_identity_for_metadata(metadata or {})
        if not identity_field or not identity_value:
            continue

        key = (identity_field, str(identity_value))
        group = groups.setdefault(
            key,
            {
                "identity_field": identity_field,
                "identity_value": str(identity_value),
                "hit_count": 0,
                "best_score": float(score),
                "representative_chunks": [],
            },
        )
        group["hit_count"] += 1
        group["best_score"] = max(group["best_score"], float(score))
        group["representative_chunks"].append(
            {
                "chunk_id": doc_id,
                "score": float(score),
                "snippet": _snippet(text, snippet_chars),
                "chunk_level": (metadata or {}).get("chunk_level", ""),
                "source_type": (metadata or {}).get("source_type", ""),
                "chunk_index": (metadata or {}).get("chunk_index"),
            }
        )

    source_rows = registry.sources_by_identity(
        list(groups.keys()),
        source_type=source_type,
        title_contains=title_contains,
        author=author,
        collection=collection,
        item_type=item_type,
        doi=doi,
        language=language,
        tag=tag,
    )

    rows: List[Dict[str, Any]] = []
    for key, group in groups.items():
        source = source_rows.get(key)
        if not source:
            continue

        representatives = sorted(
            group["representative_chunks"],
            key=lambda chunk: (-float(chunk["score"]), str(chunk["chunk_id"])),
        )[: max(0, int(representative_limit))]

        row = dict(source)
        row.update(
            {
                "hit_count": group["hit_count"],
                "best_score": group["best_score"],
                "representative_chunks": representatives,
            }
        )
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -float(row["best_score"]),
            -int(row["hit_count"]),
            str(row.get("title") or "").lower(),
            str(row.get("identity_value") or "").lower(),
        )
    )

    limit = max(0, int(limit))
    return {
        "total_sources": len(rows),
        "sources": rows[:limit],
        "page": {
            "offset": 0,
            "limit": limit,
            "returned": len(rows[:limit]),
        },
    }
