"""Parent-context expansion utilities."""

from typing import Any, Dict, List, Tuple


ResultTuple = Tuple[str, str, float, Dict[str, Any]]


def attach_parent_context(
    results: List[ResultTuple],
    vector_store,
    max_parents: int = 1,
) -> List[ResultTuple]:
    """Attach parent context text for results with parent IDs."""
    if not results or max_parents <= 0:
        return results

    parent_ids = []
    for _, _, _, metadata in results:
        parent_id = metadata.get("parent_id")
        if not parent_id:
            continue
        if isinstance(parent_id, list):
            parent_ids.extend(parent_id[:max_parents])
        else:
            parent_ids.append(parent_id)

    if not parent_ids:
        return results

    parent_records = vector_store.get_by_ids(parent_ids)
    parent_lookup = {record[0]: record for record in parent_records}

    enriched_results = []
    for doc_id, text, score, metadata in results:
        new_metadata = dict(metadata)
        parent_id = metadata.get("parent_id")
        if isinstance(parent_id, list):
            attached = []
            for pid in parent_id[:max_parents]:
                if pid in parent_lookup:
                    _, parent_text, parent_metadata = parent_lookup[pid]
                    attached.append({\"id\": pid, \"text\": parent_text, \"metadata\": parent_metadata})
            if attached:
                new_metadata[\"parent_contexts\"] = attached
        elif parent_id and parent_id in parent_lookup:
            _, parent_text, parent_metadata = parent_lookup[parent_id]
            new_metadata[\"parent_text\"] = parent_text
            new_metadata[\"parent_metadata\"] = parent_metadata
        enriched_results.append((doc_id, text, score, new_metadata))

    return enriched_results
