"""Shared source-chunk enumeration logic for MCP and CLI surfaces.

Both `mcp_server.get_source_chunks` and `scripts/sources.py chunks` call
`build_source_chunks_payload`, so the two surfaces cannot diverge.
"""

from typing import Any, Dict, Optional

from .registry import source_identity_for_metadata  # re-export for callers

VALID_CHUNK_LEVELS = {"coarse", "mid", "fine", "atomic"}


def clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _sort_key_for_chunk(record: Dict[str, Any]) -> tuple:
    metadata = record.get("metadata", {}) or {}
    ordinal = metadata.get("chunk_index")
    try:
        return (0, int(ordinal), str(record.get("chunk_id", "")))
    except (TypeError, ValueError):
        return (1, 0, str(record.get("chunk_id", "")))


def build_source_chunks_payload(
    collection: Any,
    *,
    zotero_key: Optional[str] = None,
    source_path: Optional[str] = None,
    chunk_level: Optional[str] = None,
    include_text: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Enumerate chunks for a single source by exact metadata identity.

    Synchronous; callers on an event loop should run it in a worker thread.
    Raises ValueError on invalid arguments.
    """
    if bool(zotero_key) == bool(source_path):
        raise ValueError("Exactly one of zotero_key or source_path is required")
    if chunk_level and chunk_level not in VALID_CHUNK_LEVELS:
        raise ValueError(
            "chunk_level must be one of: " + ", ".join(sorted(VALID_CHUNK_LEVELS))
        )

    limit = clamp_int(limit, 50, minimum=1, maximum=200)
    offset = clamp_int(offset, 0, minimum=0, maximum=10**12)

    identity_field = "zotero_key" if zotero_key else "source_id"
    identity_value = zotero_key or source_path
    where: Dict[str, Any] = {identity_field: identity_value}

    all_result = collection.get(where=where, include=["metadatas"])
    ids = all_result.get("ids", []) or []
    metadatas = all_result.get("metadatas", []) or []

    records = [
        {"chunk_id": doc_id, "metadata": metadata or {}}
        for doc_id, metadata in zip(ids, metadatas)
    ]
    if chunk_level:
        records = [
            record
            for record in records
            if (record.get("metadata") or {}).get("chunk_level") == chunk_level
        ]
    has_ordinal = any(
        (record.get("metadata") or {}).get("chunk_index") is not None
        for record in records
    )
    records.sort(key=_sort_key_for_chunk)

    page_records = records[offset : offset + limit]

    if include_text and page_records:
        page_ids = [record["chunk_id"] for record in page_records]
        text_result = collection.get(
            ids=page_ids,
            include=["documents", "metadatas"],
        )
        result_ids = text_result.get("ids", []) or []
        documents = text_result.get("documents", []) or [None] * len(result_ids)
        result_metas = text_result.get("metadatas", []) or [{}] * len(result_ids)
        by_id = {
            doc_id: {
                "text": documents[idx] if idx < len(documents) else None,
                "metadata": (result_metas[idx] if idx < len(result_metas) else {}) or {},
            }
            for idx, doc_id in enumerate(result_ids)
        }
        for record in page_records:
            fetched = by_id.get(record["chunk_id"], {})
            record["text"] = fetched.get("text")
            if fetched.get("metadata") is not None:
                record["metadata"] = fetched["metadata"]

    return {
        "source": {
            "identity_field": identity_field,
            "identity_value": identity_value,
        },
        "total_matching": len(records),
        "page": {
            "offset": offset,
            "limit": limit,
            "returned": len(page_records),
        },
        "ordering": {
            "field": "chunk_index" if has_ordinal else "chunk_id",
            "id_tiebreak": True,
            "note": None if has_ordinal else "No chunk_index metadata found; sorted by chunk id.",
        },
        "chunks": page_records,
    }
