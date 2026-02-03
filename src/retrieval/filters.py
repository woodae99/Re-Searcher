"""Query-time filtering helpers.

We support two kinds of filtering:

1) Chroma metadata filtering ("where" clause): exact match and simple comparisons.
2) Post-filtering in Python for substring-style filters (author/title contains),
   when the underlying store does not support it reliably.

Design goal: keep the CLI/MCP interface simple while enabling deep dives.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ResultTuple = Tuple[str, str, float, Dict[str, Any]]


def build_where_filter(
    *,
    source_type: Optional[str] = None,
    zotero_key: Optional[str] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    extra_where: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a Chroma 'where' filter.

    Note: Chroma where syntax supports operators like $gte/$lte for numeric fields.
    This function keeps things conservative.
    """

    where: Dict[str, Any] = {}

    if source_type:
        where["source_type"] = source_type

    if zotero_key:
        where["zotero_key"] = zotero_key

    if year_min is not None or year_max is not None:
        # Store year as numeric comparisons when possible.
        cond: Dict[str, Any] = {}
        if year_min is not None:
            cond["$gte"] = int(year_min)
        if year_max is not None:
            cond["$lte"] = int(year_max)
        where["year"] = cond

    if extra_where:
        # Shallow merge: extra_where wins on key collisions.
        where.update(extra_where)

    return where or None


def _contains(haystack: Any, needle: str) -> bool:
    if haystack is None:
        return False
    if not isinstance(haystack, str):
        haystack = str(haystack)
    return needle.lower() in haystack.lower()


def apply_post_filters(
    results: List[ResultTuple],
    *,
    author_contains: Optional[str] = None,
    title_contains: Optional[str] = None,
) -> List[ResultTuple]:
    """Apply substring-style filters in Python.

    Preserves ordering.

    This is a fallback for cases where the vector store cannot perform partial
    string match filters.
    """

    if not results:
        return results

    out: List[ResultTuple] = []
    for doc_id, text, score, metadata in results:
        md = metadata or {}

        if author_contains and not _contains(md.get("authors"), author_contains):
            continue

        if title_contains and not _contains(md.get("title"), title_contains):
            continue

        out.append((doc_id, text, score, md))

    return out
