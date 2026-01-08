"""Progress tracking for resumable batch indexing."""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class DocumentStatus(Enum):
    """Status of a document in the indexing pipeline."""

    PENDING = "pending"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    STORED = "stored"
    ERROR = "error"


class IndexingProgress:
    """Tracks progress of batch indexing with checkpoint support."""

    def __init__(self, progress_file: Path):
        """
        Initialize progress tracker.

        Args:
            progress_file: Path to JSON progress file
        """
        self.progress_file = Path(progress_file)
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing progress or start fresh
        self.data = self._load_progress()

    def _load_progress(self) -> Dict[str, Any]:
        """Load progress from JSON file or create new structure."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return self._create_new_progress()
        return self._create_new_progress()

    def _create_new_progress(self) -> Dict[str, Any]:
        """Create a new progress structure."""
        return {
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "documents": {},  # doc_id -> {status, chunk_count, error_msg}
            "stats": {
                "total_documents": 0,
                "documents_chunked": 0,
                "documents_embedded": 0,
                "documents_stored": 0,
                "total_chunks": 0,
                "chunks_stored": 0,
                "errors": 0,
            },
        }

    def set_total_documents(self, total: int):
        """Set total document count."""
        self.data["stats"]["total_documents"] = total
        self._save()

    def set_document_status(
        self,
        doc_id: str,
        status: DocumentStatus,
        chunk_count: Optional[int] = None,
        error_msg: Optional[str] = None,
    ):
        """
        Update document status.

        Args:
            doc_id: Document identifier
            status: New status
            chunk_count: Number of chunks (when status is CHUNKED)
            error_msg: Error message (when status is ERROR)
        """
        # Get old status for stats update
        old_status = None
        if doc_id in self.data["documents"]:
            old_status = self.data["documents"][doc_id].get("status")

        # Only update if status is different to avoid duplicate stats updates
        if old_status != status.value:
            if doc_id not in self.data["documents"]:
                self.data["documents"][doc_id] = {}

            self.data["documents"][doc_id]["status"] = status.value
            self.data["documents"][doc_id]["updated_at"] = datetime.now().isoformat()

            # Update stats
            self._update_stats(old_status, status)

        if chunk_count is not None:
            self.data["documents"][doc_id]["chunk_count"] = chunk_count

        if error_msg:
            self.data["documents"][doc_id]["error_msg"] = error_msg

        self._save()

    def _update_stats(self, old_status: Optional[str], new_status: DocumentStatus):
        """Update aggregate statistics."""
        status_value = new_status.value

        # Decrement old status counter if changing from existing status
        if old_status == "chunked":
            self.data["stats"]["documents_chunked"] -= 1
        elif old_status == "embedded":
            self.data["stats"]["documents_embedded"] -= 1
        elif old_status == "stored":
            self.data["stats"]["documents_stored"] -= 1

        # Increment new status counter
        if status_value == "chunked":
            self.data["stats"]["documents_chunked"] += 1
        elif status_value == "embedded":
            self.data["stats"]["documents_embedded"] += 1
        elif status_value == "stored":
            self.data["stats"]["documents_stored"] += 1
        elif status_value == "error":
            self.data["stats"]["errors"] += 1

    def get_documents_by_status(self, status: DocumentStatus) -> Set[str]:
        """Get all document IDs with given status."""
        return {
            doc_id
            for doc_id, doc_info in self.data["documents"].items()
            if doc_info.get("status") == status.value
        }

    def get_status(self, doc_id: str) -> Optional[DocumentStatus]:
        """Get current status of a document."""
        if doc_id in self.data["documents"]:
            status_str = self.data["documents"][doc_id].get("status")
            if status_str:
                return DocumentStatus(status_str)
        return None

    def has_completed_status(self, doc_id: str) -> bool:
        """Check if document is fully indexed (status == STORED)."""
        status = self.get_status(doc_id)
        return status == DocumentStatus.STORED

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        return self.data["stats"].copy()

    def print_progress(self):
        """Print progress summary to console."""
        stats = self.get_stats()
        total = stats["total_documents"]
        stored = stats["documents_stored"]

        if total > 0:
            percent = (stored / total) * 100
            print(
                f"[PROGRESS] {stored}/{total} documents indexed ({percent:.1f}%) | "
                f"Chunks: {stats['chunks_stored']}/{stats['total_chunks']} | "
                f"Errors: {stats['errors']}"
            )
        else:
            print("[PROGRESS] No documents yet")

    def _save(self):
        """Save progress to JSON file."""
        self.data["updated_at"] = datetime.now().isoformat()
        with open(self.progress_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def clear(self):
        """Clear all progress (for testing)."""
        self.data = self._create_new_progress()
        self._save()
