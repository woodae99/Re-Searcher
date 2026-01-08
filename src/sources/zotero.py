"""Zotero data source for extracting items, notes, and attachments."""

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import html2text

from ..extract_text import extract_text
from .base import DataSource, Document, ProgressCallback


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

    def fetch_documents(self) -> Iterator[Document]:
        """
        Fetch all documents from Zotero library.

        Yields documents for:
        - Item notes (as separate documents)
        - Item attachments (full text extraction from PDFs, etc.)
        - Item annotations (if enabled)
        """
        if not self.validate_config():
            return

        conn = self._get_db_connection()
        if not conn:
            return

        try:
            items = self._get_all_items(conn)
            total_items = len(items)
            print(f"📚 Found {total_items} items in Zotero library")

            # Emit source initialization
            self._emit_progress("source_init", total=total_items)

            for idx, item_row in enumerate(items):
                item_id = item_row["itemID"]
                self._emit_progress("item_start", item_id=item_id, index=idx, total=total_items)

                try:
                    docs_yielded = 0
                    for doc in self._process_item(conn, item_id):
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
            self._emit_progress("source_complete")

    def _get_db_connection(self) -> Optional[sqlite3.Connection]:
        """Establish read-only connection to Zotero SQLite database."""
        try:
            uri = f"{self.db_path.as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            print(f"❌ Database connection error: {e}")
            return None

    def _get_all_items(self, conn: sqlite3.Connection) -> List[sqlite3.Row]:
        """Get all non-deleted items."""
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT itemID, itemTypeID, dateAdded, dateModified, key
            FROM items
            WHERE itemID NOT IN (SELECT itemID FROM deletedItems)
            """
        )
        return cursor.fetchall()

    def _process_item(self, conn: sqlite3.Connection, item_id: int) -> Iterator[Document]:
        """Process a single Zotero item and yield documents."""
        # Get item metadata
        metadata_base = self._get_item_metadata(conn, item_id)

        # Process notes if enabled
        if self.zotero_config.get("include_notes", True):
            yield from self._process_notes(conn, item_id, metadata_base)

        # Process attachments if enabled
        if self.zotero_config.get("extract_attachments", True):
            yield from self._process_attachments(conn, item_id, metadata_base)

        # Process annotations if enabled
        if self.zotero_config.get("include_annotations", True):
            yield from self._process_annotations(conn, item_id, metadata_base)

    def _get_item_metadata(self, conn: sqlite3.Connection, item_id: int) -> Dict[str, Any]:
        """Get base metadata for an item."""
        cursor = conn.cursor()

        # Get item key
        cursor.execute("SELECT key FROM items WHERE itemID = ?", (item_id,))
        key_row = cursor.fetchone()
        zotero_key = key_row["key"] if key_row else str(item_id)

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
            "SELECT note, itemID FROM itemNotes WHERE parentItemID = ?", (item_id,)
        )

        h = html2text.HTML2Text()
        h.ignore_links = False

        for idx, row in enumerate(cursor.fetchall()):
            note_html = row["note"]
            note_id = row["itemID"]
            note_text = h.handle(note_html).strip()

            if note_text:
                metadata = metadata_base.copy()
                metadata.update(
                    {
                        "source_type": "zotero_note",
                        "note_id": note_id,
                        "chunk_index": idx,
                    }
                )

                yield Document(
                    content=note_text,
                    metadata=metadata,
                    doc_id=f"zotero-{item_id}-note-{note_id}",
                )

    def _collect_attachment_tasks(
        self, conn: sqlite3.Connection, item_id: int
    ) -> List[ExtractionTask]:
        """Collect all attachment extraction tasks for an item."""
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ia.path, ia.contentType, i.itemID, i.key
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.parentItemID = ? AND ia.path IS NOT NULL
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

    def _process_attachments(
        self, conn: sqlite3.Connection, item_id: int, metadata_base: Dict[str, Any]
    ) -> Iterator[Document]:
        """Process attachments for an item using parallel extraction.

        Uses ThreadPoolExecutor for concurrent extraction while maintaining
        deterministic output ordering for reproducibility.
        """
        # Step 1: Collect all tasks
        tasks = self._collect_attachment_tasks(conn, item_id)

        if not tasks:
            return

        # Step 2: Sort tasks by attachment_key for deterministic ordering
        # This ensures "same run, same IDs, same ordering"
        tasks.sort(key=lambda t: t.attachment_key)

        # Step 3: Assign indices after sorting
        for i, task in enumerate(tasks):
            task.index = i

        total_tasks = len(tasks)

        # Step 4: Execute extraction (parallel or sequential based on config)
        if self.parallel_enabled and total_tasks > 1:
            results = self._extract_parallel(tasks, item_id)
        else:
            results = self._extract_sequential(tasks, item_id)

        # Step 5: Yield documents in deterministic order (by index)
        processed_count = 0
        error_count = 0

        for i in range(total_tasks):
            result = results.get(i)
            if not result:
                continue

            task = result.task

            if result.text:
                metadata = metadata_base.copy()
                metadata.update(
                    {
                        "source_type": "zotero_fulltext",
                        "attachment_id": task.attachment_id,
                        "attachment_key": task.attachment_key,
                        "file_name": task.filename,
                        "file_path": str(task.file_path),
                        "content_type": task.content_type,
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

        # Print summary for this item
        if total_tasks > 0:
            success_pct = (processed_count / total_tasks * 100) if total_tasks > 0 else 0
            print(f"      Item summary: {processed_count}/{total_tasks} extracted ({success_pct:.0f}%)")

    def _extract_parallel(
        self, tasks: List[ExtractionTask], item_id: int
    ) -> Dict[int, ExtractionResult]:
        """Execute extraction tasks in parallel using ThreadPoolExecutor."""
        results: Dict[int, ExtractionResult] = {}
        worker_count = self._get_worker_count()

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(self._extract_single_attachment, task): task for task in tasks
            }

            # Process results as they complete
            for future in as_completed(future_to_task):
                result = future.result()
                task = result.task

                # Store result by index for deterministic ordering
                results[task.index] = result

                # Emit progress event
                if result.text:
                    print(f"      [Worker] Extracted: {task.filename} ({len(result.text)} chars, {result.elapsed_seconds:.1f}s)")
                    self._emit_progress(
                        "attachment_complete",
                        item_id=item_id,
                        attachment_id=task.attachment_id,
                        file_name=task.filename,
                        chars_extracted=len(result.text),
                        status="success",
                    )
                else:
                    print(f"      [Worker] Failed: {task.filename} ({result.error})")
                    self._emit_progress(
                        "attachment_error",
                        item_id=item_id,
                        attachment_id=task.attachment_id,
                        file_name=task.filename,
                        error=result.error,
                    )

        return results

    def _extract_sequential(
        self, tasks: List[ExtractionTask], item_id: int
    ) -> Dict[int, ExtractionResult]:
        """Execute extraction tasks sequentially (fallback for single task or disabled parallel)."""
        results: Dict[int, ExtractionResult] = {}

        for task in tasks:
            print(f"      Extracting: {task.filename} ({task.file_size_mb:.1f}MB)...", end="", flush=True)

            # Emit start event
            self._emit_progress(
                "attachment_start",
                item_id=item_id,
                attachment_id=task.attachment_id,
                file_name=task.filename,
                file_size_mb=task.file_size_mb,
                index=task.index,
                total=len(tasks),
            )

            result = self._extract_single_attachment(task)
            results[task.index] = result

            # Emit completion event
            if result.text:
                print(f" OK ({len(result.text)} chars)")
                self._emit_progress(
                    "attachment_complete",
                    item_id=item_id,
                    attachment_id=task.attachment_id,
                    file_name=task.filename,
                    chars_extracted=len(result.text),
                    status="success",
                )
            else:
                print(f" [ERROR: {result.error}]")
                self._emit_progress(
                    "attachment_error",
                    item_id=item_id,
                    attachment_id=task.attachment_id,
                    file_name=task.filename,
                    error=result.error,
                )

        return results

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
        """Process PDF annotations for an item."""
        cursor = conn.cursor()

        # Get annotations from itemAnnotations table
        cursor.execute(
            """
            SELECT ia.text, ia.comment, ia.sortIndex, ia.pageLabel, i.itemID
            FROM itemAnnotations ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.parentItemID = ?
            ORDER BY ia.sortIndex
            """,
            (item_id,),
        )

        for idx, row in enumerate(cursor.fetchall()):
            annotation_text = row["text"] or ""
            annotation_comment = row["comment"] or ""
            annotation_id = row["itemID"]
            page = row["pageLabel"] or ""

            # Combine highlighted text and comment
            combined_text = f"{annotation_text}\n\n{annotation_comment}".strip()

            if combined_text:
                metadata = metadata_base.copy()
                metadata.update(
                    {
                        "source_type": "zotero_annotation",
                        "annotation_id": annotation_id,
                        "page": page,
                        "chunk_index": idx,
                    }
                )

                yield Document(
                    content=combined_text,
                    metadata=metadata,
                    doc_id=f"zotero-{item_id}-annotation-{annotation_id}",
                )
