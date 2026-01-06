"""Unit tests for resumable batch-concurrent indexing."""

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
from src.pipeline import ResearchRAGPipeline
from fixtures import create_test_documents, get_test_config_path


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


class TestResumableIndexing:
    """Tests for resumable batch indexing."""

    @pytest.fixture
    def temp_output_dir(self):
        """Create temporary output directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def test_config_path(self):
        """Get path to test config."""
        return get_test_config_path()

    def test_pipeline_initialization(self, test_config_path, temp_output_dir):
        """Test pipeline initializes with progress tracking."""
        # Modify config to use temp output
        with patch(
            "src.pipeline.ResearchRAGPipeline._load_config"
        ) as mock_load, patch(
            "src.embedding.lmstudio.LMStudioEmbedding"
        ) as mock_embedder_class, patch(
            "src.storage.chroma.ChromaVectorStore"
        ) as mock_store_class:
            config = {
                "output_folder": str(temp_output_dir),
                "zotero": {"enabled": False},
                "obsidian": {"enabled": False},
                "embedding": {
                    "model": "test",
                    "api_endpoint": "http://localhost:1234/v1",
                },
                "storage": {"endpoint": "http://localhost:8000"},
                "chunking": {"chunk_size": 512},
                "indexing": {"batch_size": 5},
            }
            mock_load.return_value = config
            mock_embedder_class.return_value = MagicMock()
            mock_store_class.return_value = MagicMock()

            pipeline = ResearchRAGPipeline(test_config_path)

            assert pipeline.progress is not None
            assert pipeline.batch_size == 5
            assert (temp_output_dir / "indexing_progress.json").exists()

    def test_batch_processing_with_mock(self, test_config_path, temp_output_dir):
        """Test batch processing with mocked components."""
        with patch(
            "src.pipeline.ResearchRAGPipeline._load_config"
        ) as mock_load, patch(
            "src.embedding.lmstudio.LMStudioEmbedding"
        ) as mock_embedder_class, patch(
            "src.storage.chroma.ChromaVectorStore"
        ) as mock_store_class, patch(
            "src.pipeline.ResearchRAGPipeline._initialize_sources"
        ) as mock_sources, patch(
            "src.pipeline.ResearchRAGPipeline._needs_reindex"
        ) as mock_needs_reindex, patch(
            "src.pipeline.ResearchRAGPipeline._fetch_all_documents"
        ) as mock_fetch, patch(
            "src.pipeline.ResearchRAGPipeline._save_source_hash"
        ) as mock_save_hash:

            config = {
                "output_folder": str(temp_output_dir),
                "zotero": {"enabled": False},
                "obsidian": {"enabled": False},
                "embedding": {
                    "model": "test",
                    "api_endpoint": "http://localhost:1234/v1",
                },
                "storage": {"endpoint": "http://localhost:8000"},
                "chunking": {"chunk_size": 512},
                "indexing": {"batch_size": 3},
            }
            mock_load.return_value = config
            mock_embedder_class.return_value = MagicMock()
            mock_store_class.return_value = MagicMock()
            mock_sources.return_value = []
            mock_needs_reindex.return_value = True
            mock_fetch.return_value = create_test_documents(6)

            pipeline = ResearchRAGPipeline(test_config_path)

            # Mock the processing methods
            pipeline._chunk_batch = MagicMock(
                return_value=(["chunk1", "chunk2"], [{"doc_id": "test"}], ["id1", "id2"])
            )
            pipeline._generate_embeddings = MagicMock(
                return_value=[[0.1] * 1024, [0.2] * 1024]
            )
            pipeline._store_batch = MagicMock()
            pipeline.vector_store = MagicMock()
            pipeline.vector_store.get_collection_stats = MagicMock(
                return_value={
                    "collection_name": "test",
                    "document_count": 6,
                    "endpoint": "http://localhost:8000",
                }
            )

            # Run pipeline
            pipeline.run(force_reindex=True)

            # Verify batches were processed
            assert pipeline._chunk_batch.call_count == 2  # 2 batches of 3
            assert pipeline._generate_embeddings.call_count == 2
            assert pipeline._store_batch.call_count == 2

    def test_resume_from_checkpoint(self, test_config_path, temp_output_dir):
        """Test resuming indexing from checkpoint."""
        with patch(
            "src.pipeline.ResearchRAGPipeline._load_config"
        ) as mock_load, patch(
            "src.pipeline.ResearchRAGPipeline._initialize_sources"
        ) as mock_sources, patch(
            "src.pipeline.ResearchRAGPipeline._needs_reindex"
        ) as mock_needs_reindex, patch(
            "src.pipeline.ResearchRAGPipeline._fetch_all_documents"
        ) as mock_fetch, patch(
            "src.pipeline.ResearchRAGPipeline._save_source_hash"
        ) as mock_save_hash:

            config = {
                "output_folder": str(temp_output_dir),
                "zotero": {"enabled": False},
                "obsidian": {"enabled": False},
                "embedding": {
                    "model": "test",
                    "api_endpoint": "http://localhost:1234/v1",
                },
                "storage": {"endpoint": "http://localhost:8000"},
                "chunking": {"chunk_size": 512},
                "indexing": {"batch_size": 2},
            }
            mock_load.return_value = config
            mock_sources.return_value = []
            mock_needs_reindex.return_value = True
            documents = create_test_documents(4)
            mock_fetch.return_value = documents

            pipeline = ResearchRAGPipeline(test_config_path)

            # Mock processing methods
            pipeline._chunk_batch = MagicMock(
                return_value=(["chunk1"], [{"doc_id": "test"}], ["id1"])
            )
            pipeline._generate_embeddings = MagicMock(return_value=[[0.1] * 1024])
            pipeline._store_batch = MagicMock()
            pipeline.vector_store = MagicMock()
            pipeline.vector_store.get_collection_stats = MagicMock(
                return_value={
                    "collection_name": "test",
                    "document_count": 4,
                    "endpoint": "http://localhost:8000",
                }
            )

            # Run first batch (2 docs)
            pipeline._process_batches(documents)

            # Verify first batch completed
            assert pipeline._chunk_batch.call_count == 1

            # Check progress - first 2 docs should be stored
            progress = pipeline.progress
            assert progress.get_status("test_doc_000") == DocumentStatus.STORED
            assert progress.get_status("test_doc_001") == DocumentStatus.STORED

            # Reset mock call counts
            pipeline._chunk_batch.reset_mock()
            pipeline._generate_embeddings.reset_mock()
            pipeline._store_batch.reset_mock()

            # Resume processing (should skip first 2 docs, process next 2)
            pipeline._process_batches(documents)

            # Should only process the second batch
            assert pipeline._chunk_batch.call_count == 1
            assert progress.get_status("test_doc_002") == DocumentStatus.STORED
            assert progress.get_status("test_doc_003") == DocumentStatus.STORED

    def test_interrupted_batch_recovery(self, test_config_path, temp_output_dir):
        """Test recovery from interrupted batch processing."""
        with patch(
            "src.pipeline.ResearchRAGPipeline._load_config"
        ) as mock_load, patch(
            "src.pipeline.ResearchRAGPipeline._initialize_sources"
        ) as mock_sources, patch(
            "src.pipeline.ResearchRAGPipeline._needs_reindex"
        ) as mock_needs_reindex, patch(
            "src.pipeline.ResearchRAGPipeline._fetch_all_documents"
        ) as mock_fetch, patch(
            "src.pipeline.ResearchRAGPipeline._save_source_hash"
        ) as mock_save_hash:

            config = {
                "output_folder": str(temp_output_dir),
                "zotero": {"enabled": False},
                "obsidian": {"enabled": False},
                "embedding": {
                    "model": "test",
                    "api_endpoint": "http://localhost:1234/v1",
                },
                "storage": {"endpoint": "http://localhost:8000"},
                "chunking": {"chunk_size": 512},
                "indexing": {"batch_size": 2},
            }
            mock_load.return_value = config
            mock_sources.return_value = []
            mock_needs_reindex.return_value = True
            documents = create_test_documents(4)
            mock_fetch.return_value = documents

            pipeline = ResearchRAGPipeline(test_config_path)

            call_count = [0]

            def chunk_side_effect(docs):
                call_count[0] += 1
                # Fail on second batch
                if call_count[0] == 2:
                    raise RuntimeError("Simulated failure in chunking")
                return (["chunk1"], [{"doc_id": "test"}], ["id1"])

            pipeline._chunk_batch = MagicMock(side_effect=chunk_side_effect)
            pipeline._generate_embeddings = MagicMock(return_value=[[0.1] * 1024])
            pipeline._store_batch = MagicMock()
            pipeline.vector_store = MagicMock()
            pipeline.vector_store.get_collection_stats = MagicMock(
                return_value={
                    "collection_name": "test",
                    "document_count": 4,
                    "endpoint": "http://localhost:8000",
                }
            )

            # Run with expected failure
            pipeline._process_batches(documents)

            # First batch should have succeeded
            assert pipeline.progress.get_status("test_doc_000") == DocumentStatus.STORED
            # Second batch should have failed
            assert pipeline.progress.get_status("test_doc_002") == DocumentStatus.ERROR

    def test_no_duplicate_indexing(self, test_config_path, temp_output_dir):
        """Test that resuming doesn't create duplicate entries."""
        with patch(
            "src.pipeline.ResearchRAGPipeline._load_config"
        ) as mock_load, patch(
            "src.pipeline.ResearchRAGPipeline._initialize_sources"
        ) as mock_sources, patch(
            "src.pipeline.ResearchRAGPipeline._needs_reindex"
        ) as mock_needs_reindex, patch(
            "src.pipeline.ResearchRAGPipeline._fetch_all_documents"
        ) as mock_fetch, patch(
            "src.pipeline.ResearchRAGPipeline._save_source_hash"
        ) as mock_save_hash:

            config = {
                "output_folder": str(temp_output_dir),
                "zotero": {"enabled": False},
                "obsidian": {"enabled": False},
                "embedding": {
                    "model": "test",
                    "api_endpoint": "http://localhost:1234/v1",
                },
                "storage": {"endpoint": "http://localhost:8000"},
                "chunking": {"chunk_size": 512},
                "indexing": {"batch_size": 2},
            }
            mock_load.return_value = config
            mock_sources.return_value = []
            mock_needs_reindex.return_value = True
            documents = create_test_documents(4)
            mock_fetch.return_value = documents

            pipeline = ResearchRAGPipeline(test_config_path)

            pipeline._chunk_batch = MagicMock(
                return_value=(["chunk1"], [{"doc_id": "test"}], ["id1"])
            )
            pipeline._generate_embeddings = MagicMock(return_value=[[0.1] * 1024])
            pipeline._store_batch = MagicMock()
            pipeline.vector_store = MagicMock()
            pipeline.vector_store.get_collection_stats = MagicMock(
                return_value={
                    "collection_name": "test",
                    "document_count": 4,
                    "endpoint": "http://localhost:8000",
                }
            )

            # Process all documents twice
            pipeline._process_batches(documents)
            store_call_count_first = pipeline._store_batch.call_count

            pipeline._process_batches(documents)
            store_call_count_second = pipeline._store_batch.call_count

            # Second run should not call store_batch (all already processed)
            assert store_call_count_second == store_call_count_first

    def test_progress_file_integrity(self, test_config_path, temp_output_dir):
        """Test that progress file stays valid and consistent."""
        progress_file = temp_output_dir / "indexing_progress.json"
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
