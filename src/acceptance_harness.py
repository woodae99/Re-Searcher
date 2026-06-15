"""Acceptance checks for the v0.6 rebuild loop.

These checks are deliberately small and collection-agnostic so they can run
against a fake collection in unit tests, the throwaway `research_test`
collection during P0/P1, and the final production rebuild before cutover.
"""

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .enumeration import build_source_chunks_payload
from .registry import SourceRegistry
from .registry_audit import audit_duplicates


_WHITESPACE_RE = re.compile(r"\s+")
_LINE_HYPHEN_RE = re.compile(r"(\w)-\s+(\w)")
_LIGATURES = set("ﬀﬁﬂﬃﬄ")
_LIGATURE_MAP = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
}
_REVERSED_WORD_RE = re.compile(r"[A-Za-z]{6,}")
_LETTER_SPACED_RE = re.compile(r"(?:\b[A-Za-z]\s+){6,}[A-Za-z]\b")


@dataclass(frozen=True)
class QuoteProbe:
    """A quote that should verify against one stored chunk."""

    chunk_id: str
    quote: str
    identity_field: Optional[str] = None
    identity_value: Optional[str] = None


def normalize_quote_text(text: str) -> str:
    """Normalize text for quote checks without hiding real content drift."""
    normalized = text or ""
    for ligature, replacement in _LIGATURE_MAP.items():
        normalized = re.sub(f"{ligature}\\s+", replacement, normalized)
        normalized = normalized.replace(ligature, replacement)
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = _LINE_HYPHEN_RE.sub(r"\1\2", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def scan_text_artifacts(
    records: Iterable[Tuple[str, str]],
    *,
    tolerance: float = 0.0,
) -> Dict[str, Any]:
    """Detect common PDF extraction artifacts in chunk text.

    records is an iterable of `(chunk_id, text)` pairs. Counts are chunk counts,
    not match counts, to keep the report stable and easy to compare between
    extractor/chunker runs.

    `tolerance` is the maximum fraction of scanned chunks that may carry a given
    artifact before that artifact fails the gate. The default of 0.0 preserves
    the original strict behaviour (any artifact in any chunk fails). Callers
    scanning a real PDF corpus should raise it (e.g. 0.05), since cosmetic
    line-break hyphenation is near-universal and handled by normalization at
    query time — without a tolerance the gate can never pass on real data.
    """
    counts = {
        "line_hyphenation": 0,
        "ligature_space": 0,
        "raw_ligature": 0,
        "letter_spaced_words": 0,
        "reversed_text_suspect": 0,
    }
    samples: Dict[str, List[str]] = {key: [] for key in counts}
    scanned_chunks = 0

    for chunk_id, text in records:
        scanned_chunks += 1
        text = text or ""
        findings = {
            "line_hyphenation": bool(re.search(r"\w-\s*\n\s*\w", text)),
            "ligature_space": bool(re.search(r"[ﬀﬁﬂﬃﬄ]\s+\w", text)),
            "raw_ligature": any(ch in text for ch in _LIGATURES),
            "letter_spaced_words": bool(_LETTER_SPACED_RE.search(text)),
            "reversed_text_suspect": _looks_reversed(text),
        }
        for key, found in findings.items():
            if not found:
                continue
            counts[key] += 1
            if len(samples[key]) < 10:
                samples[key].append(chunk_id)

    rates = {
        key: (count / scanned_chunks if scanned_chunks else 0.0)
        for key, count in counts.items()
    }
    over_tolerance = sorted(key for key, rate in rates.items() if rate > tolerance)
    total_flagged = sum(1 for value in counts.values() if value)
    return {
        "artifact_counts": counts,
        "artifact_rates": rates,
        "scanned_chunks": scanned_chunks,
        "sample_chunk_ids": samples,
        "artifact_kinds_flagged": total_flagged,
        "tolerance": tolerance,
        "over_tolerance": over_tolerance,
        "pass": not over_tolerance,
    }


def verify_quotes(collection: Any, probes: Sequence[QuoteProbe]) -> Dict[str, Any]:
    """Verify chunk existence, optional source membership, and quote substring."""
    failures: List[Dict[str, str]] = []

    for probe in probes:
        result = collection.get(
            ids=[probe.chunk_id],
            include=["documents", "metadatas"],
        )
        ids = result.get("ids", []) or []
        if probe.chunk_id not in ids:
            failures.append(
                {
                    "chunk_id": probe.chunk_id,
                    "reason": "chunk_id_not_found",
                }
            )
            continue

        idx = ids.index(probe.chunk_id)
        documents = result.get("documents", []) or []
        metadatas = result.get("metadatas", []) or []
        text = documents[idx] if idx < len(documents) else ""
        metadata = (metadatas[idx] if idx < len(metadatas) else {}) or {}

        if probe.identity_field and probe.identity_value is not None:
            actual = str(metadata.get(probe.identity_field) or "")
            if actual != str(probe.identity_value):
                failures.append(
                    {
                        "chunk_id": probe.chunk_id,
                        "reason": "source_membership_mismatch",
                        "expected": f"{probe.identity_field}={probe.identity_value}",
                        "actual": f"{probe.identity_field}={actual}",
                    }
                )
                continue

        normalized_text = normalize_quote_text(text)
        normalized_quote = normalize_quote_text(probe.quote)
        if normalized_quote not in normalized_text:
            failures.append(
                {
                    "chunk_id": probe.chunk_id,
                    "reason": "quote_not_verbatim_after_normalization",
                }
            )

    return {
        "checked": len(probes),
        "failed": len(failures),
        "failures": failures,
        "pass": not failures,
    }


def compare_registry_to_collection(
    registry: SourceRegistry,
    collection: Any,
    *,
    limit_sources: Optional[int] = None,
) -> Dict[str, Any]:
    """Compare registry chunk membership with exact collection enumeration."""
    mismatches: List[Dict[str, Any]] = []
    checked_sources = 0

    for identity_field, identity_value in _iter_registry_sources(registry):
        if limit_sources is not None and checked_sources >= limit_sources:
            break
        checked_sources += 1

        registry_ids = _registry_chunk_ids(registry, identity_field, identity_value)
        payload = build_source_chunks_payload(
            collection,
            zotero_key=identity_value if identity_field == "zotero_key" else None,
            source_path=identity_value if identity_field == "source_id" else None,
            include_text=False,
            limit=200,
            offset=0,
        )
        collection_ids = {chunk["chunk_id"] for chunk in payload["chunks"]}

        # build_source_chunks_payload is paged; continue if the source is larger
        # than the first page. This keeps the comparison exact for large sources.
        returned = payload["page"]["returned"]
        offset = returned
        total = payload["total_matching"]
        while offset < total:
            page = build_source_chunks_payload(
                collection,
                zotero_key=identity_value if identity_field == "zotero_key" else None,
                source_path=identity_value if identity_field == "source_id" else None,
                include_text=False,
                limit=200,
                offset=offset,
            )
            collection_ids.update(chunk["chunk_id"] for chunk in page["chunks"])
            offset += page["page"]["returned"]

        if registry_ids != collection_ids:
            mismatches.append(
                {
                    "identity_field": identity_field,
                    "identity_value": identity_value,
                    "missing_from_collection": sorted(registry_ids - collection_ids)[:25],
                    "missing_from_registry": sorted(collection_ids - registry_ids)[:25],
                    "registry_count": len(registry_ids),
                    "collection_count": len(collection_ids),
                }
            )

    return {
        "checked_sources": checked_sources,
        "mismatched_sources": len(mismatches),
        "mismatches": mismatches[:25],
        "pass": not mismatches,
    }


def run_registry_harness(
    registry: SourceRegistry,
    collection: Any,
    *,
    artifact_sample_limit: int = 1000,
    artifact_tolerance: float = 0.0,
    quote_probes: Optional[Sequence[QuoteProbe]] = None,
    limit_sources: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the P0 registry/collection acceptance checks."""
    duplicate_report = audit_duplicates(registry)
    exactness_report = compare_registry_to_collection(
        registry,
        collection,
        limit_sources=limit_sources,
    )
    artifact_report = scan_text_artifacts(
        _iter_collection_documents(collection, limit=artifact_sample_limit),
        tolerance=artifact_tolerance,
    )
    quote_report = verify_quotes(collection, quote_probes or [])

    checks = {
        "duplicates": {
            **duplicate_report,
            "pass": duplicate_report.get("duplicate_slots", 0) == 0,
        },
        "registry_collection_exactness": exactness_report,
        "artifacts": artifact_report,
        "quotes": quote_report,
    }
    return {
        "pass": all(check.get("pass", False) for check in checks.values()),
        "checks": checks,
    }


def _looks_reversed(text: str) -> bool:
    """A conservative reversed-text smell for chunks with many long odd tokens."""
    words = _REVERSED_WORD_RE.findall(text or "")
    if len(words) < 12:
        return False
    vowel_light = sum(1 for word in words if sum(ch.lower() in "aeiou" for ch in word) <= 1)
    return (vowel_light / len(words)) >= 0.55


def _iter_registry_sources(registry: SourceRegistry) -> Iterable[Tuple[str, str]]:
    with sqlite3.connect(str(registry.db_path), timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT identity_field, identity_value
            FROM sources
            WHERE total_chunks > 0
            ORDER BY identity_field, identity_value
            """
        ).fetchall()
    for row in rows:
        yield row["identity_field"], row["identity_value"]


def _registry_chunk_ids(
    registry: SourceRegistry,
    identity_field: str,
    identity_value: str,
) -> set:
    with sqlite3.connect(str(registry.db_path), timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT chunk_id
            FROM chunks
            WHERE identity_field = ? AND identity_value = ?
            """,
            (identity_field, identity_value),
        ).fetchall()
    return {row["chunk_id"] for row in rows}


def _iter_collection_documents(
    collection: Any,
    *,
    limit: int,
    batch_size: int = 200,
) -> Iterable[Tuple[str, str]]:
    remaining = max(0, int(limit))
    offset = 0
    while remaining:
        page_size = min(batch_size, remaining)
        result = collection.get(
            include=["documents"],
            limit=page_size,
            offset=offset,
        )
        ids = result.get("ids", []) or []
        documents = result.get("documents", []) or []
        if not ids:
            break
        for idx, chunk_id in enumerate(ids):
            yield chunk_id, documents[idx] if idx < len(documents) else ""
        count = len(ids)
        offset += count
        remaining -= count
        if count < page_size:
            break
