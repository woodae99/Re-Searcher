"""Helpers for diversifying / de-duplicating retrieval results.

Goal: avoid returning many near-identical chunks from the same underlying source by default,
while preserving the ability to go deep when desired.

This module is intentionally simple (no MMR yet).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

ResultTuple = Tuple[str, str, float, Dict[str, Any]]


def _pick_key(metadata: Dict[str, Any], key_priority: List[str]) -> Optional[str]:
    for k in key_priority:
        v = metadata.get(k)
        if v is None:
            continue
        # Chroma metadata values tend to be scalars; keep it conservative.
        if isinstance(v, (str, int, float, bool)):
            s = str(v).strip()
            if s:
                return f"{k}:{s}"
    return None


def apply_diversity(
    results: List[ResultTuple],
    *,
    key_priority: Optional[List[str]] = None,
    max_per_key: int = 2,
) -> List[ResultTuple]:
    """Limit how many results can come from the same grouping key.

    Preserves original ordering.

    Args:
        results: List[(doc_id, text, score, metadata)]
        key_priority: metadata keys to use for grouping, in order.
        max_per_key: max number of results allowed per grouping key.

    Returns:
        Filtered list of results.
    """
    if not results:
        return results

    if max_per_key <= 0:
        return []

    key_priority = key_priority or ["source_id", "zotero_key", "title"]

    seen_counts: Dict[str, int] = {}
    out: List[ResultTuple] = []

    for item in results:
        _, _, _, metadata = item
        key = _pick_key(metadata or {}, key_priority)
        if key is None:
            # If we can't group it, let it through.
            out.append(item)
            continue

        n = seen_counts.get(key, 0)
        if n >= max_per_key:
            continue
        seen_counts[key] = n + 1
        out.append(item)

    return out
