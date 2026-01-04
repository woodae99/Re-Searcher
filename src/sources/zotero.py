"""Zotero data source for extracting items, notes, and attachments."""

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import html2text

from ..extract_text import extract_text
from .base import DataSource, Document


class ZoteroSource(DataSource):
    """Data source for Zotero library."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.zotero_config = config.get("zotero", {})
        self.data_dir = None
        self.db_path = None
        self.storage_dir = None

        if self.is_enabled():
            self.data_dir = Path(self.zotero_config.get("data_directory", "")).expanduser()
            self.db_path = self.data_dir / "zotero.sqlite"
            self.storage_dir = self.data_dir / "storage"

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
            print(f"📚 Found {len(items)} items in Zotero library")

            for item_row in items:
                item_id = item_row["itemID"]
                yield from self._process_item(conn, item_id)

        finally:
            conn.close()

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

    def _process_attachments(
        self, conn: sqlite3.Connection, item_id: int, metadata_base: Dict[str, Any]
    ) -> Iterator[Document]:
        """Process attachments for an item."""
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

            try:
                print(f"  📎 Extracting: {file_path.name}")
                full_text = extract_text(file_path)

                if full_text.strip():
                    metadata = metadata_base.copy()
                    metadata.update(
                        {
                            "source_type": "zotero_fulltext",
                            "attachment_id": attachment_id,
                            "attachment_key": attachment_key,
                            "file_name": filename,
                            "file_path": str(file_path),
                            "content_type": row["contentType"],
                        }
                    )

                    yield Document(
                        content=full_text,
                        metadata=metadata,
                        doc_id=f"zotero-{item_id}-attachment-{attachment_id}",
                    )

            except Exception as e:
                print(f"  ⚠️  Error extracting {file_path.name}: {e}")

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
