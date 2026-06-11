"""Integrity audit: diff the registry against ground truth and itself.

Ground truth is the Zotero SQLite database and the Obsidian vault on disk.
The registry (built from the vector store) is the index side. The diff
answers, definitively rather than by search sampling:

- ghosts: sources in the index whose ground-truth item/note no longer exists
- missing: ground-truth items/notes with no indexed content
- stale: notes modified on disk after their chunks were last indexed
- duplicates: multiple chunk IDs occupying the same (source, level, ordinal,
  variant) slot — the signature of content re-indexed without deletion
- legacy IDs: chunks written by the pre-pipeline indexer
- zero vectors: optionally, a sampled check for embeddings stored as zeros
  after embed-batch failures
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import SourceRegistry
from .sources.obsidian import ObsidianSource

_SAMPLE_LIMIT = 25


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _registry_conn(registry: SourceRegistry) -> sqlite3.Connection:
    conn = sqlite3.connect(str(registry.db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def audit_duplicates(registry: SourceRegistry) -> Dict[str, Any]:
    """Chunk-ID slots occupied more than once (stale duplicates from missed deletes)."""
    with _registry_conn(registry) as conn:
        rows = conn.execute(
            """
            SELECT source_id, chunk_level, chunk_index, variant,
                   COUNT(*) AS n
            FROM chunks
            WHERE chunk_index IS NOT NULL AND source_id != ''
            GROUP BY source_id, chunk_level, chunk_index, variant
            HAVING COUNT(*) > 1
            ORDER BY n DESC
            """
        ).fetchall()
    affected_sources = sorted({row["source_id"] for row in rows})
    return {
        "duplicate_slots": len(rows),
        "extra_chunks": sum(row["n"] - 1 for row in rows),
        "affected_sources": len(affected_sources),
        "sample_sources": affected_sources[:_SAMPLE_LIMIT],
    }


def audit_id_patterns(registry: SourceRegistry) -> Dict[str, Any]:
    """Census of chunk ID generations (legacy `-chunk-N` vs stable hash IDs)."""
    with _registry_conn(registry) as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        legacy = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE chunk_id LIKE '%-chunk-%'"
        ).fetchone()["n"]
        no_stamp = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE indexed_at IS NULL OR indexed_at = ''"
        ).fetchone()["n"]
        stamps = conn.execute(
            """
            SELECT MIN(NULLIF(indexed_at, '')) AS oldest,
                   MAX(NULLIF(indexed_at, '')) AS newest
            FROM chunks
            """
        ).fetchone()
    return {
        "total_chunks": total,
        "legacy_id_chunks": legacy,
        "chunks_without_indexed_at": no_stamp,
        "oldest_indexed_at": stamps["oldest"] or "",
        "newest_indexed_at": stamps["newest"] or "",
    }


def audit_zotero(registry: SourceRegistry, config: Dict[str, Any]) -> Dict[str, Any]:
    """Diff registry Zotero sources against the Zotero SQLite item list."""
    zotero_cfg = config.get("zotero", {}) or {}
    if not zotero_cfg.get("enabled", False):
        return {"skipped": True, "reason": "zotero disabled in config"}

    db_path = Path(zotero_cfg.get("data_directory", "")).expanduser() / "zotero.sqlite"
    if not db_path.exists():
        return {"skipped": True, "reason": f"zotero.sqlite not found at {db_path}"}

    uri = f"{db_path.as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            rows = conn.execute(
                """
                SELECT i.key
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
                  AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        # Typically "database is locked": Zotero holds zotero.sqlite while the
        # app is open. Skip this section rather than failing the whole audit.
        return {
            "skipped": True,
            "reason": (
                f"zotero.sqlite unavailable ({exc}). Close Zotero and re-run "
                f"scripts/build_registry.py --audit-only."
            ),
        }
    expected = {row["key"] for row in rows}

    with _registry_conn(registry) as reg:
        indexed = {
            row["identity_value"]
            for row in reg.execute(
                "SELECT identity_value FROM sources WHERE identity_field = 'zotero_key'"
            ).fetchall()
        }

    ghosts = sorted(indexed - expected)
    without_content = sorted(expected - indexed)
    return {
        "skipped": False,
        "expected_items": len(expected),
        "indexed_items": len(indexed & expected),
        "ghost_sources": len(ghosts),
        "ghost_sample": ghosts[:_SAMPLE_LIMIT],
        # Items with no notes/attachments/annotations are legitimately absent;
        # this list is "nothing indexed", not necessarily "error".
        "items_without_indexed_content": len(without_content),
        "items_without_indexed_content_sample": without_content[:_SAMPLE_LIMIT],
    }


def _parse_stamp(stamp: str) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def audit_obsidian(registry: SourceRegistry, config: Dict[str, Any]) -> Dict[str, Any]:
    """Diff registry Obsidian sources against the vault on disk (existence + mtime)."""
    source = ObsidianSource(config)
    if not source.is_enabled():
        return {"skipped": True, "reason": "obsidian disabled in config"}
    if not source.validate_config():
        return {"skipped": True, "reason": "obsidian vault not found/configured"}

    include_folders = source._normalize_folder_list(
        source.obsidian_config.get("include_folders", [])
    )
    exclude_patterns = source._get_exclude_patterns()
    md_files = source._find_markdown_files(include_folders, exclude_patterns)

    expected: Dict[str, float] = {}
    for md_file in md_files:
        relative = md_file.relative_to(source.vault_path)
        expected[f"obsidian-{relative}"] = md_file.stat().st_mtime

    with _registry_conn(registry) as reg:
        indexed_rows = reg.execute(
            """
            SELECT identity_value, last_indexed_at
            FROM sources
            WHERE identity_field = 'source_id'
              AND identity_value LIKE 'obsidian-%'
            """
        ).fetchall()
    indexed = {row["identity_value"]: row["last_indexed_at"] for row in indexed_rows}

    ghosts = sorted(set(indexed) - set(expected))
    missing = sorted(set(expected) - set(indexed))

    stale: List[str] = []
    unknown_age = 0
    for source_id, stamp in indexed.items():
        if source_id not in expected:
            continue
        indexed_at = _parse_stamp(stamp)
        if indexed_at is None:
            unknown_age += 1
            continue
        modified_at = datetime.fromtimestamp(expected[source_id], tz=timezone.utc)
        if modified_at > indexed_at:
            stale.append(source_id)
    stale.sort()

    return {
        "skipped": False,
        "vault_notes": len(expected),
        "indexed_notes": len(set(indexed) & set(expected)),
        "ghost_sources": len(ghosts),
        "ghost_sample": ghosts[:_SAMPLE_LIMIT],
        "missing_notes": len(missing),
        "missing_sample": missing[:_SAMPLE_LIMIT],
        "stale_notes": len(stale),
        "stale_sample": stale[:_SAMPLE_LIMIT],
        "indexed_age_unknown": unknown_age,
    }


def audit_embeddings_sample(
    registry: SourceRegistry, collection: Any, sample_size: int
) -> Dict[str, Any]:
    """Sample stored embeddings and count zero vectors (failed-embed sentinel)."""
    if sample_size <= 0:
        return {"skipped": True, "reason": "no sample requested"}

    with _registry_conn(registry) as conn:
        rows = conn.execute(
            "SELECT chunk_id FROM chunks ORDER BY RANDOM() LIMIT ?",
            (int(sample_size),),
        ).fetchall()
    ids = [row["chunk_id"] for row in rows]
    if not ids:
        return {"skipped": True, "reason": "registry has no chunks"}

    zero = 0
    found = 0
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        result = collection.get(ids=batch_ids, include=["embeddings"])
        embeddings = result.get("embeddings")
        if embeddings is None:
            continue
        for embedding in embeddings:
            if embedding is None:
                continue
            found += 1
            if not any(embedding):
                zero += 1

    return {
        "skipped": False,
        "sampled": len(ids),
        "embeddings_checked": found,
        "zero_vectors": zero,
        "zero_vector_rate": (zero / found) if found else 0.0,
    }


def run_audit(
    registry: SourceRegistry,
    config: Dict[str, Any],
    *,
    collection: Any = None,
    embedding_sample: int = 0,
    chroma_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Run all audits and return a single report dict."""
    report: Dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "registry": registry.status(),
        "duplicates": audit_duplicates(registry),
        "id_patterns": audit_id_patterns(registry),
        "zotero": audit_zotero(registry, config),
        "obsidian": audit_obsidian(registry, config),
    }
    if chroma_count is not None:
        registry_chunks = report["registry"]["chunk_count"]
        report["chroma_sync"] = {
            "chroma_count": chroma_count,
            "registry_count": registry_chunks,
            "drift": chroma_count - registry_chunks,
        }
    if collection is not None and embedding_sample > 0:
        report["embeddings"] = audit_embeddings_sample(
            registry, collection, embedding_sample
        )
    return report


def summarize_report(report: Dict[str, Any]) -> str:
    """Human-readable summary of an audit report."""
    lines = ["=== Registry Audit Summary ==="]

    reg = report.get("registry", {})
    lines.append(
        f"Registry: {reg.get('source_count', 0):,} sources, "
        f"{reg.get('chunk_count', 0):,} chunks"
    )

    sync = report.get("chroma_sync")
    if sync:
        lines.append(
            f"Chroma sync: chroma={sync['chroma_count']:,} "
            f"registry={sync['registry_count']:,} drift={sync['drift']:+,}"
        )

    dup = report.get("duplicates", {})
    lines.append(
        f"Duplicate slots: {dup.get('duplicate_slots', 0):,} "
        f"({dup.get('extra_chunks', 0):,} extra chunks across "
        f"{dup.get('affected_sources', 0):,} sources)"
    )

    ids = report.get("id_patterns", {})
    lines.append(
        f"Legacy-ID chunks: {ids.get('legacy_id_chunks', 0):,} | "
        f"chunks without indexed_at: {ids.get('chunks_without_indexed_at', 0):,}"
    )

    zot = report.get("zotero", {})
    if zot.get("skipped"):
        lines.append(f"Zotero: skipped ({zot.get('reason')})")
    else:
        lines.append(
            f"Zotero: {zot.get('indexed_items', 0):,}/{zot.get('expected_items', 0):,} "
            f"items indexed | ghosts: {zot.get('ghost_sources', 0):,} | "
            f"no indexed content: {zot.get('items_without_indexed_content', 0):,}"
        )

    obs = report.get("obsidian", {})
    if obs.get("skipped"):
        lines.append(f"Obsidian: skipped ({obs.get('reason')})")
    else:
        lines.append(
            f"Obsidian: {obs.get('indexed_notes', 0):,}/{obs.get('vault_notes', 0):,} "
            f"notes indexed | ghosts: {obs.get('ghost_sources', 0):,} | "
            f"missing: {obs.get('missing_notes', 0):,} | "
            f"STALE: {obs.get('stale_notes', 0):,}"
        )

    emb = report.get("embeddings")
    if emb and not emb.get("skipped"):
        lines.append(
            f"Embeddings sample: {emb['zero_vectors']:,}/{emb['embeddings_checked']:,} "
            f"zero vectors ({emb['zero_vector_rate']:.2%})"
        )

    return "\n".join(lines)
