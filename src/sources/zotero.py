"""Zotero data source for extracting items, notes, and attachments."""

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import html2text
import requests

from ..extract_text import extract_text
from ..processing.extraction import ExtractionInput, ExtractionRouter
from .base import DataSource, Document, ProgressCallback, UnitState


@dataclass
class ExtractionTask:
    """Represents a single attachment extraction task."""

    file_path: Path
    attachment_id: int
    attachment_key: str
    filename: str
    content_type: str
    file_size_mb: float
    zotero_item_id: int
    index: int = 0  # Assigned after sorting for deterministic ordering


@dataclass
class ExtractionResult:
    """Result of extracting text from an attachment."""

    task: ExtractionTask
    text: Optional[str]
    error: Optional[str]
    elapsed_seconds: float


class ZoteroSource(DataSource):
    """Data source for Zotero library."""

    def __init__(
        self,
        config: Dict[str, Any],
        progress_callback: Optional[ProgressCallback] = None,
    ):
        super().__init__(config, progress_callback)
        self.zotero_config = config.get("zotero", {})
        self.extraction_config = config.get("extraction", {})
        self.data_dir = None
        self.db_path = None
        self.storage_dir = None

        # Extraction settings with backwards compatibility
        self.parallel_enabled = self.extraction_config.get("parallel", True)
        # Legacy fallback: zotero.max_extraction_threads -> extraction.workers
        legacy_threads = self.zotero_config.get("max_extraction_threads")
        self.configured_workers = self.extraction_config.get("workers", legacy_threads or "auto")
        self.local_api_base = self.zotero_config.get("local_api_base", "http://localhost:23119/api/users/0")
        self.local_api_timeout = float(self.zotero_config.get("local_api_timeout", 20))
        self.prefer_local_api_fulltext = bool(self.zotero_config.get("prefer_local_api_fulltext", True))
        self.fulltext_min_chars = int(self.zotero_config.get("fulltext_min_chars", 200))
        self.fulltext_large_pdf_mb = float(self.zotero_config.get("fulltext_large_pdf_mb", 20))
        self.fulltext_large_pdf_min_chars = int(
            self.zotero_config.get("fulltext_large_pdf_min_chars", 20000)
        )
        self.fulltext_bootstrap_scan = bool(self.zotero_config.get("fulltext_bootstrap_scan", False))
        self.extraction_router = ExtractionRouter(config)

        if self.is_enabled():
            self.data_dir = Path(self.zotero_config.get("data_directory", "")).expanduser()
            self.db_path = self.data_dir / "zotero.sqlite"
            self.storage_dir = self.data_dir / "storage"

    def _get_worker_count(self) -> int:
        """Get the number of workers for parallel extraction."""
        if self.configured_workers == "auto":
            return os.cpu_count() or 4
        return int(self.configured_workers)

    def is_enabled(self) -> bool:
        """Check if Zotero source is enabled."""
        return self.zotero_config.get("enabled", False)

    def validate_config(self) -> bool:
        """Validate Zotero configuration."""
        if not self.is_enabled():
            return True

        if not self.data_dir or not self.data_dir.exists():
            print(f"⚠️  Zotero data directory not found: {self.data_dir}")
            return False

        if not self.db_path.exists():
            print(f"⚠️  Zotero database not found: {self.db_path}")
            return False

        return True

    def fetch_documents(
        self,
        item_keys: Optional[List[str]] = None,
        *,
        kinds: Optional[set[str]] = None,
        attachment_keys: Optional[set[str]] = None,
    ) -> Iterator[Document]:
        """
        Fetch all documents from Zotero library.

        Yields documents for:
        - Item notes (as separate documents)
        - Item attachments (full text extraction from PDFs, etc.)
        - Item annotations (if enabled)

        Uses item-level parallelization when parallel extraction is enabled,
        processing multiple Zotero items concurrently with thread-per-item.
        """
        if not self.validate_config():
            return

        conn = self._get_db_connection()
        if not conn:
            return

        try:
            if item_keys is not None:
                items = self._get_items_by_keys(conn, item_keys)
            else:
                items = self._get_all_items(conn)
            limit = self.zotero_config.get("limit_items")
            if isinstance(limit, int) and limit > 0:
                items = items[:limit]
            total_items = len(items)
            if item_keys is not None:
                print(f"[INFO] Found {total_items} changed Zotero items to process")
            else:
                print(f"[INFO] Found {total_items} items in Zotero library")

            # Emit source initialization
            self._emit_progress("source_init", total=total_items)

            # Close main connection - parallel processing uses per-thread connections
            conn.close()
            conn = None

            if self.parallel_enabled:
                yield from self._fetch_documents_parallel(
                    items,
                    total_items,
                    kinds=kinds,
                    attachment_keys=attachment_keys,
                )
            else:
                yield from self._fetch_documents_sequential(
                    items,
                    total_items,
                    kinds=kinds,
                    attachment_keys=attachment_keys,
                )

        finally:
            if conn:
                conn.close()
            self._emit_progress("source_complete")

    def fetch_item_documents(
        self,
        item_key: str,
        *,
        kinds: Optional[set[str]] = None,
        attachment_keys: Optional[set[str]] = None,
    ) -> Iterator[Document]:
        """Fetch documents for one top-level item with optional unit selectors."""
        yield from self.fetch_documents(
            item_keys=[item_key],
            kinds=kinds,
            attachment_keys=attachment_keys,
        )

    def _fetch_documents_sequential(
        self,
        items: List,
        total_items: int,
        *,
        kinds: Optional[set[str]] = None,
        attachment_keys: Optional[set[str]] = None,
    ) -> Iterator[Document]:
        """Process items sequentially (fallback mode)."""
        conn = self._get_db_connection()
        if not conn:
            return

        try:
            for idx, item_row in enumerate(items):
                item_id = item_row["itemID"]
                self._emit_progress("item_start", item_id=item_id, index=idx, total=total_items)

                try:
                    docs_yielded = 0
                    for doc in self._process_item(
                        conn,
                        item_id,
                        kinds=kinds,
                        attachment_keys=attachment_keys,
                    ):
                        yield doc
                        docs_yielded += 1

                    self._emit_progress(
                        "item_complete",
                        item_id=item_id,
                        index=idx,
                        total=total_items,
                        docs_yielded=docs_yielded
                    )
                except Exception as e:
                    self._emit_progress(
                        "item_error",
                        item_id=item_id,
                        index=idx,
                        error=str(e)
                    )
        finally:
            conn.close()

    def _fetch_documents_parallel(
        self,
        items: List,
        total_items: int,
        *,
        kinds: Optional[set[str]] = None,
        attachment_keys: Optional[set[str]] = None,
    ) -> Iterator[Document]:
        """
        Process items in parallel using a sliding window approach.

        Maintains a constant number of in-flight tasks by submitting new items
        as workers become free. Results are buffered and yielded in deterministic
        order (original item order).
        """
        worker_count = self._get_worker_count()
        max_in_flight = worker_count * 2  # Keep this many tasks queued

        print(f"    [Parallel] Using {worker_count} workers, max {max_in_flight} in-flight")

        # Results buffer keyed by index for deterministic ordering
        results_buffer: Dict[int, List[Document]] = {}
        next_to_yield = 0  # Next index we need to yield
        next_to_submit = 0  # Next index to submit

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending_futures: Dict[Any, tuple] = {}  # future -> (idx, item_id)

            # Initial submission to fill the pipeline
            while next_to_submit < total_items and len(pending_futures) < max_in_flight:
                item_row = items[next_to_submit]
                future = executor.submit(
                    self._process_item_standalone,
                    item_row["itemID"],
                    next_to_submit,
                    total_items,
                    kinds,
                    attachment_keys,
                )
                pending_futures[future] = (next_to_submit, item_row["itemID"])
                next_to_submit += 1

            # Process results as they complete, submitting new tasks to replace them
            while pending_futures:
                # Wait for at least one future to complete
                done_futures = set()
                for future in pending_futures:
                    if future.done():
                        done_futures.add(future)

                # If none are done yet, wait for one
                if not done_futures:
                    import concurrent.futures
                    done, _ = concurrent.futures.wait(
                        pending_futures.keys(),
                        return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    done_futures = done

                # Process completed futures
                for future in done_futures:
                    idx, item_id = pending_futures.pop(future)
                    try:
                        docs = future.result()
                        results_buffer[idx] = docs
                        self._emit_progress(
                            "item_complete",
                            item_id=item_id,
                            index=idx,
                            total=total_items,
                            docs_yielded=len(docs)
                        )
                    except Exception as e:
                        results_buffer[idx] = []
                        self._emit_progress(
                            "item_error",
                            item_id=item_id,
                            index=idx,
                            error=str(e)
                        )

                    # Submit a new task to replace the completed one
                    if next_to_submit < total_items:
                        item_row = items[next_to_submit]
                        new_future = executor.submit(
                            self._process_item_standalone,
                            item_row["itemID"],
                            next_to_submit,
                            total_items,
                            kinds,
                            attachment_keys,
                        )
                        pending_futures[new_future] = (next_to_submit, item_row["itemID"])
                        next_to_submit += 1

                # Yield any results that are ready in order
                while next_to_yield in results_buffer:
                    for doc in results_buffer.pop(next_to_yield):
                        yield doc
                    next_to_yield += 1

            # Yield any remaining buffered results
            while next_to_yield in results_buffer:
                for doc in results_buffer.pop(next_to_yield):
                    yield doc
                next_to_yield += 1

    def _process_item_standalone(
        self,
        item_id: int,
        idx: int,
        total: int,
        kinds: Optional[set[str]] = None,
        attachment_keys: Optional[set[str]] = None,
    ) -> List[Document]:
        """
        Process a single item with its own DB connection (thread-safe).

        Returns a list of documents instead of yielding, for thread-pool compatibility.
        """
        self._emit_progress("item_start", item_id=item_id, index=idx, total=total)

        conn = self._get_db_connection()
        if not conn:
            return []

        try:
            return list(
                self._process_item(
                    conn,
                    item_id,
                    kinds=kinds,
                    attachment_keys=attachment_keys,
                )
            )
        finally:
            conn.close()

    def _get_db_connection(self) -> Optional[sqlite3.Connection]:
        """Establish read-only connection to Zotero SQLite database."""
        try:
            uri = f"{self.db_path.as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            return conn
        except sqlite3.Error as e:
            print(f"❌ Database connection error: {e}")
            return None

    def _get_all_items(self, conn: sqlite3.Connection) -> List[sqlite3.Row]:
        """Get all non-deleted top-level items.

        Child attachments/notes/annotations are processed via their parent
        item, never as items in their own right. Processing them directly
        used to attribute PDF annotations to the attachment's key instead of
        the parent's, splitting one source across two identities.
        """
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.itemID, i.itemTypeID, i.dateAdded, i.dateModified, i.key
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            ORDER BY i.itemID
            """
        )
        return cursor.fetchall()

    def _get_items_by_keys(self, conn: sqlite3.Connection, keys: List[str]) -> List[sqlite3.Row]:
        """Get non-deleted top-level items by Zotero item key."""
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT i.itemID, i.itemTypeID, i.dateAdded, i.dateModified, i.key
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            WHERE i.key IN ({placeholders})
              AND it.typeName NOT IN ('attachment', 'note', 'annotation')
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            ORDER BY i.itemID
            """,
            tuple(keys),
        )
        return cursor.fetchall()

    def get_delta_changes(
        self,
        *,
        last_item_version: int = 0,
        last_fulltext_version: int = 0,
        last_sqlite_date_modified: str = "",
        last_sqlite_date_deleted: str = "",
        last_sqlite_attachment_storage_mod_time: int = 0,
    ) -> Dict[str, Any]:
        """Fetch changed Zotero item keys since the given versions.

        Returns:
            {
                "changed_item_keys": [...],
                "item_version": <int>,
                "fulltext_version": <int>,
            }
        """
        sqlite_max_date_modified = last_sqlite_date_modified
        sqlite_max_date_deleted = last_sqlite_date_deleted
        sqlite_max_attachment_storage_mod_time = int(last_sqlite_attachment_storage_mod_time or 0)
        if self._is_local_api_available():
            item_keys, item_version = self._fetch_changed_item_keys(last_item_version)
            # /items?since= does NOT report deletions; they live on /deleted.
            # Without this, items deleted while Zotero is open keep their
            # chunks in the index forever.
            deleted_api_keys = self._fetch_deleted_item_keys(last_item_version)
            if deleted_api_keys:
                item_keys = list(dict.fromkeys([*item_keys, *deleted_api_keys]))
            fulltext_attachment_keys, fulltext_version = self._fetch_changed_fulltext_keys(last_fulltext_version)
            sqlite_watermarks = self._get_current_sqlite_delta_watermarks()
            sqlite_max_date_modified = sqlite_watermarks["sqlite_max_date_modified"]
            sqlite_max_date_deleted = sqlite_watermarks["sqlite_max_date_deleted"]
            sqlite_max_attachment_storage_mod_time = sqlite_watermarks[
                "sqlite_max_attachment_storage_mod_time"
            ]
        else:
            # Zotero closed => local API unavailable; use SQLite item-level
            # effective modification/deletion watermarks.
            (
                item_keys,
                sqlite_max_date_modified,
                deleted_item_keys,
                sqlite_max_date_deleted,
                attachment_parent_keys,
                sqlite_max_attachment_storage_mod_time,
            ) = self._fetch_changed_parent_item_keys_sqlite(
                last_sqlite_date_modified,
                last_sqlite_date_deleted,
                int(last_sqlite_attachment_storage_mod_time or 0),
            )
            item_version = int(last_item_version)
            fulltext_attachment_keys = []
            fulltext_version = int(last_fulltext_version)
            if deleted_item_keys:
                item_keys = list(dict.fromkeys([*item_keys, *deleted_item_keys]))
            if attachment_parent_keys:
                item_keys = list(dict.fromkeys([*item_keys, *attachment_parent_keys]))

        changed = set(self._resolve_parent_keys_for_any_item_keys(item_keys))
        if fulltext_attachment_keys:
            changed.update(self._resolve_parent_item_keys(fulltext_attachment_keys))

        return {
            "changed_item_keys": sorted(changed),
            "item_version": item_version,
            "fulltext_version": fulltext_version,
            "sqlite_max_date_modified": sqlite_max_date_modified,
            "sqlite_max_date_deleted": sqlite_max_date_deleted,
            "sqlite_max_attachment_storage_mod_time": sqlite_max_attachment_storage_mod_time,
        }

    def _is_local_api_available(self) -> bool:
        """Check whether Zotero local API is reachable."""
        url = f"{self.local_api_base}/items"
        try:
            response = requests.get(
                url,
                params={"limit": 1, "format": "keys"},
                headers={"Zotero-Allowed-Request": "1"},
                timeout=3,
            )
            return response.status_code == 200
        except Exception:
            return False

    def _fetch_changed_item_keys(self, since_version: int) -> tuple[List[str], int]:
        """Fetch item keys changed since the given Zotero library version."""
        url = f"{self.local_api_base}/items"
        try:
            response = requests.get(
                url,
                params={"since": max(0, int(since_version)), "format": "keys"},
                headers={"Zotero-Allowed-Request": "1"},
                timeout=self.local_api_timeout,
            )
            response.raise_for_status()
            keys = [line.strip() for line in response.text.splitlines() if line.strip()]
            version = int(response.headers.get("Last-Modified-Version", since_version))
            return keys, version
        except Exception as e:
            print(f"[WARN] Failed to fetch changed item keys from Zotero API: {e}")
            return [], int(since_version)

    def _fetch_changed_item_keys_sqlite(self, since_date_modified: str) -> tuple[List[str], str]:
        """Fetch changed item keys from SQLite using dateModified watermark."""
        conn = self._get_db_connection()
        if not conn:
            return [], since_date_modified
        try:
            cursor = conn.cursor()
            if since_date_modified:
                cursor.execute(
                    """
                    SELECT key, dateModified
                    FROM items
                    WHERE itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND dateModified > ?
                    ORDER BY dateModified
                    """,
                    (since_date_modified,),
                )
            else:
                cursor.execute(
                    """
                    SELECT key, dateModified
                    FROM items
                    WHERE itemID NOT IN (SELECT itemID FROM deletedItems)
                    ORDER BY dateModified
                    """
                )
            rows = cursor.fetchall()
            keys = [row["key"] for row in rows if row["key"]]
            if rows:
                max_modified = rows[-1]["dateModified"] or since_date_modified
            else:
                max_modified = since_date_modified
            return keys, max_modified
        except Exception as e:
            print(f"[WARN] Failed to fetch changed item keys from SQLite: {e}")
            return [], since_date_modified
        finally:
            conn.close()

    def _fetch_changed_parent_item_keys_sqlite(
        self,
        since_date_modified: str,
        since_date_deleted: str,
        since_attachment_storage_mod_time: int,
    ) -> tuple[List[str], str, List[str], str, List[str], int]:
        """Fetch changed top-level Zotero item keys using SQLite only.

        This is the closed-Zotero fallback path. Unlike the raw items.dateModified
        query, it tracks changes at the parent item level by considering:
        - the top-level item's own dateModified
        - child note item dateModified
        - child attachment item dateModified
        - child annotation item dateModified
        - deletions of top-level items and child records via deletedItems.dateDeleted
        """
        conn = self._get_db_connection()
        if not conn:
            return [], since_date_modified, [], since_date_deleted, [], since_attachment_storage_mod_time

        deleted_item_keys: List[str] = []
        attachment_parent_keys: List[str] = []
        try:
            cursor = conn.cursor()

            modified_sql = """
                SELECT effective_key, MAX(changed_at) AS effective_modified
                FROM (
                    SELECT parent.key AS effective_key, parent.dateModified AS changed_at
                    FROM items parent
                    JOIN itemTypes it ON it.itemTypeID = parent.itemTypeID
                    WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT parent.key AS effective_key, child.dateModified AS changed_at
                    FROM items child
                    JOIN itemNotes n ON n.itemID = child.itemID
                    JOIN items parent ON parent.itemID = n.parentItemID
                    WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT parent.key AS effective_key, child.dateModified AS changed_at
                    FROM items child
                    JOIN itemAttachments ia ON ia.itemID = child.itemID
                    JOIN items parent ON parent.itemID = ia.parentItemID
                    WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT parent.key AS effective_key, child.dateModified AS changed_at
                    FROM items child
                    JOIN itemAnnotations an ON an.itemID = child.itemID
                    JOIN itemAttachments att ON att.itemID = an.parentItemID
                    JOIN items parent ON parent.itemID = att.parentItemID
                    WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND att.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                )
                {where_clause}
                GROUP BY effective_key
                ORDER BY effective_modified
            """

            if since_date_modified:
                cursor.execute(
                    modified_sql.format(where_clause="WHERE changed_at > ?"),
                    (since_date_modified,),
                )
            else:
                cursor.execute(modified_sql.format(where_clause=""))

            modified_rows = cursor.fetchall()
            changed_item_keys = [row["effective_key"] for row in modified_rows if row["effective_key"]]
            if modified_rows:
                max_modified = modified_rows[-1]["effective_modified"] or since_date_modified
            else:
                max_modified = since_date_modified

            deleted_sql = """
                SELECT effective_key, MAX(date_deleted) AS effective_deleted
                FROM (
                    SELECT child.key AS effective_key, d.dateDeleted AS date_deleted
                    FROM deletedItems d
                    JOIN items child ON child.itemID = d.itemID
                    JOIN itemTypes it ON it.itemTypeID = child.itemTypeID
                    WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')

                    UNION ALL

                    SELECT parent.key AS effective_key, d.dateDeleted AS date_deleted
                    FROM deletedItems d
                    JOIN itemNotes n ON n.itemID = d.itemID
                    JOIN items parent ON parent.itemID = n.parentItemID
                    WHERE parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT parent.key AS effective_key, d.dateDeleted AS date_deleted
                    FROM deletedItems d
                    JOIN itemAttachments ia ON ia.itemID = d.itemID
                    JOIN items parent ON parent.itemID = ia.parentItemID
                    WHERE parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT parent.key AS effective_key, d.dateDeleted AS date_deleted
                    FROM deletedItems d
                    JOIN itemAnnotations an ON an.itemID = d.itemID
                    JOIN itemAttachments att ON att.itemID = an.parentItemID
                    JOIN items parent ON parent.itemID = att.parentItemID
                    WHERE att.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                )
                {where_clause}
                GROUP BY effective_key
                ORDER BY effective_deleted
            """

            if since_date_deleted:
                cursor.execute(
                    deleted_sql.format(where_clause="WHERE date_deleted > ?"),
                    (since_date_deleted,),
                )
            else:
                cursor.execute(deleted_sql.format(where_clause=""))

            deleted_rows = cursor.fetchall()
            deleted_item_keys = [row["effective_key"] for row in deleted_rows if row["effective_key"]]
            if deleted_rows:
                max_deleted = deleted_rows[-1]["effective_deleted"] or since_date_deleted
            else:
                max_deleted = since_date_deleted

            attachment_sql = """
                SELECT parent.key AS effective_key, MAX(ia.storageModTime) AS max_storage_mod_time
                FROM itemAttachments ia
                JOIN items parent ON parent.itemID = ia.parentItemID
                WHERE ia.parentItemID IS NOT NULL
                  AND ia.storageModTime IS NOT NULL
                  AND ia.storageModTime > ?
                  AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                GROUP BY parent.key
                ORDER BY max_storage_mod_time
            """
            cursor.execute(
                attachment_sql,
                (int(since_attachment_storage_mod_time or 0),),
            )
            attachment_rows = cursor.fetchall()
            attachment_parent_keys = [
                row["effective_key"] for row in attachment_rows if row["effective_key"]
            ]
            if attachment_rows:
                max_attachment_storage_mod_time = int(
                    attachment_rows[-1]["max_storage_mod_time"] or since_attachment_storage_mod_time
                )
            else:
                max_attachment_storage_mod_time = int(since_attachment_storage_mod_time or 0)

            return (
                list(dict.fromkeys(changed_item_keys)),
                max_modified,
                list(dict.fromkeys(deleted_item_keys)),
                max_deleted,
                list(dict.fromkeys(attachment_parent_keys)),
                max_attachment_storage_mod_time,
            )
        except Exception as e:
            print(f"[WARN] Failed to fetch changed parent item keys from SQLite: {e}")
            return (
                [],
                since_date_modified,
                [],
                since_date_deleted,
                [],
                int(since_attachment_storage_mod_time or 0),
            )
        finally:
            conn.close()

    def _get_current_sqlite_delta_watermarks(self) -> Dict[str, Any]:
        """Return current SQLite-side watermarks for future closed-Zotero delta runs."""
        conn = self._get_db_connection()
        if not conn:
            return {
                "sqlite_max_date_modified": "",
                "sqlite_max_date_deleted": "",
                "sqlite_max_attachment_storage_mod_time": 0,
            }
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT MAX(effective_modified) AS max_effective_modified
                FROM (
                    SELECT parent.dateModified AS effective_modified
                    FROM items parent
                    JOIN itemTypes it ON it.itemTypeID = parent.itemTypeID
                    WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT child.dateModified AS effective_modified
                    FROM items child
                    JOIN itemNotes n ON n.itemID = child.itemID
                    JOIN items parent ON parent.itemID = n.parentItemID
                    WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT child.dateModified AS effective_modified
                    FROM items child
                    JOIN itemAttachments ia ON ia.itemID = child.itemID
                    JOIN items parent ON parent.itemID = ia.parentItemID
                    WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)

                    UNION ALL

                    SELECT child.dateModified AS effective_modified
                    FROM items child
                    JOIN itemAnnotations an ON an.itemID = child.itemID
                    JOIN itemAttachments att ON att.itemID = an.parentItemID
                    JOIN items parent ON parent.itemID = att.parentItemID
                    WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND att.itemID NOT IN (SELECT itemID FROM deletedItems)
                      AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                )
                """
            )
            modified_row = cursor.fetchone()
            max_modified = (
                modified_row["max_effective_modified"]
                if modified_row and modified_row["max_effective_modified"]
                else ""
            )

            cursor.execute("SELECT MAX(dateDeleted) AS max_date_deleted FROM deletedItems")
            deleted_row = cursor.fetchone()
            max_deleted = (
                deleted_row["max_date_deleted"]
                if deleted_row and deleted_row["max_date_deleted"]
                else ""
            )

            cursor.execute(
                """
                SELECT MAX(storageModTime) AS max_storage_mod_time
                FROM itemAttachments
                WHERE storageModTime IS NOT NULL
                """
            )
            attachment_row = cursor.fetchone()
            max_attachment_storage_mod_time = int(
                attachment_row["max_storage_mod_time"]
                if attachment_row and attachment_row["max_storage_mod_time"]
                else 0
            )

            return {
                "sqlite_max_date_modified": max_modified,
                "sqlite_max_date_deleted": max_deleted,
                "sqlite_max_attachment_storage_mod_time": max_attachment_storage_mod_time,
            }
        except Exception as e:
            print(f"[WARN] Failed to fetch current SQLite delta watermarks: {e}")
            return {
                "sqlite_max_date_modified": "",
                "sqlite_max_date_deleted": "",
                "sqlite_max_attachment_storage_mod_time": 0,
            }
        finally:
            conn.close()

    def _fetch_deleted_item_keys(self, since_version: int) -> List[str]:
        """Fetch keys of items deleted since the given library version.

        Uses the /deleted endpoint, which is the only place the Zotero API
        reports deletions. Skipped on bootstrap (since_version <= 0), where
        the historical deletion list would be large and predates the delta
        cycle anyway.
        """
        if since_version <= 0:
            return []

        url = f"{self.local_api_base}/deleted"
        try:
            response = requests.get(
                url,
                params={"since": max(0, int(since_version))},
                headers={"Zotero-Allowed-Request": "1"},
                timeout=self.local_api_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            keys = payload.get("items", [])
            if not isinstance(keys, list):
                return []
            return [str(key) for key in keys if key]
        except Exception as e:
            print(f"[WARN] Failed to fetch deleted item keys from Zotero API: {e}")
            return []

    def _fetch_changed_fulltext_keys(self, since_version: int) -> tuple[List[str], int]:
        """Fetch attachment keys with changed fulltext since given Zotero version."""
        if since_version <= 0 and not self.fulltext_bootstrap_scan:
            # First delta bootstrap can be extremely expensive for large libraries.
            return [], 0

        url = f"{self.local_api_base}/fulltext"
        try:
            response = requests.get(
                url,
                params={"since": max(0, int(since_version))},
                headers={"Zotero-Allowed-Request": "1"},
                timeout=self.local_api_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            keys = list(payload.keys()) if isinstance(payload, dict) else []
            version_header = response.headers.get("Last-Modified-Version")
            if version_header:
                version = int(version_header)
            elif isinstance(payload, dict) and payload:
                version_candidates = []
                for value in payload.values():
                    try:
                        version_candidates.append(int(value))
                    except (TypeError, ValueError):
                        continue
                version = max(version_candidates) if version_candidates else int(since_version)
            else:
                version = int(since_version)
            return keys, version
        except Exception as e:
            print(f"[WARN] Failed to fetch changed fulltext keys from Zotero API: {e}")
            return [], int(since_version)

    def _resolve_parent_item_keys(self, attachment_keys: List[str]) -> List[str]:
        """Resolve parent item keys for changed attachment keys."""
        if not attachment_keys:
            return []
        conn = self._get_db_connection()
        if not conn:
            return []
        try:
            placeholders = ",".join("?" for _ in attachment_keys)
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT parent.key AS parent_key
                FROM items attach
                JOIN itemAttachments ia ON ia.itemID = attach.itemID
                JOIN items parent ON parent.itemID = ia.parentItemID
                WHERE attach.key IN ({placeholders})
                """,
                tuple(attachment_keys),
            )
            parent_keys = [row["parent_key"] for row in cursor.fetchall() if row["parent_key"]]
            return list(dict.fromkeys(parent_keys))
        except Exception as e:
            print(f"[WARN] Failed to resolve parent item keys from attachment keys: {e}")
            resolved = []
            for key in attachment_keys:
                parent_key = self._resolve_parent_key_via_api(key)
                if parent_key:
                    resolved.append(parent_key)
            return list(dict.fromkeys(resolved))
        finally:
            conn.close()

    def _resolve_parent_keys_for_any_item_keys(self, item_keys: List[str]) -> List[str]:
        """Normalize changed Zotero keys to top-level parent item keys."""
        if not item_keys:
            return []
        conn = self._get_db_connection()
        if not conn:
            return item_keys
        try:
            placeholders = ",".join("?" for _ in item_keys)
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                  child.key AS child_key,
                  COALESCE(parent.key, child.key) AS effective_key
                FROM items child
                LEFT JOIN itemAttachments ia ON ia.itemID = child.itemID
                LEFT JOIN itemNotes n ON n.itemID = child.itemID
                LEFT JOIN itemAnnotations an ON an.itemID = child.itemID
                LEFT JOIN items parent
                  ON parent.itemID = COALESCE(ia.parentItemID, n.parentItemID, an.parentItemID)
                WHERE child.key IN ({placeholders})
                """,
                tuple(item_keys),
            )
            rows = cursor.fetchall()
            normalized = [row["effective_key"] for row in rows if row["effective_key"]]
            # Keys absent from the SQLite items table (e.g. deleted items whose
            # trash has been emptied) must be kept as-is so their chunks can
            # still be deleted from the index by zotero_key.
            found_child_keys = {row["child_key"] for row in rows if row["child_key"]}
            unresolved = [key for key in item_keys if key not in found_child_keys]
            return list(dict.fromkeys([*normalized, *unresolved]))
        except Exception:
            return item_keys
        finally:
            conn.close()

    def _resolve_parent_key_via_api(self, item_key: str) -> Optional[str]:
        """Best-effort parent-key resolution via local API."""
        url = f"{self.local_api_base}/items/{item_key}"
        try:
            response = requests.get(
                url,
                params={"format": "json"},
                headers={"Zotero-Allowed-Request": "1"},
                timeout=self.local_api_timeout,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            parent = data.get("parentItem")
            return parent if isinstance(parent, str) and parent else item_key
        except Exception:
            return None

    def enumerate_state(self) -> Dict[str, UnitState]:
        """Enumerate current Zotero units → {unit_id: UnitState}.

        One ``parent_meta`` unit per top-level item plus one unit per child
        note / attachment / annotation, each rolled up to the parent's
        ``zotero_key`` (the source-identity rule). Fingerprints:
        - parent_meta / note / annotation → item ``dateModified``
        - attachment → ``storageHash`` when present, else ``mtime:size`` of the
          resolved file (storageHash covers only ~43% of attachments, and
          storageModTime shares its gaps — see the index-ledger spec).

        Read-only; uses set-based queries so the whole library is enumerated in
        a handful of round-trips, not per-item.
        """
        if not self.validate_config():
            return {}
        conn = self._get_db_connection()
        if not conn:
            return {}

        units: Dict[str, UnitState] = {}
        try:
            cursor = conn.cursor()

            # parent_meta: top-level, non-deleted items
            cursor.execute(
                """
                SELECT i.key AS key, i.dateModified AS dm
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
                  AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
                """
            )
            for row in cursor.fetchall():
                key = row["key"]
                if not key:
                    continue
                unit_id = f"zotero:{key}:meta"
                units[unit_id] = UnitState(
                    unit_id, "zotero_key", key, "parent_meta",
                    f"mod:{row['dm'] or ''}",
                )

            # notes (child note's own dateModified)
            cursor.execute(
                """
                SELECT parent.key AS parent_key, child.key AS child_key,
                       child.dateModified AS dm
                FROM itemNotes n
                JOIN items child ON child.itemID = n.itemID
                JOIN items parent ON parent.itemID = n.parentItemID
                WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                """
            )
            for row in cursor.fetchall():
                pk, ck = row["parent_key"], row["child_key"]
                if not pk or not ck:
                    continue
                unit_id = f"zotero:{pk}:note:{ck}"
                units[unit_id] = UnitState(
                    unit_id, "zotero_key", pk, "note", f"mod:{row['dm'] or ''}",
                )

            # annotations (hang off attachments; attributed to the parent item)
            cursor.execute(
                """
                SELECT parent.key AS parent_key, child.key AS child_key,
                       child.dateModified AS dm
                FROM itemAnnotations an
                JOIN items child ON child.itemID = an.itemID
                JOIN itemAttachments att ON att.itemID = an.parentItemID
                JOIN items parent ON parent.itemID = att.parentItemID
                WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND att.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                """
            )
            for row in cursor.fetchall():
                pk, ck = row["parent_key"], row["child_key"]
                if not pk or not ck:
                    continue
                unit_id = f"zotero:{pk}:annotation:{ck}"
                units[unit_id] = UnitState(
                    unit_id, "zotero_key", pk, "annotation", f"mod:{row['dm'] or ''}",
                )

            # attachments (composite fingerprint: storageHash else file mtime:size)
            cursor.execute(
                """
                SELECT parent.key AS parent_key, child.key AS child_key,
                       ia.storageHash AS storage_hash, ia.path AS path
                FROM itemAttachments ia
                JOIN items child ON child.itemID = ia.itemID
                JOIN items parent ON parent.itemID = ia.parentItemID
                WHERE child.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND parent.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND ia.path IS NOT NULL
                """
            )
            for row in cursor.fetchall():
                pk, ck = row["parent_key"], row["child_key"]
                if not pk or not ck:
                    continue
                fingerprint = self._attachment_fingerprint(
                    ck, row["storage_hash"], row["path"]
                )
                if fingerprint is None:
                    # No content hash and no resolvable local file → not
                    # indexable; omit so it never appears as a phantom unit.
                    continue
                unit_id = f"zotero:{pk}:attachment:{ck}"
                units[unit_id] = UnitState(
                    unit_id, "zotero_key", pk, "attachment", fingerprint,
                )

            return units
        finally:
            conn.close()

    def _attachment_fingerprint(
        self, attachment_key: str, storage_hash: Optional[str], path: Optional[str]
    ) -> Optional[str]:
        """Composite attachment fingerprint; None when nothing identifies content."""
        if storage_hash:
            return f"hash:{storage_hash}"
        if not path:
            return None
        if path.startswith("storage:"):
            filename = path.split(":", 1)[1]
            file_path = self.storage_dir / attachment_key / filename
        else:
            file_path = Path(path).expanduser()
        try:
            stat = file_path.stat()
        except OSError:
            return None
        return f"mtime:{stat.st_mtime:.6f}-{stat.st_size}"

    def _process_item(
        self,
        conn: sqlite3.Connection,
        item_id: int,
        *,
        kinds: Optional[set[str]] = None,
        attachment_keys: Optional[set[str]] = None,
    ) -> Iterator[Document]:
        """Process a single Zotero item and yield documents."""
        # Get item metadata
        metadata_base = self._get_item_metadata(conn, item_id)

        # Process notes if enabled
        if self.zotero_config.get("include_notes", True) and (
            kinds is None or "note" in kinds
        ):
            yield from self._process_notes(conn, item_id, metadata_base)

        # Process attachments if enabled
        if self.zotero_config.get("extract_attachments", True) and (
            kinds is None or "attachment" in kinds
        ):
            yield from self._process_attachments(
                conn,
                item_id,
                metadata_base,
                attachment_keys=attachment_keys,
            )

        # Process annotations if enabled
        if self.zotero_config.get("include_annotations", True) and (
            kinds is None or "annotation" in kinds
        ):
            yield from self._process_annotations(conn, item_id, metadata_base)

    def _get_item_metadata(self, conn: sqlite3.Connection, item_id: int) -> Dict[str, Any]:
        """Get base metadata for an item."""
        cursor = conn.cursor()

        # Get item key and modification stamp (used as the content version
        # for version-keyed progress; a changed item is never skipped as
        # already-stored even if delta detection missed it)
        cursor.execute(
            """
            SELECT i.key, i.dateModified, it.typeName AS item_type
            FROM items i
            LEFT JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            WHERE i.itemID = ?
            """,
            (item_id,),
        )
        key_row = cursor.fetchone()
        zotero_key = key_row["key"] if key_row else str(item_id)
        content_version = (key_row["dateModified"] or "") if key_row else ""
        item_type = (key_row["item_type"] or "") if key_row else ""

        # Get field values
        cursor.execute(
            """
            SELECT f.fieldName, v.value
            FROM itemData id
            JOIN itemDataValues v ON id.valueID = v.valueID
            JOIN fields f ON id.fieldID = f.fieldID
            WHERE id.itemID = ?
            """,
            (item_id,),
        )
        fields = {row["fieldName"]: row["value"] for row in cursor.fetchall()}

        # Get creators
        cursor.execute(
            """
            SELECT c.firstName, c.lastName
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
            """,
            (item_id,),
        )
        creators = [f"{row['firstName']} {row['lastName']}".strip() for row in cursor.fetchall()]

        # Get tags
        cursor.execute(
            """
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
            """,
            (item_id,),
        )
        tags = [row["name"] for row in cursor.fetchall()]

        # Get collections
        cursor.execute(
            """
            SELECT c.collectionName
            FROM collectionItems ci
            JOIN collections c ON ci.collectionID = c.collectionID
            WHERE ci.itemID = ?
            """,
            (item_id,),
        )
        collections = [row["collectionName"] for row in cursor.fetchall()]

        return {
            "source_type": "zotero",
            "zotero_key": zotero_key,
            "zotero_id": item_id,
            "item_type": item_type,
            "content_version": content_version,
            "title": fields.get("title", "Untitled"),
            "authors": ", ".join(creators) if creators else "Unknown",
            "year": fields.get("date", "")[:4] if fields.get("date") else "",
            "tags": tags,
            "collections": collections,
            "backlink": f"zotero://select/items/{zotero_key}",
            **fields,
        }

    def _process_notes(
        self, conn: sqlite3.Connection, item_id: int, metadata_base: Dict[str, Any]
    ) -> Iterator[Document]:
        """Process notes for an item."""
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT n.note, n.itemID, i.key
            FROM itemNotes n
            JOIN items i ON i.itemID = n.itemID
            WHERE n.parentItemID = ?
              AND n.itemID NOT IN (SELECT itemID FROM deletedItems)
            """,
            (item_id,),
        )

        h = html2text.HTML2Text()
        h.ignore_links = False

        for idx, row in enumerate(cursor.fetchall()):
            note_html = row["note"]
            note_id = row["itemID"]
            note_key = row["key"]
            note_text = h.handle(note_html).strip()

            if note_text:
                metadata = metadata_base.copy()
                metadata.update(
                    {
                        "source_type": "zotero_note",
                        "note_id": note_id,
                        "note_key": note_key,
                        "chunk_index": idx,
                    }
                )

                yield Document(
                    content=note_text,
                    metadata=metadata,
                    doc_id=f"zotero-{item_id}-note-{note_id}",
                )

    def _collect_attachment_tasks(
        self,
        conn: sqlite3.Connection,
        item_id: int,
        *,
        attachment_keys: Optional[set[str]] = None,
    ) -> List[ExtractionTask]:
        """Collect all attachment extraction tasks for an item."""
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ia.path, ia.contentType, i.itemID, i.key
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.parentItemID = ?
              AND ia.path IS NOT NULL
              AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)
            """,
            (item_id,),
        )

        tasks = []
        for row in cursor.fetchall():
            path_str = row["path"]
            if not path_str or not path_str.startswith("storage:"):
                continue

            filename = path_str.split(":", 1)[1]
            attachment_key = row["key"]
            if attachment_keys is not None and attachment_key not in attachment_keys:
                continue
            attachment_id = row["itemID"]
            file_path = self.storage_dir / attachment_key / filename

            if not file_path.exists():
                continue

            file_size_mb = file_path.stat().st_size / (1024 * 1024)

            tasks.append(
                ExtractionTask(
                    file_path=file_path,
                    attachment_id=attachment_id,
                    attachment_key=attachment_key,
                    filename=filename,
                    content_type=row["contentType"],
                    file_size_mb=file_size_mb,
                    zotero_item_id=item_id,
                )
            )

        return tasks

    def _extract_single_attachment(self, task: ExtractionTask) -> ExtractionResult:
        """
        Extract text from a single attachment. Thread-safe and stateless.

        This function is designed to be called from multiple threads concurrently.
        It calls the subprocess-based extract_text function which handles timeouts.
        """
        start_time = time.time()
        try:
            text = extract_text(task.file_path)
            elapsed = time.time() - start_time

            if text and text.strip():
                return ExtractionResult(task=task, text=text, error=None, elapsed_seconds=elapsed)
            else:
                return ExtractionResult(
                    task=task, text=None, error="empty: no extractable text", elapsed_seconds=elapsed
                )
        except Exception as e:
            elapsed = time.time() - start_time
            return ExtractionResult(task=task, text=None, error=str(e), elapsed_seconds=elapsed)

    def _extract_attachment_with_router(self, task: ExtractionTask) -> ExtractionResult:
        """Extract attachment text through the v0.6 quality-gated seam."""
        extraction_input = ExtractionInput(
            file_path=task.file_path,
            attachment_key=task.attachment_key,
            content_type=task.content_type,
            source_metadata={
                "attachment_id": task.attachment_id,
                "attachment_key": task.attachment_key,
                "file_name": task.filename,
            },
            file_size_mb=task.file_size_mb,
            fulltext_fetcher=(
                self._fetch_local_api_fulltext
                if self.prefer_local_api_fulltext
                else None
            ),
            partial_fulltext_checker=lambda text: self._is_likely_partial_fulltext(
                task, text
            ),
        )
        output = self.extraction_router.extract(extraction_input)
        result = ExtractionResult(
            task=task,
            text=output.text if output.ok else None,
            error=None if output.ok else "; ".join(output.errors or output.warnings),
            elapsed_seconds=output.elapsed_seconds,
        )
        result.output = output  # type: ignore[attr-defined]
        return result

    def _fetch_local_api_fulltext(self, attachment_key: str) -> Optional[str]:
        """Fetch indexed fulltext for an attachment from Zotero local API."""
        url = f"{self.local_api_base}/items/{attachment_key}/fulltext"
        try:
            response = requests.get(
                url,
                headers={"Zotero-Allowed-Request": "1"},
                timeout=self.local_api_timeout,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            if not isinstance(payload, dict):
                return None
            content = payload.get("content")
            if not isinstance(content, str):
                return None
            content = content.strip()
            if len(content) < self.fulltext_min_chars:
                return None
            return content
        except Exception:
            return None

    def _is_likely_partial_fulltext(self, task: ExtractionTask, text: str) -> bool:
        """Heuristic for partial Zotero fulltext on very large PDFs."""
        if not text:
            return True
        if task.file_size_mb < self.fulltext_large_pdf_mb:
            return False
        return len(text) < self.fulltext_large_pdf_min_chars

    def _process_attachments(
        self,
        conn: sqlite3.Connection,
        item_id: int,
        metadata_base: Dict[str, Any],
        *,
        attachment_keys: Optional[set[str]] = None,
    ) -> Iterator[Document]:
        """Process attachments for an item sequentially.

        Parallelization happens at the item level (multiple items processed
        concurrently), so attachment extraction within an item is sequential.
        """
        # Step 1: Collect all tasks
        tasks = self._collect_attachment_tasks(
            conn,
            item_id,
            attachment_keys=attachment_keys,
        )

        if not tasks:
            return

        # Step 2: Sort tasks by attachment_key for deterministic ordering
        tasks.sort(key=lambda t: t.attachment_key)

        # Step 3: Extract each attachment sequentially
        processed_count = 0
        error_count = 0

        for task in tasks:
            result = self._extract_attachment_with_router(task)
            extraction_output = getattr(result, "output", None)
            if extraction_output is not None:
                event_payload = {
                    "item_id": item_id,
                    "zotero_key": metadata_base.get("zotero_key"),
                    "attachment_id": task.attachment_id,
                    "attachment_key": task.attachment_key,
                    "file_name": task.filename,
                    "file_path": str(task.file_path),
                    "file_size_mb": task.file_size_mb,
                    "source_type": "zotero_fulltext",
                    "extractor": extraction_output.extractor,
                    "extractor_version": extraction_output.extractor_version,
                    "extract_quality": extraction_output.provenance().get("extract_quality", ""),
                    "extract_action": extraction_output.action,
                    "extract_route": extraction_output.route,
                    "warnings": extraction_output.warnings,
                    "errors": extraction_output.errors,
                    "text_length": len(extraction_output.text or ""),
                }
                if extraction_output.warnings:
                    self._emit_progress("extraction_warning", **event_payload)
                if extraction_output.action == "escalate":
                    self._emit_progress("extraction_escalate", **event_payload)
                elif not extraction_output.ok:
                    self._emit_progress("extraction_reject", **event_payload)

            if result.text:
                metadata = metadata_base.copy()
                provenance = (
                    extraction_output.provenance()
                    if extraction_output is not None
                    else {}
                )
                metadata.update(
                    {
                        "source_type": "zotero_fulltext",
                        "attachment_id": task.attachment_id,
                        "attachment_key": task.attachment_key,
                        "file_name": task.filename,
                        "file_path": str(task.file_path),
                        "content_type": task.content_type,
                        "text_source": provenance.get("extractor", "unknown"),
                        **provenance,
                    }
                )

                yield Document(
                    content=result.text,
                    metadata=metadata,
                    doc_id=f"zotero-{item_id}-attachment-{task.attachment_id}",
                )
                processed_count += 1
            else:
                error_count += 1
                self._log_problematic_pdf(task.file_path, task.filename, result.error or "unknown")

    def _log_problematic_pdf(self, file_path: Path, filename: str, reason: str):
        """Log a problematic PDF for later manual processing."""
        try:
            from pathlib import Path as PathlibPath
            log_file = PathlibPath(self.data_dir).parent / "problematic_pdfs.log"
            
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{filename} | {file_size_mb:.1f}MB | {reason} | {file_path}\n")
        except Exception as e:
            print(f"    [Warning: Could not log problematic PDF: {e}]")

    def _process_annotations(
        self, conn: sqlite3.Connection, item_id: int, metadata_base: Dict[str, Any]
    ) -> Iterator[Document]:
        """Process PDF annotations for a top-level item.

        Annotations hang off the item's attachments, so the query goes
        through itemAttachments: this attributes them to the parent item's
        zotero_key (the source identity rule), not the attachment's.
        """
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT an.text, an.comment, an.sortIndex, an.pageLabel, i.itemID, i.key
            FROM itemAnnotations an
            JOIN items i ON an.itemID = i.itemID
            JOIN itemAttachments att ON att.itemID = an.parentItemID
            WHERE att.parentItemID = ?
              AND an.itemID NOT IN (SELECT itemID FROM deletedItems)
            ORDER BY an.sortIndex
            """,
            (item_id,),
        )

        for idx, row in enumerate(cursor.fetchall()):
            annotation_text = row["text"] or ""
            annotation_comment = row["comment"] or ""
            annotation_id = row["itemID"]
            annotation_key = row["key"]
            page = row["pageLabel"] or ""

            # Combine highlighted text and comment
            combined_text = f"{annotation_text}\n\n{annotation_comment}".strip()

            if combined_text:
                metadata = metadata_base.copy()
                metadata.update(
                    {
                        "source_type": "zotero_annotation",
                        "annotation_id": annotation_id,
                        "annotation_key": annotation_key,
                        "has_comment": bool(annotation_comment.strip()),
                        "page": page,
                        "chunk_index": idx,
                    }
                )

                yield Document(
                    content=combined_text,
                    metadata=metadata,
                    doc_id=f"zotero-{item_id}-annotation-{annotation_id}",
                )
