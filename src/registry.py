"""Source registry: a SQLite mirror of source/chunk identity for the vector store.

The registry is maintained by the indexing pipeline in the same code paths that
write to and delete from ChromaDB, so enumeration surfaces (MCP list_sources,
CLI parity commands, index status) never need to scan collection metadata.

For collections indexed before the registry existed, a checkpointed backfill
(`scripts/build_registry.py`) scans the collection once and can resume from
its last committed offset after interruption.

Concurrency model: every public method opens its own short-lived SQLite
connection, so the registry is safe to use from the pipeline's embed/store
worker threads and from MCP request threads simultaneously. WAL mode keeps
readers unblocked while the pipeline writes.
"""

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA_VERSION = 5

_PLACEHOLDER_TITLES = {"", "Untitled"}
_PLACEHOLDER_AUTHORS = {"", "Unknown"}
_TEXT_BEARING_UNIT_KINDS = {"attachment", "note", "annotation", "vault_file"}


def collection_slug(collection_name: str) -> str:
    """Filesystem-safe slug for a collection name (matches pipeline progress files)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(collection_name)).strip("_")
    return slug or "research_library"


def registry_path_for(config: Dict[str, Any]) -> Path:
    """Resolve the registry database path for a config, scoped per collection."""
    output_dir = Path(config.get("output_folder", "./output"))
    collection_name = str(
        config.get("storage", {}).get("collection_name", "research_library")
    )
    return output_dir / f"registry.{collection_slug(collection_name)}.sqlite"


def source_identity_for_metadata(metadata: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Single source of truth for the source-identity rule.

    Zotero-derived chunks group by zotero_key (item level); everything else
    groups by source_id (document level). list_sources rows and
    get_source_chunks filters must agree on this rule exactly.
    """
    source_type = metadata.get("source_type")
    if isinstance(source_type, str) and source_type.startswith("zotero"):
        return "zotero_key", metadata.get("zotero_key")
    return "source_id", metadata.get("source_id")


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _like_escape(needle: str) -> str:
    return (
        needle.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _unit_child_key(unit_id: str, unit_kind: str) -> str:
    prefix = {
        "attachment": ":attachment:",
        "note": ":note:",
        "annotation": ":annotation:",
    }.get(unit_kind)
    if not prefix or prefix not in unit_id:
        return ""
    return unit_id.rsplit(prefix, 1)[-1]


class SourceRegistry:
    """SQLite-backed registry of sources and chunks mirrored from the vector store."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    identity_field TEXT NOT NULL,
                    identity_value TEXT NOT NULL,
                    source_id TEXT,
                    source_type TEXT,
                    chunk_level TEXT,
                    chunk_index INTEGER,
                    variant TEXT DEFAULT '',
                    zotero_key TEXT DEFAULT '',
                    attachment_key TEXT DEFAULT '',
                    note_key TEXT DEFAULT '',
                    annotation_key TEXT DEFAULT '',
                    indexed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_identity
                    ON chunks(identity_field, identity_value);
                CREATE INDEX IF NOT EXISTS idx_chunks_source_id
                    ON chunks(source_id);
                CREATE TABLE IF NOT EXISTS vault_files (
                    relative_path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    identity_field TEXT NOT NULL,
                    identity_value TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    authors TEXT DEFAULT '',
                    year TEXT DEFAULT '',
                    backlink TEXT DEFAULT '',
                    collections TEXT DEFAULT '',
                    item_type TEXT DEFAULT '',
                    doi TEXT DEFAULT '',
                    abstract TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    venue TEXT DEFAULT '',
                    language TEXT DEFAULT '',
                    extractor TEXT DEFAULT '',
                    extract_quality TEXT DEFAULT '',
                    extract_action TEXT DEFAULT '',
                    source_types TEXT DEFAULT '',
                    counts_json TEXT DEFAULT '{}',
                    total_chunks INTEGER DEFAULT 0,
                    first_indexed_at TEXT DEFAULT '',
                    last_indexed_at TEXT DEFAULT '',
                    PRIMARY KEY (identity_field, identity_value)
                );
                CREATE TABLE IF NOT EXISTS index_units (
                    unit_id TEXT PRIMARY KEY,
                    identity_field TEXT NOT NULL,
                    identity_value TEXT NOT NULL,
                    unit_kind TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    indexed_grain TEXT DEFAULT '',
                    indexed_at TEXT DEFAULT '',
                    chunk_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_units_identity
                    ON index_units(identity_field, identity_value);
                CREATE INDEX IF NOT EXISTS idx_units_kind
                    ON index_units(unit_kind);
                """
            )
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)").fetchall()
            }
            if "collections" not in existing:
                conn.execute("ALTER TABLE sources ADD COLUMN collections TEXT DEFAULT ''")
            for column in (
                "item_type",
                "doi",
                "abstract",
                "tags",
                "venue",
                "language",
                "extractor",
                "extract_quality",
                "extract_action",
            ):
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE sources ADD COLUMN {column} TEXT DEFAULT ''"
                    )
            existing_chunks = {
                row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()
            }
            for column in (
                "zotero_key",
                "attachment_key",
                "note_key",
                "annotation_key",
            ):
                if column not in existing_chunks:
                    conn.execute(
                        f"ALTER TABLE chunks ADD COLUMN {column} TEXT DEFAULT ''"
                    )
            # Upsert (not INSERT OR IGNORE) so the version reflects the migrations
            # actually applied: the additive CREATE/ALTER statements above run on
            # every init, so an existing v1 registry is now at SCHEMA_VERSION.
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    # ----------------------------------------------------------------- write

    def record_chunks(
        self,
        ids: List[str],
        metadatas: List[Dict[str, Any]],
        meta_updates: Optional[Dict[str, str]] = None,
    ) -> int:
        """Upsert chunk rows (mirroring a Chroma upsert) plus source attributes.

        meta_updates, when given, is committed in the same transaction as the
        chunk rows; the backfill uses this to checkpoint its scan offset
        atomically with the data it just recorded.

        Returns the number of chunk rows recorded.
        """
        chunk_rows: List[Tuple] = []
        attrs: Dict[Tuple[str, str], Dict[str, str]] = {}

        for chunk_id, metadata in zip(ids, metadatas):
            metadata = metadata or {}
            identity_field, identity_value = source_identity_for_metadata(metadata)
            if not identity_value:
                continue
            identity_value = str(identity_value)

            ordinal_raw = metadata.get("chunk_index")
            try:
                ordinal: Optional[int] = int(ordinal_raw)
            except (TypeError, ValueError):
                ordinal = None

            chunk_rows.append(
                (
                    str(chunk_id),
                    identity_field,
                    identity_value,
                    str(metadata.get("source_id") or ""),
                    str(metadata.get("source_type") or "unknown"),
                    str(metadata.get("chunk_level") or "unknown"),
                    ordinal,
                    str(metadata.get("chunk_id_variant") or ""),
                    str(metadata.get("zotero_key") or ""),
                    str(metadata.get("attachment_key") or ""),
                    str(metadata.get("note_key") or ""),
                    str(metadata.get("annotation_key") or ""),
                    str(metadata.get("indexed_at") or ""),
                )
            )

            key = (identity_field, identity_value)
            current = attrs.setdefault(
                key,
                {
                    "title": "",
                    "authors": "",
                    "year": "",
                    "backlink": "",
                    "collections": "",
                    "item_type": "",
                    "doi": "",
                    "abstract": "",
                    "tags": "",
                    "venue": "",
                    "language": "",
                    "extractor": "",
                    "extract_quality": "",
                    "extract_action": "",
                },
            )
            title = str(metadata.get("title") or "")
            if current["title"] in _PLACEHOLDER_TITLES and title not in _PLACEHOLDER_TITLES:
                current["title"] = title
            authors = str(metadata.get("authors") or "")
            if current["authors"] in _PLACEHOLDER_AUTHORS and authors not in _PLACEHOLDER_AUTHORS:
                current["authors"] = authors
            if not current["year"] and metadata.get("year"):
                current["year"] = str(metadata.get("year"))
            if not current["backlink"] and metadata.get("backlink"):
                current["backlink"] = str(metadata.get("backlink"))
            # Zotero collection names: a list before Chroma sanitization
            # (pipeline path) or a comma-joined string after it (backfill path).
            if not current["collections"] and metadata.get("collections"):
                raw_collections = metadata.get("collections")
                if isinstance(raw_collections, list):
                    current["collections"] = ", ".join(
                        str(name) for name in raw_collections
                    )
                else:
                    current["collections"] = str(raw_collections)
            if not current["item_type"] and metadata.get("item_type"):
                current["item_type"] = str(metadata.get("item_type"))
            if not current["doi"] and (metadata.get("doi") or metadata.get("DOI")):
                current["doi"] = str(metadata.get("doi") or metadata.get("DOI"))
            if not current["abstract"] and (
                metadata.get("abstract") or metadata.get("abstractNote")
            ):
                current["abstract"] = str(
                    metadata.get("abstract") or metadata.get("abstractNote")
                )
            if not current["tags"] and metadata.get("tags"):
                raw_tags = metadata.get("tags")
                if isinstance(raw_tags, list):
                    current["tags"] = ", ".join(str(name) for name in raw_tags)
                else:
                    current["tags"] = str(raw_tags)
            if not current["venue"] and (
                metadata.get("venue") or metadata.get("publicationTitle")
            ):
                current["venue"] = str(
                    metadata.get("venue") or metadata.get("publicationTitle")
                )
            if not current["language"] and metadata.get("language"):
                current["language"] = str(metadata.get("language"))
            if not current["extractor"] and metadata.get("extractor"):
                current["extractor"] = str(metadata.get("extractor"))
            if not current["extract_quality"] and metadata.get("extract_quality"):
                current["extract_quality"] = str(metadata.get("extract_quality"))
            if not current["extract_action"] and metadata.get("extract_action"):
                current["extract_action"] = str(metadata.get("extract_action"))

        if not chunk_rows and not meta_updates:
            return 0

        with self._connect() as conn:
            if chunk_rows:
                conn.executemany(
                    """
                    INSERT INTO chunks(
                        chunk_id, identity_field, identity_value, source_id,
                        source_type, chunk_level, chunk_index, variant,
                        zotero_key, attachment_key, note_key, annotation_key,
                        indexed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        identity_field = excluded.identity_field,
                        identity_value = excluded.identity_value,
                        source_id = excluded.source_id,
                        source_type = excluded.source_type,
                        chunk_level = excluded.chunk_level,
                        chunk_index = excluded.chunk_index,
                        variant = excluded.variant,
                        zotero_key = excluded.zotero_key,
                        attachment_key = excluded.attachment_key,
                        note_key = excluded.note_key,
                        annotation_key = excluded.annotation_key,
                        indexed_at = excluded.indexed_at
                    """,
                    chunk_rows,
                )
            if attrs:
                conn.executemany(
                    """
                    INSERT INTO sources(
                        identity_field, identity_value, title, authors, year,
                        backlink, collections, item_type, doi, abstract, tags,
                        venue, language, extractor, extract_quality, extract_action
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(identity_field, identity_value) DO UPDATE SET
                        title = CASE
                            WHEN excluded.title NOT IN ('', 'Untitled')
                                 AND sources.title IN ('', 'Untitled')
                            THEN excluded.title ELSE sources.title END,
                        authors = CASE
                            WHEN excluded.authors NOT IN ('', 'Unknown')
                                 AND sources.authors IN ('', 'Unknown')
                            THEN excluded.authors ELSE sources.authors END,
                        year = CASE
                            WHEN excluded.year != '' AND sources.year = ''
                            THEN excluded.year ELSE sources.year END,
                        backlink = CASE
                            WHEN excluded.backlink != '' AND sources.backlink = ''
                            THEN excluded.backlink ELSE sources.backlink END,
                        collections = CASE
                            WHEN excluded.collections != '' AND sources.collections = ''
                            THEN excluded.collections ELSE sources.collections END,
                        item_type = CASE
                            WHEN excluded.item_type != '' AND sources.item_type = ''
                            THEN excluded.item_type ELSE sources.item_type END,
                        doi = CASE
                            WHEN excluded.doi != '' AND sources.doi = ''
                            THEN excluded.doi ELSE sources.doi END,
                        abstract = CASE
                            WHEN excluded.abstract != '' AND sources.abstract = ''
                            THEN excluded.abstract ELSE sources.abstract END,
                        tags = CASE
                            WHEN excluded.tags != '' AND sources.tags = ''
                            THEN excluded.tags ELSE sources.tags END,
                        venue = CASE
                            WHEN excluded.venue != '' AND sources.venue = ''
                            THEN excluded.venue ELSE sources.venue END,
                        language = CASE
                            WHEN excluded.language != '' AND sources.language = ''
                            THEN excluded.language ELSE sources.language END
                        ,
                        extractor = CASE
                            WHEN excluded.extractor != '' AND sources.extractor = ''
                            THEN excluded.extractor ELSE sources.extractor END,
                        extract_quality = CASE
                            WHEN excluded.extract_quality != '' AND sources.extract_quality = ''
                            THEN excluded.extract_quality ELSE sources.extract_quality END,
                        extract_action = CASE
                            WHEN excluded.extract_action != '' AND sources.extract_action = ''
                            THEN excluded.extract_action ELSE sources.extract_action END
                    """,
                    [
                        (
                            field,
                            value,
                            a["title"],
                            a["authors"],
                            a["year"],
                            a["backlink"],
                            a["collections"],
                            a["item_type"],
                            a["doi"],
                            a["abstract"],
                            a["tags"],
                            a["venue"],
                            a["language"],
                            a["extractor"],
                            a["extract_quality"],
                            a["extract_action"],
                        )
                        for (field, value), a in attrs.items()
                    ],
                )
            for key, value in (meta_updates or {}).items():
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )

        return len(chunk_rows)

    # --------------------------------------------------------------- ledger
    #
    # The index ledger (index_units) records, per smallest-independently-changing
    # unit, the source-side fingerprint it was last indexed at. It is the
    # authority for reconciliation: "what needs processing" is a diff between a
    # source's current state (enumerated by the adapter) and these rows. The
    # register treats fingerprints as opaque strings — it compares them, never
    # parses them — so it stays source-agnostic. See
    # docs/SPEC_REGISTER_AS_INDEX_LEDGER.md.

    def get_unit_states(self) -> Dict[str, str]:
        """Return the recorded ledger as {unit_id: source_fingerprint}."""
        with self._connect() as conn:
            return {
                row["unit_id"]: row["source_fingerprint"]
                for row in conn.execute(
                    "SELECT unit_id, source_fingerprint FROM index_units"
                )
            }

    def indexed_identities(self) -> set:
        """Source identities that currently have chunks (i.e. are in the index).

        Used to seed the ledger from world state after a legacy-mode run: only
        identities with chunks are recorded, so failed/empty items are not
        marked as indexed and `parent_meta` units (which produce no chunks of
        their own) are still covered via their parent identity.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT identity_field, identity_value FROM sources WHERE total_chunks > 0"
            ).fetchall()
        return {(row["identity_field"], row["identity_value"]) for row in rows}

    def record_unit_states(
        self,
        units: List[Dict[str, Any]],
        meta_updates: Optional[Dict[str, str]] = None,
    ) -> int:
        """Upsert ledger rows for indexed units, optionally with meta in the same txn.

        Each unit dict carries: unit_id, identity_field, identity_value,
        unit_kind, source_fingerprint, and optionally indexed_grain,
        indexed_at, chunk_count. A unit should be recorded only after its
        vectors are committed to Chroma, so an interrupted run re-plans the
        un-recorded unit (idempotent by stable id).
        """
        rows: List[Tuple] = []
        for unit in units:
            unit_id = str(unit.get("unit_id") or "")
            if not unit_id:
                continue
            rows.append(
                (
                    unit_id,
                    str(unit.get("identity_field") or ""),
                    str(unit.get("identity_value") or ""),
                    str(unit.get("unit_kind") or "unknown"),
                    str(unit.get("source_fingerprint") or ""),
                    str(unit.get("indexed_grain") or ""),
                    str(unit.get("indexed_at") or ""),
                    int(unit.get("chunk_count") or 0),
                )
            )

        if not rows and not meta_updates:
            return 0

        with self._connect() as conn:
            if rows:
                conn.executemany(
                    """
                    INSERT INTO index_units(
                        unit_id, identity_field, identity_value, unit_kind,
                        source_fingerprint, indexed_grain, indexed_at, chunk_count
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(unit_id) DO UPDATE SET
                        identity_field = excluded.identity_field,
                        identity_value = excluded.identity_value,
                        unit_kind = excluded.unit_kind,
                        source_fingerprint = excluded.source_fingerprint,
                        indexed_grain = excluded.indexed_grain,
                        indexed_at = excluded.indexed_at,
                        chunk_count = excluded.chunk_count
                    """,
                    rows,
                )
            for key, value in (meta_updates or {}).items():
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
        return len(rows)

    def delete_units(self, unit_ids: List[str]) -> int:
        """Remove ledger rows by unit id (mirrors a surgical Chroma delete)."""
        ids = [str(uid) for uid in unit_ids if uid]
        if not ids:
            return 0
        deleted = 0
        with self._connect() as conn:
            for start in range(0, len(ids), 500):
                batch = ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(
                    f"DELETE FROM index_units WHERE unit_id IN ({placeholders})",
                    tuple(batch),
                )
                deleted += cursor.rowcount
        return deleted

    def delete_units_for_source(
        self, identity_field: str, identity_value: str
    ) -> int:
        """Remove all ledger rows for one source identity (full-source delete)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM index_units WHERE identity_field = ? AND identity_value = ?",
                (identity_field, str(identity_value)),
            )
        return cursor.rowcount

    def delete_source_chunks(self, identity_field: str, identity_value: str) -> int:
        """Remove all chunk rows for one source (mirrors a Chroma delete_where)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM chunks WHERE identity_field = ? AND identity_value = ?",
                (identity_field, str(identity_value)),
            )
            conn.execute(
                "DELETE FROM sources WHERE identity_field = ? AND identity_value = ?",
                (identity_field, str(identity_value)),
            )
            conn.execute(
                "DELETE FROM index_units WHERE identity_field = ? AND identity_value = ?",
                (identity_field, str(identity_value)),
            )
        return cursor.rowcount

    def chunk_records_for_source(
        self,
        identity_field: str,
        identity_value: str,
        *,
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return registry chunk rows for one source identity."""
        where = ["identity_field = ?", "identity_value = ?"]
        params: List[Any] = [identity_field, str(identity_value)]
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            where.append(f"source_type IN ({placeholders})")
            params.extend(source_types)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT chunk_id, identity_field, identity_value, source_id,
                       source_type, chunk_level, chunk_index, variant,
                       zotero_key, attachment_key, note_key, annotation_key,
                       indexed_at
                FROM chunks
                WHERE {" AND ".join(where)}
                ORDER BY chunk_id
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_chunks_matching(
        self,
        identity_field: str,
        identity_value: str,
        *,
        source_types: Optional[List[str]] = None,
        attachment_key: Optional[str] = None,
        note_key: Optional[str] = None,
        annotation_key: Optional[str] = None,
    ) -> int:
        """Delete chunk rows matching a surgical vector-store delete."""
        where = ["identity_field = ?", "identity_value = ?"]
        params: List[Any] = [identity_field, str(identity_value)]
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            where.append(f"source_type IN ({placeholders})")
            params.extend(source_types)
        if attachment_key:
            where.append("attachment_key = ?")
            params.append(str(attachment_key))
        if note_key:
            where.append("note_key = ?")
            params.append(str(note_key))
        if annotation_key:
            where.append("annotation_key = ?")
            params.append(str(annotation_key))

        with self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM chunks WHERE {' AND '.join(where)}",
                params,
            )
        return cursor.rowcount

    def delete_sources_like(self, identity_field: str, like_pattern: str) -> int:
        """Bulk-remove chunk and source rows whose identity matches a LIKE pattern.

        Used by repair flows (e.g. wiping all 'obsidian-%' identities before a
        full vault re-index).
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM chunks WHERE identity_field = ? AND identity_value LIKE ?",
                (identity_field, like_pattern),
            )
            conn.execute(
                "DELETE FROM sources WHERE identity_field = ? AND identity_value LIKE ?",
                (identity_field, like_pattern),
            )
            conn.execute(
                "DELETE FROM index_units WHERE identity_field = ? AND identity_value LIKE ?",
                (identity_field, like_pattern),
            )
        return cursor.rowcount

    def clear_vault_state(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM vault_files")

    def refresh_sources(self) -> int:
        """Rebuild per-source aggregates (types, level counts, freshness) from chunks.

        Chunk rows are the authoritative layer; this derives the source layer
        from them so the two cannot drift apart internally. Returns the number
        of sources after refresh.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT identity_field, identity_value,
                       COALESCE(source_type, 'unknown') AS source_type,
                       COALESCE(chunk_level, 'unknown') AS chunk_level,
                       COUNT(*) AS n,
                       MIN(NULLIF(indexed_at, '')) AS first_at,
                       MAX(NULLIF(indexed_at, '')) AS last_at
                FROM chunks
                GROUP BY identity_field, identity_value, source_type, chunk_level
                """
            ).fetchall()

            aggregates: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for row in rows:
                key = (row["identity_field"], row["identity_value"])
                agg = aggregates.setdefault(
                    key,
                    {
                        "types": {},
                        "levels": {},
                        "total": 0,
                        "first_at": "",
                        "last_at": "",
                    },
                )
                type_levels = agg["types"].setdefault(row["source_type"], {})
                type_levels[row["chunk_level"]] = (
                    type_levels.get(row["chunk_level"], 0) + row["n"]
                )
                agg["levels"][row["chunk_level"]] = (
                    agg["levels"].get(row["chunk_level"], 0) + row["n"]
                )
                agg["total"] += row["n"]
                if row["first_at"] and (
                    not agg["first_at"] or row["first_at"] < agg["first_at"]
                ):
                    agg["first_at"] = row["first_at"]
                if row["last_at"] and row["last_at"] > agg["last_at"]:
                    agg["last_at"] = row["last_at"]

            conn.executemany(
                """
                INSERT INTO sources(identity_field, identity_value)
                VALUES(?, ?)
                ON CONFLICT(identity_field, identity_value) DO NOTHING
                """,
                list(aggregates.keys()),
            )
            conn.executemany(
                """
                UPDATE sources SET
                    source_types = ?,
                    counts_json = ?,
                    total_chunks = ?,
                    first_indexed_at = ?,
                    last_indexed_at = ?
                WHERE identity_field = ? AND identity_value = ?
                """,
                [
                    (
                        ",".join(sorted(agg["types"].keys())),
                        json.dumps({"levels": agg["levels"], "types": agg["types"]}),
                        agg["total"],
                        agg["first_at"],
                        agg["last_at"],
                        field,
                        value,
                    )
                    for (field, value), agg in aggregates.items()
                ],
            )
            conn.execute(
                """
                DELETE FROM sources
                WHERE NOT EXISTS (
                    SELECT 1 FROM chunks c
                    WHERE c.identity_field = sources.identity_field
                      AND c.identity_value = sources.identity_value
                )
                """
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('last_refreshed_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_utc_now_iso(),),
            )

        return len(aggregates)

    def reset(self) -> None:
        """Clear all registry data (used by --force full re-index and backfill --restart)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM sources")
            conn.execute("DELETE FROM vault_files")
            conn.execute("DELETE FROM index_units")
            conn.execute(
                "DELETE FROM meta WHERE key NOT IN ('schema_version')"
            )

    # ------------------------------------------------------------ vault state

    def get_vault_state(self) -> Dict[str, Tuple[float, int]]:
        """Per-file vault snapshot from the last successful index run."""
        with self._connect() as conn:
            return {
                row["relative_path"]: (row["mtime"], row["size"])
                for row in conn.execute(
                    "SELECT relative_path, mtime, size FROM vault_files"
                )
            }

    def set_vault_state_entries(self, entries: Dict[str, Tuple[float, int]]) -> None:
        """Upsert vault file states (called after files are successfully stored)."""
        if not entries:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO vault_files(relative_path, mtime, size)
                VALUES(?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    mtime = excluded.mtime,
                    size = excluded.size
                """,
                [
                    (path, float(state[0]), int(state[1]))
                    for path, state in entries.items()
                ],
            )

    def delete_vault_state_entries(self, relative_paths: List[str]) -> None:
        if not relative_paths:
            return
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM vault_files WHERE relative_path = ?",
                [(path,) for path in relative_paths],
            )

    def obsidian_freshness(self) -> Dict[str, str]:
        """Vault-relative path -> last_indexed_at for indexed Obsidian sources.

        Used to bootstrap the Obsidian delta when no vault snapshot exists yet
        (first run after the registry backfill).
        """
        prefix = "obsidian-"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT identity_value, last_indexed_at
                FROM sources
                WHERE identity_field = 'source_id'
                  AND identity_value LIKE 'obsidian-%'
                """
            ).fetchall()
        return {
            row["identity_value"][len(prefix):]: row["last_indexed_at"] or ""
            for row in rows
        }

    # ------------------------------------------------------------------ read

    def is_ready(self) -> bool:
        """True when the registry has source rows to serve."""
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM sources LIMIT 1").fetchone()
        return row is not None

    def chunk_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]

    def source_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]

    def list_sources_payload(
        self,
        *,
        source_type: Optional[str] = None,
        title_contains: Optional[str] = None,
        author: Optional[str] = None,
        collection: Optional[str] = None,
        item_type: Optional[str] = None,
        doi: Optional[str] = None,
        language: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Build the list_sources response payload (shape shared by MCP and CLI)."""
        where: List[str] = ["total_chunks > 0"]
        params: List[Any] = []

        if source_type:
            where.append("(',' || source_types || ',') LIKE ?")
            params.append(f"%,{source_type},%")
        if title_contains:
            where.append("lower(title) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(title_contains).lower())}%")
        if author:
            where.append("lower(authors) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(author).lower())}%")
        if collection:
            # Substring match on Zotero collection names (comma-joined).
            where.append("lower(collections) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(collection).lower())}%")
        if item_type:
            where.append("lower(item_type) = ?")
            params.append(str(item_type).lower())
        if doi:
            where.append("lower(doi) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(doi).lower())}%")
        if language:
            where.append("lower(language) = ?")
            params.append(str(language).lower())
        if tag:
            where.append("(',' || replace(lower(tags), ', ', ',') || ',') LIKE ?")
            params.append(f"%,{_like_escape(str(tag).lower())},%")

        where_sql = " AND ".join(where)

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM sources WHERE {where_sql}", params
            ).fetchone()["n"]
            rows = conn.execute(
                f"""
                SELECT * FROM sources
                WHERE {where_sql}
                ORDER BY lower(title), lower(identity_value)
                LIMIT ? OFFSET ?
                """,
                [*params, int(limit), int(offset)],
            ).fetchall()

        sources = []
        for row in rows:
            try:
                counts = json.loads(row["counts_json"] or "{}")
            except json.JSONDecodeError:
                counts = {}
            sources.append(
                {
                    "identity_field": row["identity_field"],
                    "identity_value": row["identity_value"],
                    "title": row["title"] or "Untitled",
                    "authors": row["authors"] or "Unknown",
                    "year": row["year"] or "",
                    "source_type": row["source_types"] or "unknown",
                    "backlink": row["backlink"] or None,
                    "collections": row["collections"] or "",
                    "item_type": row["item_type"] or "",
                    "doi": row["doi"] or "",
                    "abstract": row["abstract"] or "",
                    "tags": row["tags"] or "",
                    "venue": row["venue"] or "",
                    "language": row["language"] or "",
                    "extractor": row["extractor"] or "",
                    "extract_quality": row["extract_quality"] or "",
                    "extract_action": row["extract_action"] or "",
                    "chunk_counts": counts.get("levels", {}),
                    "chunk_counts_by_type": counts.get("types", {}),
                    "total_chunks": row["total_chunks"],
                    "freshness": row["last_indexed_at"] or "unknown",
                }
            )

        return {
            "total_sources": total,
            "page": {
                "offset": int(offset),
                "limit": int(limit),
                "returned": len(sources),
            },
            "filters": {
                "source_type": source_type,
                "title_contains": title_contains,
                "author": author,
                "collection": collection,
                "item_type": item_type,
                "doi": doi,
                "language": language,
                "tag": tag,
            },
            "sources": sources,
        }

    def sources_by_identity(
        self,
        identities: List[Tuple[str, str]],
        *,
        source_type: Optional[str] = None,
        title_contains: Optional[str] = None,
        author: Optional[str] = None,
        collection: Optional[str] = None,
        item_type: Optional[str] = None,
        doi: Optional[str] = None,
        language: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Fetch source payload rows for exact identities, with list-source filters."""
        if not identities:
            return {}

        unique_identities = list(dict.fromkeys((field, str(value)) for field, value in identities))
        identity_clauses = []
        params: List[Any] = []
        for field, value in unique_identities:
            identity_clauses.append("(identity_field = ? AND identity_value = ?)")
            params.extend([field, value])

        where: List[str] = [
            "total_chunks > 0",
            "(" + " OR ".join(identity_clauses) + ")",
        ]

        if source_type:
            where.append("(',' || source_types || ',') LIKE ?")
            params.append(f"%,{source_type},%")
        if title_contains:
            where.append("lower(title) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(title_contains).lower())}%")
        if author:
            where.append("lower(authors) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(author).lower())}%")
        if collection:
            where.append("lower(collections) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(collection).lower())}%")
        if item_type:
            where.append("lower(item_type) = ?")
            params.append(str(item_type).lower())
        if doi:
            where.append("lower(doi) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(str(doi).lower())}%")
        if language:
            where.append("lower(language) = ?")
            params.append(str(language).lower())
        if tag:
            where.append("(',' || replace(lower(tags), ', ', ',') || ',') LIKE ?")
            params.append(f"%,{_like_escape(str(tag).lower())},%")

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM sources
                WHERE {" AND ".join(where)}
                """,
                params,
            ).fetchall()

        out: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            try:
                counts = json.loads(row["counts_json"] or "{}")
            except json.JSONDecodeError:
                counts = {}
            out[(row["identity_field"], row["identity_value"])] = {
                "identity_field": row["identity_field"],
                "identity_value": row["identity_value"],
                "title": row["title"] or "Untitled",
                "authors": row["authors"] or "Unknown",
                "year": row["year"] or "",
                "source_type": row["source_types"] or "unknown",
                "backlink": row["backlink"] or None,
                "collections": row["collections"] or "",
                "item_type": row["item_type"] or "",
                "doi": row["doi"] or "",
                "abstract": row["abstract"] or "",
                "tags": row["tags"] or "",
                "venue": row["venue"] or "",
                "language": row["language"] or "",
                "extractor": row["extractor"] or "",
                "extract_quality": row["extract_quality"] or "",
                "extract_action": row["extract_action"] or "",
                "chunk_counts": counts.get("levels", {}),
                "chunk_counts_by_type": counts.get("types", {}),
                "total_chunks": row["total_chunks"],
                "freshness": row["last_indexed_at"] or "unknown",
            }
        return out

    def status(self) -> Dict[str, Any]:
        """Registry health snapshot for index_status and CLI."""
        with self._connect() as conn:
            meta = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM meta").fetchall()
            }
            chunk_count = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            source_count = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
            index_unit_count = conn.execute(
                "SELECT COUNT(*) AS n FROM index_units"
            ).fetchone()["n"]
            ledger_drift = self._ledger_drift_report(conn)
        return {
            "db_path": str(self.db_path),
            "schema_version": meta.get("schema_version", ""),
            "source_count": source_count,
            "chunk_count": chunk_count,
            "index_unit_count": index_unit_count,
            "ledger_drift": ledger_drift,
            "last_refreshed_at": meta.get("last_refreshed_at", ""),
            "last_backfill_at": meta.get("last_backfill_at", ""),
            "backfill_complete": meta.get("backfill_complete", "") == "1",
            "backfill_offset": int(meta.get("backfill_offset", "0") or 0),
            "last_index_run_at": meta.get("last_index_run_at", ""),
        }

    def _ledger_drift_report(
        self, conn: sqlite3.Connection, *, sample_limit: int = 10
    ) -> Dict[str, Any]:
        """Registry-only consistency check between index_units and chunks.

        The report deliberately reads only SQLite registry tables. Chroma drift
        remains the live vector-store count check; this one catches ledger gaps
        that would make plan-driven updates unsafe or incomplete.
        """
        unit_rows = conn.execute(
            """
            SELECT unit_id, identity_field, identity_value, unit_kind
            FROM index_units
            WHERE unit_kind IN ('attachment', 'note', 'annotation', 'vault_file')
            ORDER BY unit_id
            """
        ).fetchall()

        chunkless_samples: List[Dict[str, str]] = []
        expected_chunkless_samples: List[Dict[str, str]] = []
        unexpected_chunkless_samples: List[Dict[str, str]] = []
        chunkless_count = 0
        expected_chunkless_count = 0
        unexpected_chunkless_count = 0
        for row in unit_rows:
            unit_kind = row["unit_kind"]
            where = ["identity_field = ?", "identity_value = ?"]
            params: List[Any] = [row["identity_field"], row["identity_value"]]

            child_key = _unit_child_key(row["unit_id"], unit_kind)
            if unit_kind == "attachment":
                where.append("attachment_key = ?")
                params.append(child_key)
            elif unit_kind == "note":
                where.append("note_key = ?")
                params.append(child_key)
            elif unit_kind == "annotation":
                where.append("annotation_key = ?")
                params.append(child_key)

            found = conn.execute(
                f"SELECT 1 FROM chunks WHERE {' AND '.join(where)} LIMIT 1",
                params,
            ).fetchone()
            if not found:
                chunkless_count += 1
                source = conn.execute(
                    """
                    SELECT title, total_chunks, source_types
                    FROM sources
                    WHERE identity_field = ? AND identity_value = ?
                    """,
                    (row["identity_field"], row["identity_value"]),
                ).fetchone()
                identity_chunks = conn.execute(
                    """
                    SELECT attachment_key, note_key, annotation_key, source_type
                    FROM chunks
                    WHERE identity_field = ? AND identity_value = ?
                    LIMIT 20
                    """,
                    (row["identity_field"], row["identity_value"]),
                ).fetchall()
                expected = False
                reason = "missing_indexed_chunks"
                if unit_kind == "attachment" and identity_chunks:
                    expected = True
                    attachment_keys = {
                        str(chunk["attachment_key"] or "")
                        for chunk in identity_chunks
                    }
                    if attachment_keys - {"", child_key}:
                        reason = "sibling_attachment_indexed"
                    else:
                        reason = "no_indexed_fulltext_for_attachment"

                sample = {
                    "unit_id": row["unit_id"],
                    "identity_field": row["identity_field"],
                    "identity_value": row["identity_value"],
                    "unit_kind": unit_kind,
                    "child_key": child_key,
                    "reason": reason,
                    "title": str(source["title"] if source else ""),
                }
                if len(chunkless_samples) < sample_limit:
                    chunkless_samples.append(sample)
                if expected:
                    expected_chunkless_count += 1
                    if len(expected_chunkless_samples) < sample_limit:
                        expected_chunkless_samples.append(sample)
                else:
                    unexpected_chunkless_count += 1
                    if len(unexpected_chunkless_samples) < sample_limit:
                        unexpected_chunkless_samples.append(sample)

        orphan_rows = conn.execute(
            """
            SELECT c.identity_field, c.identity_value, COUNT(*) AS chunk_count
            FROM chunks c
            WHERE NOT EXISTS (
                SELECT 1 FROM index_units u
                WHERE u.identity_field = c.identity_field
                  AND u.identity_value = c.identity_value
            )
            GROUP BY c.identity_field, c.identity_value
            ORDER BY c.identity_field, c.identity_value
            """
        ).fetchall()
        orphan_samples = [
            {
                "identity_field": row["identity_field"],
                "identity_value": row["identity_value"],
                "chunk_count": row["chunk_count"],
            }
            for row in orphan_rows[:sample_limit]
        ]
        orphan_identity_count = len(orphan_rows)
        orphan_chunk_count = sum(int(row["chunk_count"] or 0) for row in orphan_rows)

        return {
            "chunkless_unit_count": chunkless_count,
            "chunkless_unit_samples": chunkless_samples,
            "expected_chunkless_unit_count": expected_chunkless_count,
            "expected_chunkless_unit_samples": expected_chunkless_samples,
            "unexpected_chunkless_unit_count": unexpected_chunkless_count,
            "unexpected_chunkless_unit_samples": unexpected_chunkless_samples,
            "orphan_identity_count": orphan_identity_count,
            "orphan_chunk_count": orphan_chunk_count,
            "orphan_identity_samples": orphan_samples,
            "ok": unexpected_chunkless_count == 0 and orphan_identity_count == 0,
        }


def backfill_from_collection(
    registry: SourceRegistry,
    collection: Any,
    *,
    batch_size: int = 5000,
    restart: bool = False,
    progress: Optional[Callable[[str], None]] = print,
) -> Dict[str, Any]:
    """Scan a Chroma collection's metadata into the registry, resumably.

    The scan offset is committed in the same transaction as each recorded
    batch, so an interrupted run (Ctrl-C, reboot, crash) resumes from its
    last committed batch instead of starting over.
    """
    emit = progress or (lambda _msg: None)

    if restart:
        registry.reset()

    if registry.get_meta("backfill_complete") == "1":
        return {
            "skipped": True,
            "reason": "backfill already complete (use --restart to rebuild)",
            "chunks_recorded": 0,
        }

    collection_count = int(collection.count())
    offset = int(registry.get_meta("backfill_offset", "0") or 0)

    count_at_start = registry.get_meta("backfill_collection_count")
    if offset > 0 and count_at_start and int(count_at_start) != collection_count:
        emit(
            f"[WARN] Collection count changed since backfill started "
            f"({count_at_start} -> {collection_count}). Offsets may have shifted; "
            f"the post-backfill audit/reconcile will catch any gaps. "
            f"Use --restart for a clean rebuild."
        )
    if offset == 0:
        registry.set_meta("backfill_collection_count", str(collection_count))
        registry.set_meta("backfill_started_at", _utc_now_iso())

    started = time.monotonic()
    scanned_this_run = 0

    while True:
        batch = collection.get(
            include=["metadatas"],
            limit=batch_size,
            offset=offset,
        )
        ids = batch.get("ids", []) or []
        metadatas = batch.get("metadatas", []) or []
        if not ids:
            break

        registry.record_chunks(
            ids,
            metadatas,
            meta_updates={"backfill_offset": str(offset + len(ids))},
        )
        offset += len(ids)
        scanned_this_run += len(ids)

        elapsed = time.monotonic() - started
        rate = scanned_this_run / elapsed if elapsed > 0 else 0.0
        remaining = max(0, collection_count - offset)
        eta_min = (remaining / rate / 60) if rate > 0 else float("inf")
        emit(
            f"[BACKFILL] {offset:,}/{collection_count:,} chunks "
            f"({offset / max(1, collection_count) * 100:.1f}%) | "
            f"{rate:,.0f} chunks/s this run | ETA {eta_min:,.0f} min"
        )

        if len(ids) < batch_size:
            break

    emit("[BACKFILL] Scan finished; refreshing source aggregates...")
    source_total = registry.refresh_sources()
    registry.set_meta("backfill_complete", "1")
    registry.set_meta("last_backfill_at", _utc_now_iso())
    registry.set_meta("backfill_chroma_count", str(collection_count))

    return {
        "skipped": False,
        "chunks_recorded": scanned_this_run,
        "total_offset": offset,
        "collection_count": collection_count,
        "source_count": source_total,
    }
