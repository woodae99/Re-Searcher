"""Utilities for stable chunk ID generation."""

import hashlib
from typing import Optional


def stable_chunk_id(
    source_id: str,
    level: str,
    ordinal: int,
    chunk_text: str,
    variant: Optional[str] = None,
) -> str:
    """Generate stable chunk ID using content-derived hash."""
    snippet = chunk_text[:256]
    snippet_hash = hashlib.sha1(snippet.encode("utf-8")).hexdigest()
    variant_part = "" if variant is None else str(variant)
    composite = f"{source_id}|{level}|{ordinal}|{variant_part}|{snippet_hash}"
    digest = hashlib.sha1(composite.encode("utf-8")).hexdigest()
    return f"{source_id}-{level}-{ordinal}-{digest}"


def attach_parent_ids(metadatas: list[dict], ids: list[str]) -> None:
    """Attach parent_id to metadata entries based on parent_level/parent_ordinal.

    Scoped by source_id to prevent cross-document collisions.
    """
    id_lookup = {}
    for metadata, chunk_id in zip(metadatas, ids):
        source_id = metadata.get("source_id") or metadata.get("doc_id")
        level = metadata.get("chunk_level")
        ordinal = metadata.get("chunk_index")
        if source_id and level is not None and ordinal is not None:
            id_lookup[(source_id, level, ordinal)] = chunk_id

    for metadata in metadatas:
        # Don't overwrite existing parent_id
        if "parent_id" in metadata:
            continue

        source_id = metadata.get("source_id") or metadata.get("doc_id")
        parent_level = metadata.get("parent_level")
        parent_ordinal = metadata.get("parent_ordinal")

        if not source_id or parent_level is None or parent_ordinal is None:
            continue

        parent_id = id_lookup.get((source_id, parent_level, parent_ordinal))
        if parent_id:
            metadata["parent_id"] = parent_id
