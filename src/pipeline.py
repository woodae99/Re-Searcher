"""Main pipeline for indexing research library into vector store."""

import json
import hashlib
import queue
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .factories.chunker_factory import create_chunker
from .factories.embedding_factory import create_embedder
from .factories.reranker_factory import create_reranker
from .indexing import DocumentStatus, IndexingProgress
from .processing.id_utils import attach_parent_ids, stable_chunk_id
from .processing.oversize_guard import create_oversize_guard
from .processing.quality_filter import create_quality_filter_guard
from .progress import IndexingStage, ProgressDisplay, create_progress_display
from .retrieval.diversity import apply_diversity
from .retrieval.expand import attach_parent_context
from .retrieval.filters import apply_post_filters, build_where_filter
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

        # Output directory for metadata and live control files.
        self.output_dir = Path(self.config.get("output_folder", "./output"))
        self.output_dir.mkdir(exist_ok=True)

        dashboard_cfg = self.config.get("indexing", {}).get("dashboard", {}) or {}
        snapshot_file = None
        if dashboard_cfg.get("enabled", True):
            snapshot_file = self._resolve_output_path(
                dashboard_cfg.get("snapshot_file", "indexing_dashboard.json")
            )
        snapshot_interval = float(dashboard_cfg.get("snapshot_interval_seconds", 1.0))

        # Initialize progress display (before sources so callbacks are available)
        self.progress_display: ProgressDisplay = create_progress_display(
            progress_mode,
            snapshot_file=snapshot_file,
            snapshot_interval=snapshot_interval,
        )

        # Initialize components
        self.sources = self._initialize_sources()
        self.chunker = create_chunker(self.config)
        self.oversize_guard = create_oversize_guard(self.config)
        self.quality_filter = create_quality_filter_guard(self.config)
        self.embedder = create_embedder(self.config)
        self.reranker = create_reranker(self.config)
        self.vector_store = ChromaVectorStore(self.config)

        # Progress tracking for resumable indexing, scoped per collection.
        collection_name = str(
            self.config.get("storage", {}).get("collection_name", "research_library")
        )
        collection_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", collection_name).strip("_")
        if not collection_slug:
            collection_slug = "research_library"
        progress_file = self.output_dir / f"indexing_progress.{collection_slug}.json"
        self.progress = IndexingProgress(progress_file)

        # Batch configuration
        self.batch_size = self.config.get("indexing", {}).get("batch_size", 50)
        stop_cfg = self.config.get("indexing", {}).get("stop_after_batch", {}) or {}
        self.stop_flag_path = self._resolve_output_path(
            stop_cfg.get("flag_file", "stop_after_batch.flag")
        )

    def _resolve_output_path(self, raw_path: str) -> Path:
        """Resolve a configured path relative to the output directory."""
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return self.output_dir / path

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

            # If previous run was interrupted, resume even when source hashes are unchanged.
            resume_incomplete = self.progress.has_incomplete_work()
            if resume_incomplete and not force_reindex:
                print("[INFO] Incomplete checkpoint detected; resuming from saved batch progress.")

            # Check if re-indexing is needed
            changed_zotero_keys: Optional[List[str]] = None
            delta_versions: Optional[Dict[str, Any]] = None
            if force_reindex:
                print("[INFO] --force enabled: resetting index state and vector store")
                self._reset_index_state()
            elif resume_incomplete:
                # Resume mode should prioritize checkpoint continuity over delta
                # re-discovery, which can otherwise fan out to broad "changed"
                # sets when delta state is missing/stale.
                print("[INFO] Resume mode: skipping delta change discovery and continuing from checkpoint.")
            elif self._delta_enabled():
                delta_changes = self._collect_zotero_delta_changes()
                changed_zotero_keys = delta_changes.get("changed_item_keys", [])
                delta_versions = {
                    "item_version": delta_changes.get("item_version", 0),
                    "fulltext_version": delta_changes.get("fulltext_version", 0),
                    "sqlite_date_modified": delta_changes.get("sqlite_max_date_modified", ""),
                    "sqlite_date_deleted": delta_changes.get("sqlite_max_date_deleted", ""),
                    "sqlite_attachment_storage_mod_time": delta_changes.get(
                        "sqlite_max_attachment_storage_mod_time",
                        0,
                    ),
                }
                if changed_zotero_keys:
                    print(f"[INFO] Delta mode: {len(changed_zotero_keys)} changed Zotero items detected.")
                else:
                    print("[INFO] Delta mode: no Zotero changes detected.")

                # If delta reports no changes but global source hash changed, fall back
                # to a full scan for safety (covers local-API outages or unsupported edits).
                global_reindex_needed = self._needs_reindex()
                if not changed_zotero_keys and global_reindex_needed:
                    print("[WARN] Delta reported no changed keys but source hash changed; falling back to full Zotero scan.")
                    changed_zotero_keys = None

                # If no changes anywhere, exit early.
                if changed_zotero_keys == [] and not global_reindex_needed and not resume_incomplete:
                    if delta_versions:
                        self._save_delta_state(
                            item_version=delta_versions["item_version"],
                            fulltext_version=delta_versions["fulltext_version"],
                            sqlite_date_modified=delta_versions.get("sqlite_date_modified", ""),
                            sqlite_date_deleted=delta_versions.get("sqlite_date_deleted", ""),
                            sqlite_attachment_storage_mod_time=delta_versions.get(
                                "sqlite_attachment_storage_mod_time",
                                0,
                            ),
                        )
                    print("[INFO] No changes detected. Use --force to re-index anyway.")
                    return
            elif not self._needs_reindex() and not resume_incomplete:
                print("[INFO] Index is up to date. Use --force to re-index anyway.")
                return

            # Stage 2: Fetching documents
            self.progress_display.set_stage(IndexingStage.FETCHING, 2, 4)
            self.progress_display.set_activity("Fetching documents from sources...")
            documents = self._fetch_all_documents(zotero_item_keys=changed_zotero_keys)

            if not documents:
                if delta_versions:
                    self._save_delta_state(
                        item_version=delta_versions["item_version"],
                        fulltext_version=delta_versions["fulltext_version"],
                        sqlite_date_modified=delta_versions.get("sqlite_date_modified", ""),
                        sqlite_date_deleted=delta_versions.get("sqlite_date_deleted", ""),
                        sqlite_attachment_storage_mod_time=delta_versions.get(
                            "sqlite_attachment_storage_mod_time",
                            0,
                        ),
                    )
                print("[WARNING] No documents to index!")
                return

            if changed_zotero_keys:
                # Replace old chunks for changed Zotero items to avoid stale duplicates.
                self._delete_existing_zotero_chunks(changed_zotero_keys)
                # Ensure changed docs are not skipped due old progress entries.
                for doc in documents:
                    if doc.metadata.get("source_type", "").startswith("zotero"):
                        self.progress.forget_document(doc.doc_id)

            # Initialize progress tracking
            self.progress.set_total_documents(len(documents))
            self._overall_total_chunks = 0
            self._overall_embedded = 0
            self._overall_stored = 0

            # Stage 3: Chunking + Embedding
            self.progress_display.set_stage(IndexingStage.CHUNKING, 3, 4)
            self.progress_display.set_activity(f"Processing {len(documents)} documents in batches...")
            completed_run = self._process_batches(documents)
            if not completed_run:
                print(
                    "[INFO] Indexing paused after completing the current batch. "
                    "Run the same command again to resume."
                )
                return

            # Resumed full runs skip initial delta discovery, so capture the
            # current Zotero watermarks at the end before future incremental runs.
            if self._delta_enabled() and delta_versions is None:
                current_delta = self._collect_zotero_delta_changes()
                delta_versions = {
                    "item_version": current_delta.get("item_version", 0),
                    "fulltext_version": current_delta.get("fulltext_version", 0),
                    "sqlite_date_modified": current_delta.get("sqlite_max_date_modified", ""),
                    "sqlite_date_deleted": current_delta.get("sqlite_max_date_deleted", ""),
                    "sqlite_attachment_storage_mod_time": current_delta.get(
                        "sqlite_max_attachment_storage_mod_time",
                        0,
                    ),
                }

            # Save hash of sources
            self._save_source_hash()
            if delta_versions:
                self._save_delta_state(
                    item_version=delta_versions["item_version"],
                    fulltext_version=delta_versions["fulltext_version"],
                    sqlite_date_modified=delta_versions.get("sqlite_date_modified", ""),
                    sqlite_date_deleted=delta_versions.get("sqlite_date_deleted", ""),
                    sqlite_attachment_storage_mod_time=delta_versions.get(
                        "sqlite_attachment_storage_mod_time",
                        0,
                    ),
                )

            # Stage 4: Complete
            self.progress_display.set_stage(IndexingStage.COMPLETE, 4, 4)
            self.progress_display.set_activity("Indexing complete!")

            # Print final stats
            stats = self.vector_store.get_collection_stats()
            print(f"\nCollection: {stats.get('collection_name')}")
            print(f"Total documents: {stats.get('document_count')}")
            print(f"Endpoint: {stats.get('endpoint')}")

        finally:
            # Persist quality-filter diagnostics when enabled.
            self.quality_filter.write_report()
            # Always stop progress display
            self.progress_display.stop()

    def _process_batches(self, documents: List) -> bool:
        """
        Process documents in batches with resumable checkpoints.

        Args:
            documents: List of Document objects
        """
        total_batches = (len(documents) - 1) // self.batch_size + 1

        # Process in batches
        for batch_idx in range(0, len(documents), self.batch_size):
            if self._consume_stop_request():
                self.progress_display.set_activity(
                    "Stop requested; halting before next batch."
                )
                return False

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

                self._overall_total_chunks += len(chunks)

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

                # Step 2: Generate embeddings (+ store, if pipelined)
                self.progress_display.set_stage(IndexingStage.EMBEDDING, 3, 4)
                if self._should_use_embed_store_pipeline():
                    self.progress_display.set_activity(
                        f"Batch {batch_num} of {total_batches} Embedding + storing {len(chunks)} chunks"
                    )
                    try:
                        self._process_embeddings_and_store(
                            chunks,
                            chunk_metadatas,
                            chunk_ids,
                            batch_num,
                            total_batches,
                        )
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
                else:
                    self.progress_display.set_activity(
                        f"Batch {batch_num} of {total_batches} Embedding {len(chunks)} chunks"
                    )
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
                    self.progress_display.set_activity(
                        f"Batch {batch_num} of {total_batches} Storing {len(chunks)} chunks"
                    )
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

        return True

    def _consume_stop_request(self) -> bool:
        """Return True when a stop-after-batch request is present and consume it."""
        if not self.stop_flag_path.exists():
            return False

        try:
            self.stop_flag_path.unlink()
        except FileNotFoundError:
            return False

        print(f"[INFO] Stop-after-batch request detected: {self.stop_flag_path}")
        return True

    def _fetch_all_documents(self, zotero_item_keys: Optional[List[str]] = None) -> List:
        """Fetch documents from all sources."""
        all_documents = []
        zotero_cfg = self.config.get("zotero", {}) or {}
        zotero_strict = bool(zotero_cfg.get("fail_if_no_documents", True))

        for source in self.sources:
            source_name = source.__class__.__name__
            self.progress_display.set_activity(f"Fetching from {source_name}...")
            try:
                if isinstance(source, ZoteroSource) and zotero_item_keys is not None:
                    documents = list(source.fetch_documents(item_keys=zotero_item_keys))
                else:
                    documents = list(source.fetch_documents())

                if isinstance(source, ZoteroSource) and not documents:
                    msg = (
                        "Zotero source returned 0 documents. "
                        "This usually means the Zotero SQLite DB is locked or unavailable."
                    )
                    if zotero_strict:
                        raise RuntimeError(msg)
                    print(f"[WARN] {msg}")

                all_documents.extend(documents)
            except Exception as e:
                # Log error but continue with other sources
                self.progress_display.set_activity(f"Error fetching from {source_name}: {e}")
                if isinstance(source, ZoteroSource) and zotero_strict:
                    raise

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
            ordinal_raw = chunk_metadata.get("chunk_index", idx)
            try:
                ordinal = int(ordinal_raw)
            except (TypeError, ValueError):
                ordinal = idx
            if id_strategy == "legacy":
                source_id = chunk_metadata.get("source_id", "unknown")
                chunk_id = f"{source_id}-chunk-{ordinal}"
            else:
                source_id = chunk_metadata.get("source_id", "unknown")
                level = chunk_metadata.get("chunk_level", "mid")
                variant = chunk_metadata.get("chunk_id_variant")
                chunk_id = stable_chunk_id(
                    source_id,
                    level,
                    ordinal,
                    chunk_text,
                    variant=variant,
                )
            all_ids.append(chunk_id)

        # Step 4: Apply quality filter before embedding (drops low-signal chunks).
        all_chunks, all_metadatas, all_ids = self.quality_filter.process_with_ids(
            all_chunks,
            all_metadatas,
            all_ids,
        )

        # Step 5: Attach parent IDs
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

    def _should_use_embed_store_pipeline(self) -> bool:
        """Check if embed/store pipelining is enabled."""
        pipeline_cfg = self.config.get("indexing", {}).get("embed_store_pipeline", {})
        return bool(pipeline_cfg.get("enabled", False))

    def _process_embeddings_and_store(
        self,
        chunks: List[str],
        chunk_metadatas: List[Dict[str, Any]],
        chunk_ids: List[str],
        batch_num: int,
        total_batches: int,
    ) -> None:
        """Embed chunks and store in Chroma, optionally pipelined."""
        if not chunks:
            return

        if not self._should_use_embed_store_pipeline():
            embeddings = self._generate_embeddings(chunks)
            self._store_batch(chunks, embeddings, chunk_metadatas, chunk_ids)
            return

        pipeline_cfg = self.config.get("indexing", {}).get("embed_store_pipeline", {})
        queue_max = int(pipeline_cfg.get("queue_max_items", 8))
        embed_sub_batch = int(pipeline_cfg.get("embed_sub_batch_size", 2000))
        store_sub_batch = int(pipeline_cfg.get("store_sub_batch_size", embed_sub_batch))

        work_queue: queue.Queue = queue.Queue(maxsize=queue_max)
        stop_event = threading.Event()
        error_lock = threading.Lock()
        errors: List[tuple[Exception, str]] = []
        progress_lock = threading.Lock()
        embedded_count = 0
        stored_count = 0
        total_chunks = len(chunks)
        last_update = 0.0

        def maybe_update_activity() -> None:
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 0.5:
                return
            last_update = now
            overall_total = self._overall_total_chunks
            overall_embedded = self._overall_embedded
            overall_stored = self._overall_stored
            overall_embedded_pct = 0.0 if overall_total == 0 else (overall_embedded / overall_total) * 100
            overall_stored_pct = 0.0 if overall_total == 0 else (overall_stored / overall_total) * 100
            detail = (
                "Overall Progress: "
                f"Embedded {overall_embedded}/{overall_total} ({overall_embedded_pct:.1f}%) | "
                f"Stored {overall_stored}/{overall_total} ({overall_stored_pct:.1f}%)"
            )
            self.progress_display.set_activity(
                f"Batch {batch_num} of {total_batches} Embedding + storing {total_chunks} chunks | "
                f"embedded: {embedded_count} | stored: {stored_count}",
                detail=detail,
            )

        def record_error(err: Exception, trace: str) -> None:
            with error_lock:
                errors.append((err, trace))
                stop_event.set()
            print(f"[ERROR] Embed/store pipeline error: {err}")
            if trace:
                print(trace)

        def producer() -> None:
            nonlocal embedded_count
            try:
                for i in range(0, len(chunks), embed_sub_batch):
                    if stop_event.is_set():
                        break
                    batch_end = min(i + embed_sub_batch, len(chunks))
                    batch_chunks = chunks[i:batch_end]
                    batch_metadatas = chunk_metadatas[i:batch_end]
                    batch_ids = chunk_ids[i:batch_end]

                    embeddings = self._generate_embeddings(batch_chunks)
                    with progress_lock:
                        embedded_count += len(batch_chunks)
                        self._overall_embedded += len(batch_chunks)
                        maybe_update_activity()
                    while True:
                        try:
                            work_queue.put(
                                (batch_chunks, embeddings, batch_metadatas, batch_ids),
                                timeout=1,
                            )
                            break
                        except queue.Full:
                            if stop_event.is_set():
                                return
            except Exception as e:
                record_error(e, traceback.format_exc())
            finally:
                while True:
                    try:
                        work_queue.put(None, timeout=1)
                        break
                    except queue.Full:
                        if stop_event.is_set():
                            break

        def consumer() -> None:
            nonlocal stored_count
            try:
                while True:
                    try:
                        item = work_queue.get(timeout=1)
                    except queue.Empty:
                        if stop_event.is_set():
                            break
                        continue
                    if item is None:
                        break
                    batch_chunks, batch_embeddings, batch_metadatas, batch_ids = item

                    for i in range(0, len(batch_chunks), store_sub_batch):
                        if stop_event.is_set():
                            break
                        batch_end = min(i + store_sub_batch, len(batch_chunks))
                        self._store_batch(
                            batch_chunks[i:batch_end],
                            batch_embeddings[i:batch_end],
                            batch_metadatas[i:batch_end],
                            batch_ids[i:batch_end],
                        )
                        with progress_lock:
                            stored_count += batch_end - i
                            self._overall_stored += batch_end - i
                            maybe_update_activity()
            except Exception as e:
                record_error(e, traceback.format_exc())

        producer_thread = threading.Thread(target=producer, name="EmbedProducer")
        consumer_thread = threading.Thread(target=consumer, name="StoreConsumer")

        producer_thread.start()
        consumer_thread.start()

        producer_thread.join()
        consumer_thread.join()

        if errors:
            raise errors[0][0]

    def _store_batch(
        self,
        chunks: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str],
    ):
        """Store embeddings from a batch in vector database."""
        try:
            indexed_at = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            for metadata in metadatas:
                metadata.setdefault("indexed_at", indexed_at)

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

    def _delta_cfg(self) -> Dict[str, Any]:
        return self.config.get("indexing", {}).get("delta", {}) or {}

    def _delta_enabled(self) -> bool:
        return bool(self._delta_cfg().get("enabled", False))

    def _delta_state_path(self) -> Path:
        cfg = self._delta_cfg()
        state_file = cfg.get("state_file", "zotero_delta_state.json")
        return self.output_dir / state_file

    def _load_delta_state(self) -> Dict[str, Any]:
        path = self._delta_state_path()
        if not path.exists():
            return {
                "last_item_version": 0,
                "last_fulltext_version": 0,
                "last_sqlite_date_modified": "",
                "last_sqlite_date_deleted": "",
                "last_sqlite_attachment_storage_mod_time": 0,
            }
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sqlite_modified = str(
                data.get(
                    "last_sqlite_date_modified",
                    data.get("last_sqlite_effective_modified", ""),
                )
                or ""
            )
            attachment_storage_mod_time = data.get(
                "last_sqlite_attachment_storage_mod_time",
                0,
            )
            if not attachment_storage_mod_time and sqlite_modified:
                try:
                    attachment_storage_mod_time = int(
                        datetime.fromisoformat(
                            sqlite_modified.replace(" ", "T")
                        ).timestamp()
                        * 1000
                    )
                except ValueError:
                    attachment_storage_mod_time = 0
            return {
                "last_item_version": int(data.get("last_item_version", 0)),
                "last_fulltext_version": int(data.get("last_fulltext_version", 0)),
                "last_sqlite_date_modified": sqlite_modified,
                "last_sqlite_date_deleted": str(data.get("last_sqlite_date_deleted", "") or ""),
                "last_sqlite_attachment_storage_mod_time": int(attachment_storage_mod_time or 0),
            }
        except Exception:
            return {
                "last_item_version": 0,
                "last_fulltext_version": 0,
                "last_sqlite_date_modified": "",
                "last_sqlite_date_deleted": "",
                "last_sqlite_attachment_storage_mod_time": 0,
            }

    def _save_delta_state(
        self,
        *,
        item_version: int,
        fulltext_version: int,
        sqlite_date_modified: str = "",
        sqlite_date_deleted: str = "",
        sqlite_attachment_storage_mod_time: int = 0,
    ) -> None:
        path = self._delta_state_path()
        payload = {
            "last_item_version": int(item_version),
            "last_fulltext_version": int(fulltext_version),
            "last_sqlite_date_modified": sqlite_date_modified or "",
            "last_sqlite_effective_modified": sqlite_date_modified or "",
            "last_sqlite_date_deleted": sqlite_date_deleted or "",
            "last_sqlite_attachment_storage_mod_time": int(sqlite_attachment_storage_mod_time or 0),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _get_zotero_source(self) -> Optional[ZoteroSource]:
        for source in self.sources:
            if isinstance(source, ZoteroSource):
                return source
        return None

    def _collect_zotero_delta_changes(self) -> Dict[str, Any]:
        zotero_source = self._get_zotero_source()
        if not zotero_source:
            return {
                "changed_item_keys": [],
                "item_version": 0,
                "fulltext_version": 0,
            }

        state = self._load_delta_state()
        changes = zotero_source.get_delta_changes(
            last_item_version=state.get("last_item_version", 0),
            last_fulltext_version=state.get("last_fulltext_version", 0),
            last_sqlite_date_modified=state.get("last_sqlite_date_modified", ""),
            last_sqlite_date_deleted=state.get("last_sqlite_date_deleted", ""),
            last_sqlite_attachment_storage_mod_time=state.get(
                "last_sqlite_attachment_storage_mod_time",
                0,
            ),
        )
        return changes

    def _delete_existing_zotero_chunks(self, item_keys: List[str]) -> None:
        if not item_keys:
            return
        max_delete_keys = int(self._delta_cfg().get("max_delete_keys_per_run", 500))
        if len(item_keys) > max_delete_keys:
            print(
                f"[WARN] Skipping targeted chunk deletes for {len(item_keys)} keys "
                f"(max_delete_keys_per_run={max_delete_keys})."
            )
            return
        source_types = ["zotero", "zotero_note", "zotero_fulltext", "zotero_annotation"]
        for key in item_keys:
            for source_type in source_types:
                self.vector_store.delete_where(
                    {
                        "$and": [
                            {"zotero_key": key},
                            {"source_type": source_type},
                        ]
                    }
                )

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

    def _reset_index_state(self):
        """Reset progress and storage for a full re-index."""
        self.progress.clear()

        hash_file = self.output_dir / "source_hash.txt"
        if hash_file.exists():
            hash_file.unlink()
        delta_state_file = self._delta_state_path()
        if delta_state_file.exists():
            delta_state_file.unlink()

        try:
            self.vector_store.delete_collection()
        except Exception as e:
            print(f"[ERROR] Failed to reset vector store: {e}")
            raise

        if hasattr(self.vector_store, "_get_or_create_collection"):
            self.vector_store.collection = self.vector_store._get_or_create_collection()

    def query(
        self,
        query_text: str,
        k: int = 5,
        *,
        retrieval_mode: Optional[str] = None,
        rerank_enabled: Optional[bool] = None,
        diversity_enabled: Optional[bool] = None,
        diversity_max_per_key: Optional[int] = None,
        # Retrieval knobs
        k_recall_override: Optional[int] = None,
        # Filters (deep dives)
        source_type: Optional[str] = None,
        zotero_key: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        chunk_level: Optional[str] = None,
        author_contains: Optional[str] = None,
        title_contains: Optional[str] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List:
        """
        Query the vector store.

        Args:
            query_text: Query string
            k: Number of results to return
            retrieval_mode: Retrieval path mode:
                - "fast": broad vector recall + post-filtering in Python
                - "strict": applies all eligible filters at vector-store query time
            rerank_enabled: Override config to enable/disable reranking
            diversity_enabled: Override config to enable/disable diversity filtering
            diversity_max_per_key: Max results per source (enables diversity if set)
            k_recall_override: Override how many candidates to recall before filtering
            source_type: Filter by source type (zotero_fulltext, zotero_note, obsidian, etc.)
            zotero_key: Filter by specific Zotero item key
            year_min: Filter results published on or after this year
            year_max: Filter results published on or before this year
            chunk_level: Filter by chunk granularity (coarse/mid/fine for context control)
            author_contains: Filter results where author field contains this string
            title_contains: Filter results where title field contains this string
            where: Advanced Chroma where clause for custom filtering

        Returns:
            List of search results as (doc_id, text, score, metadata) tuples
        """
        print(f"\nQuery: {query_text}\n")
        t_total_start = time.perf_counter()
        timings: Dict[str, float] = {}

        # Generate query embedding
        t_embed_start = time.perf_counter()
        query_embedding = self.embedder.embed_query(query_text)
        timings["embed_ms"] = (time.perf_counter() - t_embed_start) * 1000.0

        retrieval_config = self.config.get("retrieval", {})
        k_recall_cfg = retrieval_config.get("k_recall", 50)
        k_recall = int(k_recall_override) if k_recall_override is not None else int(k_recall_cfg)
        k_return = k
        mode_default = str(retrieval_config.get("mode_default", "fast")).lower()
        mode = str(retrieval_mode or mode_default).lower()
        if mode not in {"fast", "strict"}:
            mode = "fast"

        # Build store-level filter:
        # - strict mode: apply all compatible filters at vector-store query time
        # - fast mode: only apply highly selective exact filters in-store
        if mode == "strict":
            where_filter = build_where_filter(
                source_type=source_type,
                zotero_key=zotero_key,
                year_min=year_min,
                year_max=year_max,
                chunk_level=chunk_level,
                extra_where=where,
            )
        else:
            where_filter = build_where_filter(
                zotero_key=zotero_key,
                extra_where=where,
            )

        # If we're applying post-filters, over-recall to preserve enough hits.
        k_recall_eff = k_recall
        has_post_filters = any(
            [
                author_contains,
                title_contains,
                source_type if mode == "fast" else None,
                chunk_level if mode == "fast" else None,
                year_min if mode == "fast" else None,
                year_max if mode == "fast" else None,
            ]
        )
        if has_post_filters:
            k_recall_eff = min(max(k_recall * 5, k_recall), 1000)

        # Search vector store
        t_vector_start = time.perf_counter()
        results = self.vector_store.search(query_embedding, k=k_recall_eff, filter=where_filter)
        timings["vector_ms"] = (time.perf_counter() - t_vector_start) * 1000.0

        # Post-filters
        t_post_filter_start = time.perf_counter()
        if (
            author_contains
            or title_contains
            or (mode == "fast" and (source_type or chunk_level or year_min is not None or year_max is not None))
        ):
            results = apply_post_filters(
                results,
                source_type=(source_type if mode == "fast" else None),
                chunk_level=(chunk_level if mode == "fast" else None),
                year_min=(year_min if mode == "fast" else None),
                year_max=(year_max if mode == "fast" else None),
                author_contains=author_contains,
                title_contains=title_contains,
            )
        timings["postfilter_ms"] = (time.perf_counter() - t_post_filter_start) * 1000.0

        rerank_config = retrieval_config.get("rerank", {})
        rerank_on = rerank_config.get("enabled", False) if rerank_enabled is None else bool(rerank_enabled)
        if rerank_on:
            t_rerank_start = time.perf_counter()
            try:
                results = self.reranker.rerank(query_text, results)
            except Exception as e:
                # Never hard-fail a query because reranking failed.
                print(f"[WARN] Rerank failed; returning un-reranked results. Error: {e}")
            timings["rerank_ms"] = (time.perf_counter() - t_rerank_start) * 1000.0

        # Stage 2: diversity / de-duplication (implicit-first)
        diversity_cfg = retrieval_config.get("diversity", {})
        # Auto-enable diversity if max_per_key is explicitly set via parameter
        if diversity_enabled is None and diversity_max_per_key is not None:
            diversity_on = True
        else:
            diversity_on = diversity_cfg.get("enabled", False) if diversity_enabled is None else bool(diversity_enabled)
        if diversity_on:
            key_priority = diversity_cfg.get(
                "key_priority", ["source_id", "zotero_key", "title"]
            )
            max_per_key = int(diversity_cfg.get("max_per_key", 2))
            if diversity_max_per_key is not None:
                max_per_key = int(diversity_max_per_key)
            results = apply_diversity(results, key_priority=key_priority, max_per_key=max_per_key)

        # Final slicing
        if rerank_on:
            top_n = rerank_config.get("top_n")
            limit = k_return if top_n is None else min(k_return, top_n)
            results = results[:limit]
        else:
            results = results[:k_return]

        expand_config = retrieval_config.get("expand", {})
        if expand_config.get("include_parent", False):
            t_expand_start = time.perf_counter()
            max_parents = expand_config.get("max_parents", 1)
            results = attach_parent_context(results, self.vector_store, max_parents=max_parents)
            timings["expand_ms"] = (time.perf_counter() - t_expand_start) * 1000.0

        timings["total_ms"] = (time.perf_counter() - t_total_start) * 1000.0
        telemetry_cfg = retrieval_config.get("telemetry", {})
        if telemetry_cfg.get("enabled", True):
            print(
                "[TIMING] "
                f"mode={mode} "
                + " ".join(f"{k}={v:.1f}" for k, v in timings.items())
            )
        return results
