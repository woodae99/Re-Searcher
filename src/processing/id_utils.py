"""Utilities for stable chunk ID generation."""

import hashlib


def stable_chunk_id(source_id: str, level: str, ordinal: int, chunk_text: str) -> str:
    """Generate stable chunk ID using content-derived hash."""
    snippet = chunk_text[:256]
    snippet_hash = hashlib.sha1(snippet.encode("utf-8")).hexdigest()
    composite = f"{source_id}|{level}|{ordinal}|{snippet_hash}"
    digest = hashlib.sha1(composite.encode("utf-8")).hexdigest()
    return f"{source_id}-{level}-{ordinal}-{digest}"


def attach_parent_ids(metadatas: list[dict], ids: list[str]) -> None:
    """Attach parent_id to metadata entries based on parent_level/parent_ordinal."""
    id_lookup = {}
    for metadata, chunk_id in zip(metadatas, ids):
        level = metadata.get("chunk_level")
        ordinal = metadata.get("chunk_index")
        if level is not None and ordinal is not None:
            id_lookup[(level, ordinal)] = chunk_id

    for metadata in metadatas:
        parent_level = metadata.get("parent_level")
        parent_ordinal = metadata.get("parent_ordinal")
        if parent_level is None or parent_ordinal is None:
            continue
        parent_id = id_lookup.get((parent_level, parent_ordinal))
        if parent_id:
            metadata["parent_id"] = parent_id
