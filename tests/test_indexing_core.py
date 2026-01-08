"""Unit tests for resumable batch-concurrent indexing - Core functionality only."""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.indexing import DocumentStatus, IndexingProgress


class TestIndexingProgress:
    """Tests for IndexingProgress tracking."""

    def test_create_new_progress(self):
        """Test creating a new progress file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            # File is created on first status change or save
            progress.set_total_documents(1)
            assert progress_file.exists()

    def test_load_existing_progress(self):
        """Test loading existing progress file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"

            # Create initial progress
            progress1 = IndexingProgress(progress_file)
            progress1.set_total_documents(10)
            progress1.set_document_status("doc1", DocumentStatus.CHUNKED)

            # Load it again
            progress2 = IndexingProgress(progress_file)
            assert progress2.data["stats"]["total_documents"] == 10
            assert progress2.get_status("doc1") == DocumentStatus.CHUNKED

    def test_document_status_transitions(self):
        """Test transitioning document through statuses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)
            progress.set_total_documents(1)

            # Transition through statuses
            progress.set_document_status("doc1", DocumentStatus.PENDING)
            assert progress.get_status("doc1") == DocumentStatus.PENDING

            progress.set_document_status("doc1", DocumentStatus.CHUNKED, chunk_count=5)
            assert progress.get_status("doc1") == DocumentStatus.CHUNKED
            assert progress.data["documents"]["doc1"]["chunk_count"] == 5

            progress.set_document_status("doc1", DocumentStatus.EMBEDDED)
            assert progress.get_status("doc1") == DocumentStatus.EMBEDDED

            progress.set_document_status("doc1", DocumentStatus.STORED)
            assert progress.get_status("doc1") == DocumentStatus.STORED

    def test_has_completed_status_only_when_stored(self):
        """Test completion status only marks stored documents as complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            progress.set_document_status("doc1", DocumentStatus.CHUNKED)
            assert progress.has_completed_status("doc1") is False

            progress.set_document_status("doc1", DocumentStatus.EMBEDDED)
            assert progress.has_completed_status("doc1") is False

            progress.set_document_status("doc1", DocumentStatus.STORED)
            assert progress.has_completed_status("doc1") is True

    def test_stats_updates(self):
        """Test that stats are updated correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)
            progress.set_total_documents(3)

            # Process documents
            progress.set_document_status("doc1", DocumentStatus.CHUNKED)
            progress.set_document_status("doc2", DocumentStatus.CHUNKED)

            stats = progress.get_stats()
            assert stats["documents_chunked"] == 2

            # Transition doc1 to embedded
            progress.set_document_status("doc1", DocumentStatus.EMBEDDED)

            stats = progress.get_stats()
            assert stats["documents_chunked"] == 1  # Only doc2 is still chunked
            assert stats["documents_embedded"] == 1  # doc1 is now embedded

    def test_get_documents_by_status(self):
        """Test retrieving documents by status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            for i in range(5):
                doc_id = f"doc{i}"
                if i < 2:
                    progress.set_document_status(doc_id, DocumentStatus.CHUNKED)
                elif i < 4:
                    progress.set_document_status(doc_id, DocumentStatus.EMBEDDED)
                else:
                    progress.set_document_status(doc_id, DocumentStatus.STORED)

            chunked = progress.get_documents_by_status(DocumentStatus.CHUNKED)
            assert len(chunked) == 2
            assert "doc0" in chunked and "doc1" in chunked

            embedded = progress.get_documents_by_status(DocumentStatus.EMBEDDED)
            assert len(embedded) == 2

            stored = progress.get_documents_by_status(DocumentStatus.STORED)
            assert len(stored) == 1

    def test_error_handling(self):
        """Test error status tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            progress.set_document_status(
                "doc1",
                DocumentStatus.ERROR,
                error_msg="Test error message",
            )

            assert progress.get_status("doc1") == DocumentStatus.ERROR
            assert (
                progress.data["documents"]["doc1"]["error_msg"]
                == "Test error message"
            )
            assert progress.get_stats()["errors"] == 1

    def test_progress_file_integrity(self):
        """Test that progress file stays valid and consistent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "indexing_progress.json"
            progress = IndexingProgress(progress_file)

            # Create some activity
            progress.set_total_documents(10)
            for i in range(5):
                progress.set_document_status(f"doc{i}", DocumentStatus.CHUNKED)
                progress.set_document_status(f"doc{i}", DocumentStatus.STORED)

            # Verify file is valid JSON
            with open(progress_file) as f:
                data = json.load(f)

            assert data["stats"]["documents_stored"] == 5
            assert len(data["documents"]) == 5

            # Reload and verify consistency
            progress2 = IndexingProgress(progress_file)
            assert progress2.get_stats()["documents_stored"] == 5

    def test_clear_progress(self):
        """Test clearing progress for re-indexing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            # Add some progress
            progress.set_total_documents(5)
            progress.set_document_status("doc1", DocumentStatus.STORED)

            # Clear progress
            progress.clear()

            stats = progress.get_stats()
            assert stats["total_documents"] == 0
            assert stats["documents_stored"] == 0
            assert len(progress.data["documents"]) == 0

    def test_concurrent_access(self):
        """Test that progress file handles sequential access correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"

            # Create multiple progress objects and write sequentially
            progress1 = IndexingProgress(progress_file)
            progress1.set_total_documents(10)

            # Load fresh instance after first write
            progress2 = IndexingProgress(progress_file)
            progress2.set_document_status("doc1", DocumentStatus.CHUNKED)

            # Reload and verify both writes are present
            progress3 = IndexingProgress(progress_file)
            assert progress3.data["stats"]["total_documents"] == 10
            assert progress3.get_status("doc1") == DocumentStatus.CHUNKED

    def test_batch_tracking(self):
        """Test tracking documents through multiple batches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            # Simulate processing in batches
            doc_ids = [f"doc{i:03d}" for i in range(10)]
            progress.set_total_documents(len(doc_ids))

            # Batch 1: chunk first 5
            for doc_id in doc_ids[:5]:
                progress.set_document_status(doc_id, DocumentStatus.CHUNKED, chunk_count=3)

            stats = progress.get_stats()
            assert stats["documents_chunked"] == 5

            # Batch 2: embed first 5, chunk next 5
            for doc_id in doc_ids[:5]:
                progress.set_document_status(doc_id, DocumentStatus.EMBEDDED)
            for doc_id in doc_ids[5:]:
                progress.set_document_status(doc_id, DocumentStatus.CHUNKED, chunk_count=3)

            stats = progress.get_stats()
            assert stats["documents_embedded"] == 5
            assert stats["documents_chunked"] == 5

            # Batch 3: store first 5, embed last 5
            for doc_id in doc_ids[:5]:
                progress.set_document_status(doc_id, DocumentStatus.STORED)
            for doc_id in doc_ids[5:]:
                progress.set_document_status(doc_id, DocumentStatus.EMBEDDED)

            stats = progress.get_stats()
            assert stats["documents_stored"] == 5
            assert stats["documents_embedded"] == 5
            assert stats["documents_chunked"] == 0

    def test_no_status_duplicates(self):
        """Test that setting the same status twice doesn't double-count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "progress.json"
            progress = IndexingProgress(progress_file)

            # Set status twice
            progress.set_document_status("doc1", DocumentStatus.CHUNKED)
            progress.set_document_status("doc1", DocumentStatus.CHUNKED)

            stats = progress.get_stats()
            # Should only count once
            assert stats["documents_chunked"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
