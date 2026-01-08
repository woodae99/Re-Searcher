"""Main pipeline for indexing research library into vector store."""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .factories.chunker_factory import create_chunker
from .factories.embedding_factory import create_embedder
from .factories.reranker_factory import create_reranker
from .indexing import DocumentStatus, IndexingProgress
from .processing.id_utils import attach_parent_ids, stable_chunk_id
from .processing.oversize_guard import create_oversize_guard
from .progress import IndexingStage, ProgressDisplay, create_progress_display
from .retrieval.expand import attach_parent_context
from .sources.base import ProgressCallback
from .sources.obsidian import ObsidianSource
from .sources.zotero import ZoteroSource
from .storage.chroma import ChromaVectorStore


class ResearchRAGPipeline:
    """Main pipeline for indexing research library."""

    def __init__(self, config_path: Path, progress_mode: str = "auto"):
        """
        Initialize pipeline with configuration.

        Args:
            config_path: Path to YAML configuration file
            progress_mode: Progress display mode - "auto", "rich", "plain", or "quiet"
        """
        self.config = self._load_config(config_path)
        self.config_path = config_path

        # Initialize progress display (before sources so callbacks are available)
        self.progress_display: ProgressDisplay = create_progress_display(progress_mode)

        # Initialize components
        self.sources = self._initialize_sources()
        self.chunker = create_chunker(self.config)
        self.oversize_guard = create_oversize_guard(self.config)
        self.embedder = create_embedder(self.config)
        self.reranker = create_reranker(self.config)
        self.vector_store = ChromaVectorStore(self.config)

        # Output directory for metadata
        self.output_dir = Path(self.config.get("output_folder", "./output"))
        self.output_dir.mkdir(exist_ok=True)

        # Progress tracking for resumable indexing
        progress_file = self.output_dir / "indexing_progress.json"
        self.progress = IndexingProgress(progress_file)

        # Batch configuration
        self.batch_size = self.config.get("indexing", {}).get("batch_size", 50)

    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    def _create_source_progress_callback(self, source_name: str) -> ProgressCallback:
        """Create a progress callback for a data source."""

        def callback(event: Dict[str, Any]) -> None:
            event_type = event.get("event", "")

            if event_type == "source_init":
                total = event.get("total", 0)
                self.progress_display.init_source(source_name, total)

            elif event_type == "item_complete":
                status = event.get("status", "success")
                if status == "success":
                    self.progress_display.update_source(source_name, processed=1, new=1)
                elif status == "empty":
                    self.progress_display.update_source(source_name, processed=1, skipped=1)

            elif event_type == "item_error":
                self.progress_display.update_source(source_name, processed=1, errors=1)

            elif event_type == "attachment_start":
                file_name = event.get("file_name", "")
                file_size_mb = event.get("file_size_mb", 0.0)
                self.progress_display.set_activity(
                    f"Extracting attachment",
                    file_name=file_name,
                    file_size_mb=file_size_mb,
                )

            elif event_type == "attachment_complete":
                status = event.get("status", "success")
                if status == "success":
                    self.progress_display.set_activity("Attachment extracted")
                else:
                    self.progress_display.set_activity("Attachment skipped (empty)")

            elif event_type == "attachment_error":
                error = event.get("error", "unknown error")
                self.progress_display.set_activity(f"Attachment error: {error}")

        return callback

    def _initialize_sources(self) -> List:
        """Initialize all enabled data sources."""
        sources = []

        # Zotero source
        zotero_callback = self._create_source_progress_callback("Zotero")
        zotero = ZoteroSource(self.config, progress_callback=zotero_callback)
        if zotero.is_enabled() and zotero.validate_config():
            sources.append(zotero)
            print("[OK] Zotero source enabled")

        # Obsidian source
        obsidian_callback = self._create_source_progress_callback("Obsidian")
        obsidian = ObsidianSource(self.config, progress_callback=obsidian_callback)
        if obsidian.is_enabled() and obsidian.validate_config():
            sources.append(obsidian)
            print("[OK] Obsidian source enabled")

        if not sources:
            print("[WARNING] No data sources enabled!")

        return sources

    def run(self, force_reindex: bool = False):
        """
        Run the full indexing pipeline with resumable batch processing.

        Args:
            force_reindex: If True, re-index even if sources haven't changed
        """
        # Start progress display
        self.progress_display.start()

        try:
            # Stage 1: Initializing
            self.progress_display.set_stage(IndexingStage.INITIALIZING, 1, 4)

            # Check if re-indexing is needed
            if not force_reindex and not self._needs_reindex():
                print("[INFO] Index is up to date. Use --force to re-index anyway.")
                return

            # Stage 2: Fetching documents
            self.progress_display.set_stage(IndexingStage.FETCHING, 2, 4)
            self.progress_display.set_activity("Fetching documents from sources...")
            documents = self._fetch_all_documents()

            if not documents:
                print("[WARNING] No documents to index!")
                return

            # Initialize progress tracking
            self.progress.set_total_documents(len(documents))

            # Stage 3: Chunking + Embedding
            self.progress_display.set_stage(IndexingStage.CHUNKING, 3, 4)
            self.progress_display.set_activity(f"Processing {len(documents)} documents in batches...")
            self._process_batches(documents)

            # Save hash of sources
            self._save_source_hash()

            # Stage 4: Complete
            self.progress_display.set_stage(IndexingStage.COMPLETE, 4, 4)
            self.progress_display.set_activity("Indexing complete!")

            # Print final stats
            stats = self.vector_store.get_collection_stats()
            print(f"\nCollection: {stats.get('collection_name')}")
            print(f"Total documents: {stats.get('document_count')}")
            print(f"Endpoint: {stats.get('endpoint')}")

        finally:
            # Always stop progress display
            self.progress_display.stop()

    def _process_batches(self, documents: List):
        """
        Process documents in batches with resumable checkpoints.

        Args:
            documents: List of Document objects
        """
        total_batches = (len(documents) - 1) // self.batch_size + 1

        # Process in batches
        for batch_idx in range(0, len(documents), self.batch_size):
            batch_end = min(batch_idx + self.batch_size, len(documents))
            batch = documents[batch_idx:batch_end]
            batch_num = batch_idx // self.batch_size + 1

            self.progress_display.set_activity(f"Processing batch {batch_num}/{total_batches}")

            # Filter out already processed documents
            pending_docs = [
                doc
                for doc in batch
                if not self.progress.has_completed_status(doc.doc_id)
            ]

            if not pending_docs:
                continue

            try:
                # Step 1: Chunk documents
                self.progress_display.set_stage(IndexingStage.CHUNKING, 3, 4)
                self.progress_display.set_activity(f"Chunking batch {batch_num}/{total_batches}...")
                chunks, chunk_metadatas, chunk_ids = self._chunk_batch(pending_docs)

                if not chunks:
                    continue

                # Update progress: documents chunked
                for doc in pending_docs:
                    doc_chunks = sum(
                        1 for chunk_id in chunk_ids
                        if chunk_id.startswith(f"{doc.doc_id}-")
                    )
                    self.progress.set_document_status(
                        doc.doc_id,
                        DocumentStatus.CHUNKED,
                        chunk_count=doc_chunks,
                    )

                # Step 2: Generate embeddings
                self.progress_display.set_stage(IndexingStage.EMBEDDING, 3, 4)
                self.progress_display.set_activity(f"Embedding {len(chunks)} chunks (batch {batch_num}/{total_batches})...")
                try:
                    embeddings = self._generate_embeddings(chunks)
                except Exception as e:
                    for doc in pending_docs:
                        self.progress.set_document_status(
                            doc.doc_id,
                            DocumentStatus.ERROR,
                            error_msg=str(e),
                        )
                    continue

                # Update progress: documents embedded
                for doc in pending_docs:
                    self.progress.set_document_status(
                        doc.doc_id,
                        DocumentStatus.EMBEDDED,
                    )

                # Step 3: Store embeddings
                self.progress_display.set_stage(IndexingStage.STORING, 3, 4)
                self.progress_display.set_activity(f"Storing {len(chunks)} chunks (batch {batch_num}/{total_batches})...")
                try:
                    self._store_batch(chunks, embeddings, chunk_metadatas, chunk_ids)
                except Exception as e:
                    for doc in pending_docs:
                        self.progress.set_document_status(
                            doc.doc_id,
                            DocumentStatus.ERROR,
                            error_msg=str(e),
                        )
                    continue

                # Update progress: documents stored
                for doc in pending_docs:
                    self.progress.set_document_status(
                        doc.doc_id,
                        DocumentStatus.STORED,
                    )

            except Exception as e:
                for doc in pending_docs:
                    self.progress.set_document_status(
                        doc.doc_id,
                        DocumentStatus.ERROR,
                        error_msg=str(e),
                    )

    def _fetch_all_documents(self) -> List:
        """Fetch documents from all sources."""
        all_documents = []

        for source in self.sources:
            source_name = source.__class__.__name__
            self.progress_display.set_activity(f"Fetching from {source_name}...")
            try:
                documents = list(source.fetch_documents())
                all_documents.extend(documents)
            except Exception as e:
                # Log error but continue with other sources
                self.progress_display.set_activity(f"Error fetching from {source_name}: {e}")

        return all_documents

    def _chunk_batch(self, documents: List) -> tuple:
        """
        Chunk a batch of documents into smaller segments.

        Returns:
            Tuple of (chunks, metadatas, ids)
        """
        # Step 1: Chunk all documents
        all_chunk_data: List[tuple] = []

        for doc in documents:
            try:
                chunk_data = self.chunker.chunk_with_metadata(doc.content, doc.metadata)

                # Add source_id to each chunk's metadata
                for chunk_text, chunk_metadata in chunk_data:
                    chunk_metadata["source_id"] = doc.doc_id
                    all_chunk_data.append((chunk_text, chunk_metadata))

            except Exception as e:
                print(f"  [WARNING] Error chunking document {doc.doc_id}: {e}")

        # Step 2: Apply oversize guard (CRITICAL - runs after all chunking, before IDs)
        all_chunk_data = self.oversize_guard.process(all_chunk_data)

        # Log oversize guard stats if any chunks were handled
        guard_stats = self.oversize_guard.get_stats()
        if guard_stats.split > 0 or guard_stats.truncated > 0 or guard_stats.skipped > 0:
            print(f"    {guard_stats.summary()}")

        # Step 3: Generate IDs for chunks
        all_chunks = []
        all_metadatas = []
        all_ids = []

        chunking_config = self.config.get("chunking", {})
        id_strategy = chunking_config.get("id_strategy", "legacy")

        for idx, (chunk_text, chunk_metadata) in enumerate(all_chunk_data):
            all_chunks.append(chunk_text)
            all_metadatas.append(chunk_metadata)

            # Generate unique ID for chunk
            if id_strategy == "legacy":
                source_id = chunk_metadata.get("source_id", "unknown")
                chunk_id = f"{source_id}-chunk-{idx}"
            else:
                source_id = chunk_metadata.get("source_id", "unknown")
                level = chunk_metadata.get("chunk_level", "mid")
                chunk_id = stable_chunk_id(source_id, level, idx, chunk_text)
            all_ids.append(chunk_id)

        # Step 4: Attach parent IDs
        attach_parent_ids(all_metadatas, all_ids)

        return all_chunks, all_metadatas, all_ids

    def _generate_embeddings(self, chunks: List[str]) -> List[List[float]]:
        """Generate embeddings for chunks."""
        try:
            embeddings = self.embedder.embed_texts(chunks)
            return embeddings
        except Exception as e:
            print(f"[ERROR] Error generating embeddings: {e}")
            raise

    def _store_batch(
        self,
        chunks: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str],
    ):
        """Store embeddings from a batch in vector database."""
        try:
            # Store in sub-batches to avoid overwhelming ChromaDB
            batch_size = 100
            for i in range(0, len(chunks), batch_size):
                batch_end = min(i + batch_size, len(chunks))
                self.vector_store.add_documents(
                    texts=chunks[i:batch_end],
                    embeddings=embeddings[i:batch_end],
                    metadatas=metadatas[i:batch_end],
                    ids=ids[i:batch_end],
                )
        except Exception as e:
            print(f"[ERROR] Error storing embeddings: {e}")
            raise

    def _compute_source_hash(self) -> str:
        """Compute hash of all data sources to detect changes."""
        h = hashlib.md5()

        # Hash config file
        if self.config_path.exists():
            h.update(self.config_path.read_bytes())

        # Hash Zotero database if enabled
        zotero_cfg = self.config.get("zotero", {})
        if zotero_cfg.get("enabled"):
            zotero_db = Path(zotero_cfg.get("data_directory", "")).expanduser() / "zotero.sqlite"
            if zotero_db.exists():
                h.update(str(zotero_db.stat().st_mtime).encode())

        # Hash Obsidian vault if enabled
        obsidian_cfg = self.config.get("obsidian", {})
        if obsidian_cfg.get("enabled"):
            vault_path = Path(obsidian_cfg.get("vault_path", "")).expanduser()
            if vault_path.exists():
                # Hash all markdown files
                for md_file in sorted(vault_path.rglob("*.md")):
                    h.update(str(md_file.stat().st_mtime).encode())

        return h.hexdigest()

    def _needs_reindex(self) -> bool:
        """Check if re-indexing is needed based on source changes."""
        hash_file = self.output_dir / "source_hash.txt"

        current_hash = self._compute_source_hash()
        if not hash_file.exists():
            return True

        previous_hash = hash_file.read_text().strip()
        return current_hash != previous_hash

    def _save_source_hash(self):
        """Save current source hash."""
        hash_file = self.output_dir / "source_hash.txt"
        current_hash = self._compute_source_hash()
        hash_file.write_text(current_hash)

    def query(self, query_text: str, k: int = 5) -> List:
        """
        Query the vector store.

        Args:
            query_text: Query string
            k: Number of results to return

        Returns:
            List of search results
        """
        print(f"\nQuery: {query_text}\n")

        # Generate query embedding
        query_embedding = self.embedder.embed_query(query_text)

        retrieval_config = self.config.get("retrieval", {})
        k_recall = retrieval_config.get("k_recall", 50)
        k_return = k

        # Search vector store
        results = self.vector_store.search(query_embedding, k=k_recall)

        rerank_config = retrieval_config.get("rerank", {})
        if rerank_config.get("enabled", False):
            results = self.reranker.rerank(query_text, results)
            top_n = rerank_config.get("top_n")
            limit = k_return if top_n is None else min(k_return, top_n)
            results = results[:limit]
        else:
            results = results[:k_return]

        expand_config = retrieval_config.get("expand", {})
        if expand_config.get("include_parent", False):
            max_parents = expand_config.get("max_parents", 1)
            results = attach_parent_context(results, self.vector_store, max_parents=max_parents)

        return results
