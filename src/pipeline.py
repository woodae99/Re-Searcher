"""Main pipeline for indexing research library into vector store."""

import json
import hashlib
import queue
import re
import sys
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
from .processing.id_utils import stable_chunk_id
from .processing.oversize_guard import create_oversize_guard
from .processing.quality_filter import create_quality_filter_guard
from .progress import IndexingStage, ProgressDisplay, create_progress_display
from .reconcile import WorkPlan, build_work_plan
from .registry import SourceRegistry, registry_path_for
from .retrieval.diversity import apply_diversity
from .retrieval.expand import attach_parent_context
from .retrieval.filters import apply_post_filters, build_where_filter
from .retrieval.survey import aggregate_hits_by_source
from .run_reporting import RunReporter
from .sources.base import ProgressCallback
from .sources.obsidian import ObsidianSource
from .sources.zotero import ZoteroSource
from .storage.chroma import ChromaVectorStore
from .durable_write import write_json_durable


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
        self.reporter = RunReporter.from_config(self.config, self.output_dir)

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
        self.chunking_mode = self.config.get("chunking", {}).get(
            "mode", "v0.6_single_grain"
        )
        self.oversize_guard = create_oversize_guard(self.config)
        self.quality_filter = create_quality_filter_guard(
            self.config, reporter=self.reporter
        )
        if hasattr(self.oversize_guard, "set_reporter"):
            self.oversize_guard.set_reporter(self.reporter)
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
        self._overall_total_chunks = 0
        self._overall_embedded = 0
        self._overall_stored = 0

        # Source registry: SQLite mirror of source/chunk identity, updated in
        # the same code paths as vector-store writes so enumeration surfaces
        # (list_sources, CLI, index status) never scan collection metadata.
        self.registry = SourceRegistry(registry_path_for(self.config))

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

    def _report_event(
        self,
        *,
        stage: str,
        severity: str,
        remediation: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
        text_length: Optional[int] = None,
        token_estimate: Optional[int] = None,
        exception: Optional[BaseException] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        reporter = getattr(self, "reporter", None)
        if reporter is None:
            return
        reporter.record(
            stage=stage,
            severity=severity,
            remediation=remediation,
            message=message,
            metadata=metadata,
            document_id=document_id,
            chunk_id=chunk_id,
            text_length=text_length,
            token_estimate=token_estimate,
            exception=exception,
            extra=extra,
        )

    def _report_exception(
        self,
        *,
        stage: str,
        remediation: str,
        message: str,
        exception: BaseException,
        metadata: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
        text_length: Optional[int] = None,
        token_estimate: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        reporter = getattr(self, "reporter", None)
        if reporter is None:
            return
        reporter.record_exception(
            stage=stage,
            remediation=remediation,
            message=message,
            exception=exception,
            metadata=metadata,
            document_id=document_id,
            chunk_id=chunk_id,
            text_length=text_length,
            token_estimate=token_estimate,
            extra=extra,
        )

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
                self._report_event(
                    stage="source_fetch",
                    severity="error",
                    remediation="source_data",
                    message=f"{source_name} item fetch failed",
                    metadata=event,
                    extra=event,
                )

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
                self._report_event(
                    stage="extraction",
                    severity="error",
                    remediation="extraction_quality",
                    message=f"{source_name} attachment extraction failed",
                    metadata=event,
                    extra=event,
                )

            elif event_type in {"extraction_reject", "extraction_escalate"}:
                self._report_event(
                    stage="extraction_quality_gate",
                    severity="error" if event_type == "extraction_reject" else "warn",
                    remediation="extraction_quality",
                    message=f"{source_name} extraction {event.get('action', 'failed')}",
                    metadata=event,
                    text_length=event.get("text_length"),
                    extra=event,
                )

            elif event_type == "extraction_warning":
                self._report_event(
                    stage="extraction_quality_gate",
                    severity="warn",
                    remediation="extraction_quality",
                    message=f"{source_name} extraction warning",
                    metadata=event,
                    text_length=event.get("text_length"),
                    extra=event,
                )

        return callback

    def _initialize_sources(self) -> List:
        """Initialize all enabled data sources."""
        sources = []

        # Zotero source
        zotero_callback = self._create_source_progress_callback("Zotero")
        zotero = ZoteroSource(self.config, progress_callback=zotero_callback)
        if zotero.is_enabled() and zotero.validate_config():
            sources.append(zotero)
            print("[OK] Zotero source enabled", file=sys.stderr)

        # Obsidian source
        obsidian_callback = self._create_source_progress_callback("Obsidian")
        obsidian = ObsidianSource(self.config, progress_callback=obsidian_callback)
        if obsidian.is_enabled() and obsidian.validate_config():
            sources.append(obsidian)
            print("[OK] Obsidian source enabled", file=sys.stderr)

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
            obsidian_delta: Optional[Dict[str, Any]] = None
            obsidian_paths_to_fetch: Optional[List[str]] = None
            if force_reindex:
                print("[INFO] --force enabled: resetting index state and vector store")
                self._reset_index_state()
            elif resume_incomplete:
                # Resume mode should prioritize checkpoint continuity over delta
                # re-discovery, which can otherwise fan out to broad "changed"
                # sets when delta state is missing/stale.
                if self._ledger_execution_enabled():
                    print("[INFO] Resume mode: re-planning from the ledger and continuing.")
                    self._run_ledger_work_plan()
                    return
                print("[INFO] Resume mode: skipping delta change discovery and continuing from checkpoint.")
            elif self._ledger_execution_enabled():
                self._run_ledger_work_plan()
                return
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
                self._run_ledger_shadow(delta_changes)
                if changed_zotero_keys:
                    print(f"[INFO] Delta mode: {len(changed_zotero_keys)} changed Zotero items detected.")
                else:
                    print("[INFO] Delta mode: no Zotero changes detected.")

                # Obsidian per-file delta: new/changed/deleted notes from the
                # vault snapshot (or registry freshness on bootstrap).
                obsidian_delta = self._collect_obsidian_delta_changes()
                if obsidian_delta is not None:
                    obsidian_paths_to_fetch = obsidian_delta["changed"]
                    print(
                        f"[INFO] Obsidian delta: {len(obsidian_delta['changed'])} new/changed, "
                        f"{len(obsidian_delta['deleted'])} deleted."
                    )

                # Per-source hash fallback. Only a *Zotero* hash change widens
                # the Zotero scan; Obsidian is covered exhaustively by the
                # file-state diff, and a config edit alone should never
                # trigger a multi-hour re-fetch.
                current_hashes = self._compute_source_hashes()
                previous_hashes = self._load_source_hashes()
                if (
                    "config" in previous_hashes
                    and previous_hashes["config"] != current_hashes["config"]
                ):
                    print(
                        "[WARN] config.yaml changed since the last run. Metadata "
                        "filters pick this up automatically, but chunking/embedding "
                        "changes need a --force re-index."
                    )
                if (
                    not changed_zotero_keys
                    and "zotero" in previous_hashes
                    and previous_hashes["zotero"] != current_hashes["zotero"]
                ):
                    print("[WARN] Delta reported no changed keys but the Zotero DB changed; falling back to full Zotero scan.")
                    changed_zotero_keys = None

                # If no changes anywhere, persist watermarks and exit early.
                obsidian_has_work = obsidian_delta is not None and (
                    obsidian_delta["changed"] or obsidian_delta["deleted"]
                )
                if changed_zotero_keys == [] and not obsidian_has_work:
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
                    self._save_source_hash()
                    # Seed the vault snapshot on bootstrap so future runs diff
                    # against it even though nothing needed indexing today.
                    self._persist_vault_state(obsidian_delta)
                    print("[INFO] No changes detected. Use --force to re-index anyway.")
                    return
            elif not self._needs_reindex() and not resume_incomplete:
                print("[INFO] Index is up to date. Use --force to re-index anyway.")
                return

            # Remove chunks for vault notes that were deleted or changed
            # (changed notes are re-indexed below; delete-first avoids stale
            # chunk IDs surviving a content change).
            if obsidian_delta is not None:
                if obsidian_delta["deleted"]:
                    print(f"[INFO] Removing {len(obsidian_delta['deleted'])} deleted Obsidian notes from the index.")
                    self._delete_obsidian_sources(obsidian_delta["deleted"], removing=True)
                if obsidian_delta["changed"]:
                    self._delete_obsidian_sources(obsidian_delta["changed"])

            # Replace old chunks for changed/deleted Zotero items. This runs
            # before fetching so that pure deletions (which produce no
            # documents) are still applied to the index.
            if changed_zotero_keys:
                self._delete_existing_zotero_chunks(changed_zotero_keys)

            # Stage 2: Fetching documents
            self.progress_display.set_stage(IndexingStage.FETCHING, 2, 4)
            self.progress_display.set_activity("Fetching documents from sources...")
            documents = self._fetch_all_documents(
                zotero_item_keys=changed_zotero_keys,
                obsidian_relative_paths=obsidian_paths_to_fetch,
            )

            if not documents:
                # Deletions (Zotero items removed, vault notes removed) are
                # already applied above; persist watermarks so they are not
                # re-detected, and keep the registry aggregates in step.
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
                    self._save_source_hash()
                    self._persist_vault_state(obsidian_delta)
                    self._refresh_registry()
                    self._seed_ledger_from_world()
                    print("[INFO] No documents to re-index; deletions (if any) have been applied.")
                else:
                    print("[WARNING] No documents to index!")
                return

            if changed_zotero_keys:
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
                self._refresh_registry()
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

            # Record which vault files are now reliably indexed, then keep
            # registry aggregates in step with this run's writes.
            self._persist_vault_state(obsidian_delta)
            self._refresh_registry()
            # Mirror the indexed world into the ledger so ledger.shadow parity
            # is meaningful on the next run (legacy path doesn't self-record).
            self._seed_ledger_from_world()

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
            reporter = getattr(self, "reporter", None)
            if reporter is not None:
                reporter.write_summary()
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

            # Filter out already processed documents (version-aware: a doc
            # whose source content changed is never skipped as stored)
            pending_docs = [
                doc
                for doc in batch
                if not self.progress.has_completed_status(
                    doc.doc_id, doc.metadata.get("content_version")
                )
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
                        self._report_batch_failure(
                            "embedding",
                            "embedder_limit",
                            "Embedding/store pipeline failed",
                            e,
                            pending_docs,
                            chunks,
                            chunk_metadatas,
                            chunk_ids,
                        )
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
                        self._report_batch_failure(
                            "embedding",
                            "embedder_limit",
                            "Embedding failed",
                            e,
                            pending_docs,
                            chunks,
                            chunk_metadatas,
                            chunk_ids,
                        )
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
                        self._report_batch_failure(
                            "storage",
                            "vector_store",
                            "Vector-store write failed",
                            e,
                            pending_docs,
                            chunks,
                            chunk_metadatas,
                            chunk_ids,
                        )
                        for doc in pending_docs:
                            self.progress.set_document_status(
                                doc.doc_id,
                                DocumentStatus.ERROR,
                                error_msg=str(e),
                            )
                        continue

                # Update progress: documents stored (with the content version
                # that was stored, so future runs can detect changes)
                for doc in pending_docs:
                    self.progress.set_document_status(
                        doc.doc_id,
                        DocumentStatus.STORED,
                        content_version=doc.metadata.get("content_version"),
                    )

            except Exception as e:
                self._report_batch_failure(
                    "chunking",
                    "code_bug",
                    "Batch processing failed",
                    e,
                    pending_docs,
                )
                for doc in pending_docs:
                    self.progress.set_document_status(
                        doc.doc_id,
                        DocumentStatus.ERROR,
                        error_msg=str(e),
                    )

        return True

    def _report_batch_failure(
        self,
        stage: str,
        remediation: str,
        message: str,
        exception: BaseException,
        docs: List[Any],
        chunks: Optional[List[str]] = None,
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        chunks = chunks or []
        metadatas = metadatas or []
        ids = ids or []
        if chunks and metadatas:
            for idx, (text, metadata) in enumerate(zip(chunks, metadatas)):
                self._report_exception(
                    stage=stage,
                    remediation=remediation,
                    message=message,
                    exception=exception,
                    metadata=metadata,
                    document_id=metadata.get("source_id"),
                    chunk_id=ids[idx] if idx < len(ids) else None,
                    text_length=len(text),
                    token_estimate=len(text) // 4,
                )
            return

        for doc in docs:
            self._report_exception(
                stage=stage,
                remediation=remediation,
                message=message,
                exception=exception,
                metadata=getattr(doc, "metadata", {}),
                document_id=getattr(doc, "doc_id", None),
                text_length=len(getattr(doc, "content", "") or ""),
            )

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

    def _fetch_all_documents(
        self,
        zotero_item_keys: Optional[List[str]] = None,
        obsidian_relative_paths: Optional[List[str]] = None,
    ) -> List:
        """Fetch documents from all sources.

        For both parameters, None means "fetch everything" and a list means
        "fetch only these" (an empty list fetches nothing from that source).
        """
        all_documents = []
        zotero_cfg = self.config.get("zotero", {}) or {}
        zotero_strict = bool(zotero_cfg.get("fail_if_no_documents", True))

        for source in self.sources:
            source_name = source.__class__.__name__
            self.progress_display.set_activity(f"Fetching from {source_name}...")
            try:
                if isinstance(source, ZoteroSource) and zotero_item_keys is not None:
                    if not zotero_item_keys:
                        continue
                    documents = list(source.fetch_documents(item_keys=zotero_item_keys))
                elif isinstance(source, ObsidianSource) and obsidian_relative_paths is not None:
                    if not obsidian_relative_paths:
                        continue
                    documents = list(
                        source.fetch_documents(relative_paths=obsidian_relative_paths)
                    )
                else:
                    documents = list(source.fetch_documents())

                if (
                    isinstance(source, ZoteroSource)
                    and not documents
                    and zotero_item_keys is None
                ):
                    # Only suspicious on a full scan; a targeted delta fetch
                    # legitimately returns nothing when every changed key was
                    # a deletion.
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
                self._report_exception(
                    stage="source_fetch",
                    remediation="source_data",
                    message=f"{source_name} fetch failed",
                    exception=e,
                    extra={"source": source_name},
                )
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
                self._report_exception(
                    stage="chunking",
                    remediation="chunking",
                    message="Document chunking failed",
                    exception=e,
                    metadata=doc.metadata,
                    document_id=doc.doc_id,
                    text_length=len(doc.content or ""),
                    token_estimate=len(doc.content or "") // 4,
                )

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

            self._record_registry_chunks(ids, metadatas)
        except Exception as e:
            print(f"[ERROR] Error storing embeddings: {e}")
            raise

    def _record_registry_chunks(self, ids: List[str], metadatas: List[Dict[str, Any]]):
        """Mirror stored chunks into the source registry (never fails indexing)."""
        try:
            self.registry.record_chunks(ids, metadatas)
        except Exception as e:
            print(
                f"[WARN] Registry update failed; registry may drift from the vector "
                f"store until 'python scripts/build_registry.py' is re-run. Error: {e}"
            )
            for idx, metadata in enumerate(metadatas):
                self._report_exception(
                    stage="registry_write",
                    remediation="registry",
                    message="Registry chunk mirror failed after vector-store write",
                    exception=e,
                    metadata=metadata,
                    document_id=metadata.get("source_id"),
                    chunk_id=ids[idx] if idx < len(ids) else None,
                )

    def _refresh_registry(self):
        """Rebuild registry source aggregates after a run's writes/deletes."""
        try:
            self.registry.refresh_sources()
            self.registry.set_meta(
                "last_index_run_at",
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            )
        except Exception as e:
            print(f"[WARN] Registry refresh failed: {e}")
            self._report_exception(
                stage="registry_refresh",
                remediation="registry",
                message="Registry aggregate refresh failed",
                exception=e,
            )

    def _seed_ledger_from_world(self) -> None:
        """Mirror current source state into the ledger for indexed identities.

        The ledger execution path records units as it runs; the legacy delta path
        does not. Without this, a legacy-mode run leaves `index_units` empty, so
        `ledger.shadow` reconciles against an empty ledger and reports the whole
        corpus as 'create' — useless for parity. Recording the current
        enumerate_state for identities that have chunks makes shadow parity
        meaningful on the next run. Call after `_refresh_registry()` so the
        indexed-identity set is current. This exists only for old shadow-mode
        diagnostics; v0.6 production executes from the ledger directly.
        """
        try:
            indexed = self.registry.indexed_identities()
            units = [
                unit
                for source in self.sources
                if source.is_enabled()
                for unit in source.enumerate_state().values()
                if (unit.identity_field, unit.identity_value) in indexed
            ]
            self._record_ledger_unit_states(units)
            print(f"[INFO] Ledger mirror updated for {len(units)} indexed units.")
        except Exception as e:
            print(f"[WARN] Ledger seed-from-world failed: {e}")
            self._report_exception(
                stage="ledger_seed",
                remediation="ledger",
                message="Ledger seed-from-world failed",
                exception=e,
            )

    def _delta_cfg(self) -> Dict[str, Any]:
        return self.config.get("indexing", {}).get("delta", {}) or {}

    def _delta_enabled(self) -> bool:
        return bool(self._delta_cfg().get("enabled", False))

    def _ledger_cfg(self) -> Dict[str, Any]:
        return self.config.get("indexing", {}).get("ledger", {}) or {}

    def _ledger_execution_enabled(self) -> bool:
        return bool(self._ledger_cfg().get("execute", True))

    def _ledger_shadow_enabled(self) -> bool:
        return bool(self._ledger_cfg().get("shadow", False))

    def _run_ledger_shadow(self, delta_changes: Dict[str, Any]) -> None:
        """Log the ledger reconciler's plan beside the current delta decision.

        P2 is a parity gate: this computes the new planner's answer but never
        lets it drive deletes, fetches, embeds, or watermarks.
        """
        if not self._ledger_shadow_enabled():
            return

        try:
            plan = build_work_plan(self.sources, self.registry)
            print(
                "[INFO] Ledger shadow: "
                f"{len(plan.creates)} creates, {len(plan.updates)} updates, "
                f"{len(plan.deletes)} deletes, {plan.unchanged} unchanged units."
            )

            ledger_zotero_touched = {
                value
                for field, value in plan.touched_identities()
                if field == "zotero_key"
            }
            ledger_zotero_deletes = {
                parent
                for parent in (
                    self._zotero_parent_from_unit_id(unit_id)
                    for unit_id in plan.deletes
                )
                if parent
            }
            delta_zotero_keys = set(delta_changes.get("changed_item_keys") or [])
            comparable_delta_keys = delta_zotero_keys - ledger_zotero_deletes

            missing = sorted(comparable_delta_keys - ledger_zotero_touched)
            extra = sorted(ledger_zotero_touched - comparable_delta_keys)
            if missing or extra:
                print(
                    "[WARN] Ledger shadow parity divergence: "
                    f"delta_only={self._sample_list(missing)}, "
                    f"ledger_only={self._sample_list(extra)}"
                )
            elif delta_zotero_keys or ledger_zotero_touched:
                print(
                    "[INFO] Ledger shadow parity: Zotero modify/create parent set "
                    "matches current delta path."
                )

            if ledger_zotero_deletes:
                print(
                    "[INFO] Ledger shadow deletions: "
                    f"{len(ledger_zotero_deletes)} Zotero parent(s) absent from world "
                    f"(sample={self._sample_list(sorted(ledger_zotero_deletes))})."
                )
        except Exception as e:
            print(f"[WARN] Ledger shadow reconciliation failed: {e}")
            self._report_exception(
                stage="ledger_reconciliation",
                remediation="ledger",
                message="Ledger shadow reconciliation failed",
                exception=e,
            )

    @staticmethod
    def _zotero_parent_from_unit_id(unit_id: str) -> Optional[str]:
        parts = str(unit_id).split(":")
        if len(parts) >= 3 and parts[0] == "zotero":
            return parts[1]
        return None

    @staticmethod
    def _sample_list(values: List[str], limit: int = 10) -> List[str]:
        if len(values) <= limit:
            return values
        return [*values[:limit], f"...(+{len(values) - limit} more)"]

    def _run_ledger_work_plan(self) -> None:
        """Execute an incremental update from the registry-ledger work plan."""
        plan = build_work_plan(self.sources, self.registry)
        print(
            "[INFO] Ledger mode: "
            f"{len(plan.creates)} creates, {len(plan.updates)} updates, "
            f"{len(plan.deletes)} deletes, {plan.unchanged} unchanged units."
        )

        if plan.is_empty():
            self._save_source_hash()
            self._refresh_registry()
            print("[INFO] Ledger mode: no changes detected. Use --force to re-index anyway.")
            return

        self._execute_ledger_deletes(plan)
        documents, units_by_id = self._fetch_ledger_documents(plan)

        if documents:
            self.progress.forget_many([doc.doc_id for doc in documents])
            self.progress.set_total_documents(len(documents))
            self._overall_total_chunks = 0
            self._overall_embedded = 0
            self._overall_stored = 0

            self.progress_display.set_stage(IndexingStage.CHUNKING, 3, 4)
            self.progress_display.set_activity(
                f"Processing {len(documents)} ledger-planned documents in batches..."
            )
            completed_run = self._process_batches(documents)
            if not completed_run:
                self._refresh_registry()
                print(
                    "[INFO] Ledger indexing paused after completing the current batch. "
                    "Run the same command again to resume."
                )
                return

            self._record_ledger_units_for_documents(documents, units_by_id)
        else:
            print("[INFO] Ledger mode: no text documents need embedding.")

        self._apply_ledger_metadata_updates(plan)
        self._save_source_hash()
        self._refresh_registry()

        self.progress_display.set_stage(IndexingStage.COMPLETE, 4, 4)
        self.progress_display.set_activity("Ledger indexing complete!")
        stats = self.vector_store.get_collection_stats()
        print(f"\nCollection: {stats.get('collection_name')}")
        print(f"Total documents: {stats.get('document_count')}")
        print(f"Endpoint: {stats.get('endpoint')}")

    def _execute_ledger_deletes(self, plan: WorkPlan) -> None:
        """Apply delete-before-replace operations required by the work plan."""
        zotero_delete_groups = self._group_zotero_deletes(plan.deletes)
        zotero_changed_groups = self._group_zotero_states([*plan.creates, *plan.updates])

        for relative_path in self._obsidian_delete_paths(plan):
            self._delete_obsidian_sources([relative_path], removing=True)

        for relative_path in self._obsidian_update_paths(plan):
            self._delete_obsidian_sources([relative_path])

        full_deleted_parents = {
            parent
            for parent, group in zotero_delete_groups.items()
            if group.get("parent_meta")
        }
        for parent_key in sorted(full_deleted_parents):
            self._delete_existing_zotero_chunks([parent_key])

        for parent_key, group in zotero_delete_groups.items():
            if parent_key in full_deleted_parents:
                continue
            for attachment_key in sorted(group.get("attachment", set())):
                self._delete_zotero_attachment_chunks(parent_key, attachment_key)
            if group.get("note") or group.get("annotation"):
                self._delete_zotero_note_annotation_chunks(parent_key)

        for parent_key, group in zotero_changed_groups.items():
            if parent_key in full_deleted_parents:
                continue
            for attachment_key in sorted(group.get("attachment", set())):
                self._delete_zotero_attachment_chunks(parent_key, attachment_key)
            if group.get("note") or group.get("annotation"):
                self._delete_zotero_note_annotation_chunks(parent_key)

        deleted_unit_ids = [
            unit_id
            for unit_id in plan.deletes
            if not (
                unit_id.startswith("zotero:")
                and self._zotero_parent_from_unit_id(unit_id) in full_deleted_parents
            )
        ]
        try:
            self.registry.delete_units(deleted_unit_ids)
        except Exception as e:
            print(f"[WARN] Ledger delete-unit cleanup failed: {e}")
            self._report_exception(
                stage="ledger_write",
                remediation="ledger",
                message="Ledger delete-unit cleanup failed",
                exception=e,
                extra={"unit_ids": deleted_unit_ids[:20]},
            )

    def _fetch_ledger_documents(self, plan: WorkPlan) -> tuple[List[Any], Dict[str, Any]]:
        """Fetch only documents needed by the ledger work plan."""
        documents: List[Any] = []
        units_by_id = {unit.unit_id: unit for unit in (*plan.creates, *plan.updates)}

        obsidian_paths = self._obsidian_create_update_paths(plan)
        if obsidian_paths:
            source = self._get_obsidian_source()
            if source is not None:
                documents.extend(source.fetch_documents(relative_paths=obsidian_paths))

        zotero_source = self._get_zotero_source()
        if zotero_source is not None:
            changed_groups = self._group_zotero_states([*plan.creates, *plan.updates])
            delete_groups = self._group_zotero_deletes(plan.deletes)
            for parent_key in sorted(set(changed_groups) | set(delete_groups)):
                if delete_groups.get(parent_key, {}).get("parent_meta"):
                    continue
                group = changed_groups.get(parent_key, {})
                deleted = delete_groups.get(parent_key, {})

                attachment_keys = set(group.get("attachment", set()))
                if attachment_keys:
                    documents.extend(
                        zotero_source.fetch_item_documents(
                            parent_key,
                            kinds={"attachment"},
                            attachment_keys=attachment_keys,
                        )
                    )

                if (
                    group.get("note")
                    or group.get("annotation")
                    or deleted.get("note")
                    or deleted.get("annotation")
                ):
                    documents.extend(
                        zotero_source.fetch_item_documents(
                            parent_key,
                            kinds={"note", "annotation"},
                        )
                    )

        deduped = list({doc.doc_id: doc for doc in documents}.values())
        return deduped, units_by_id

    def _record_ledger_units_for_documents(
        self,
        documents: List[Any],
        units_by_id: Dict[str, Any],
    ) -> None:
        unit_ids = {
            unit_id
            for doc in documents
            for unit_id in [self._unit_id_for_document(doc)]
            if unit_id in units_by_id
        }
        self._record_ledger_unit_states([units_by_id[unit_id] for unit_id in sorted(unit_ids)])

    def _apply_ledger_metadata_updates(self, plan: WorkPlan) -> None:
        """Refresh source metadata for parent_meta units without embedding."""
        zotero_source = self._get_zotero_source()
        if zotero_source is None:
            return

        deleted_parents = {
            parent
            for parent, group in self._group_zotero_deletes(plan.deletes).items()
            if group.get("parent_meta")
        }
        parent_meta_units = [
            unit
            for unit in (*plan.creates, *plan.updates)
            if unit.identity_field == "zotero_key"
            and unit.unit_kind == "parent_meta"
            and unit.identity_value not in deleted_parents
        ]
        for unit in parent_meta_units:
            fresh = self._fetch_zotero_metadata_base(zotero_source, unit.identity_value)
            if fresh is None:
                continue
            records = self.registry.chunk_records_for_source("zotero_key", unit.identity_value)
            ids = [record["chunk_id"] for record in records]
            if ids:
                stored = self.vector_store.get_by_ids(ids)
                existing_by_id = {doc_id: metadata for doc_id, _text, metadata in stored}
                metadatas = [
                    self._merge_source_metadata(existing_by_id.get(chunk_id, {}), fresh)
                    for chunk_id in ids
                ]
                self.vector_store.update_metadata(ids, metadatas)
                self.registry.record_chunks(ids, metadatas)
            self._record_ledger_unit_states([unit])

    def _fetch_zotero_metadata_base(
        self, source: ZoteroSource, item_key: str
    ) -> Optional[Dict[str, Any]]:
        conn = source._get_db_connection()
        if not conn:
            return None
        try:
            rows = source._get_items_by_keys(conn, [item_key])
            if not rows:
                return None
            return source._get_item_metadata(conn, rows[0]["itemID"])
        finally:
            conn.close()

    @staticmethod
    def _merge_source_metadata(
        existing: Dict[str, Any],
        fresh_source_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        chunk_specific = {
            "source_type",
            "source_id",
            "chunk_level",
            "chunk_index",
            "chunk_id_variant",
            "attachment_id",
            "attachment_key",
            "note_id",
            "note_key",
            "annotation_id",
            "annotation_key",
            "file_name",
            "file_path",
            "content_type",
            "text_source",
            "fulltext_available",
            "fulltext_partial_fallback",
            "page",
        }
        merged = dict(existing or {})
        for key, value in fresh_source_metadata.items():
            if key not in chunk_specific:
                merged[key] = value
        return merged

    def _record_ledger_unit_states(self, units: List[Any]) -> None:
        if not units:
            return
        indexed_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        rows = [
            {
                "unit_id": unit.unit_id,
                "identity_field": unit.identity_field,
                "identity_value": unit.identity_value,
                "unit_kind": unit.unit_kind,
                "source_fingerprint": unit.fingerprint,
                "indexed_at": indexed_at,
            }
            for unit in units
        ]
        try:
            self.registry.record_unit_states(rows)
        except Exception as e:
            print(f"[WARN] Ledger unit-state update failed: {e}")
            self._report_exception(
                stage="ledger_write",
                remediation="ledger",
                message="Ledger unit-state update failed",
                exception=e,
                extra={"unit_ids": [row["unit_id"] for row in rows[:20]]},
            )

    def _delete_zotero_attachment_chunks(
        self, parent_key: str, attachment_key: str
    ) -> None:
        where = {
            "$and": [
                {"zotero_key": parent_key},
                {"source_type": "zotero_fulltext"},
                {"attachment_key": attachment_key},
            ]
        }
        self.vector_store.delete_where(where)
        self.registry.delete_chunks_matching(
            "zotero_key",
            parent_key,
            source_types=["zotero_fulltext"],
            attachment_key=attachment_key,
        )

    def _delete_zotero_note_annotation_chunks(self, parent_key: str) -> None:
        source_types = ["zotero_note", "zotero_annotation"]
        self.vector_store.delete_where(
            {
                "$and": [
                    {"zotero_key": parent_key},
                    {"source_type": {"$in": source_types}},
                ]
            }
        )
        self.registry.delete_chunks_matching(
            "zotero_key",
            parent_key,
            source_types=source_types,
        )

    def _obsidian_create_update_paths(self, plan: WorkPlan) -> List[str]:
        return sorted(
            unit.identity_value[len("obsidian-") :]
            for unit in (*plan.creates, *plan.updates)
            if unit.unit_kind == "vault_file"
            and unit.identity_field == "source_id"
            and unit.identity_value.startswith("obsidian-")
        )

    def _obsidian_update_paths(self, plan: WorkPlan) -> List[str]:
        return sorted(
            unit.identity_value[len("obsidian-") :]
            for unit in plan.updates
            if unit.unit_kind == "vault_file"
            and unit.identity_field == "source_id"
            and unit.identity_value.startswith("obsidian-")
        )

    def _obsidian_delete_paths(self, plan: WorkPlan) -> List[str]:
        prefix = "obsidian:"
        return sorted(
            unit_id[len(prefix) :]
            for unit_id in plan.deletes
            if unit_id.startswith(prefix)
        )

    def _group_zotero_states(self, units: List[Any]) -> Dict[str, Dict[str, set[str]]]:
        groups: Dict[str, Dict[str, set[str]]] = {}
        for unit in units:
            if unit.identity_field != "zotero_key":
                continue
            group = groups.setdefault(
                unit.identity_value,
                {"parent_meta": set(), "attachment": set(), "note": set(), "annotation": set()},
            )
            if unit.unit_kind == "parent_meta":
                group["parent_meta"].add(unit.unit_id)
                continue
            child_key = self._child_key_from_unit_id(unit.unit_id)
            if child_key and unit.unit_kind in group:
                group[unit.unit_kind].add(child_key)
        return groups

    def _group_zotero_deletes(self, unit_ids: List[str]) -> Dict[str, Dict[str, set[str]]]:
        groups: Dict[str, Dict[str, set[str]]] = {}
        for unit_id in unit_ids:
            parsed = self._parse_zotero_unit_id(unit_id)
            if parsed is None:
                continue
            parent_key, kind, child_key = parsed
            group = groups.setdefault(
                parent_key,
                {"parent_meta": set(), "attachment": set(), "note": set(), "annotation": set()},
            )
            if kind == "parent_meta":
                group["parent_meta"].add(unit_id)
            elif child_key and kind in group:
                group[kind].add(child_key)
        return groups

    @staticmethod
    def _parse_zotero_unit_id(unit_id: str) -> Optional[tuple[str, str, Optional[str]]]:
        parts = str(unit_id).split(":")
        if len(parts) == 3 and parts[0] == "zotero" and parts[2] == "meta":
            return parts[1], "parent_meta", None
        if len(parts) == 4 and parts[0] == "zotero":
            return parts[1], parts[2], parts[3]
        return None

    @staticmethod
    def _child_key_from_unit_id(unit_id: str) -> Optional[str]:
        parts = str(unit_id).split(":")
        if len(parts) == 4:
            return parts[3]
        return None

    @staticmethod
    def _unit_id_for_document(doc: Any) -> Optional[str]:
        metadata = doc.metadata or {}
        source_type = metadata.get("source_type")
        zotero_key = metadata.get("zotero_key")
        if source_type == "zotero_note" and zotero_key and metadata.get("note_key"):
            return f"zotero:{zotero_key}:note:{metadata['note_key']}"
        if source_type == "zotero_fulltext" and zotero_key and metadata.get("attachment_key"):
            return f"zotero:{zotero_key}:attachment:{metadata['attachment_key']}"
        if source_type == "zotero_annotation" and zotero_key and metadata.get("annotation_key"):
            return f"zotero:{zotero_key}:annotation:{metadata['annotation_key']}"
        if source_type == "obsidian" and metadata.get("relative_path"):
            return f"obsidian:{metadata['relative_path']}"
        return None

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
            "last_sqlite_attachment_storage_mod_time": int(
                sqlite_attachment_storage_mod_time or 0
            ),
        }
        write_json_durable(path, payload, indent=2)

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

    def _get_obsidian_source(self) -> Optional[ObsidianSource]:
        for source in self.sources:
            if isinstance(source, ObsidianSource):
                return source
        return None

    @staticmethod
    def _parse_indexed_at(stamp: str) -> Optional[datetime]:
        if not stamp:
            return None
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _collect_obsidian_delta_changes(self) -> Optional[Dict[str, Any]]:
        """Diff the vault against the registry's per-file snapshot.

        Returns {"changed": [...], "deleted": [...], "disk": {path: (mtime, size)},
        "bootstrap": bool}, or None when no Obsidian source is enabled.

        Normal mode compares against the vault_files snapshot from the last
        run. Bootstrap mode (no snapshot yet, e.g. first run after the
        registry backfill) falls back to comparing file mtimes against each
        source's last_indexed_at in the registry; notes whose chunks predate
        indexed_at stamps are assumed current — the registry audit's stale
        list is the repair path for those.
        """
        source = self._get_obsidian_source()
        if source is None:
            return None

        disk = source.get_file_states()
        snapshot = self.registry.get_vault_state()

        if snapshot:
            changed = sorted(
                path for path, state in disk.items() if snapshot.get(path) != state
            )
            deleted = sorted(path for path in snapshot if path not in disk)
            return {"changed": changed, "deleted": deleted, "disk": disk, "bootstrap": False}

        freshness = self.registry.obsidian_freshness()
        changed = []
        unknown_age = 0
        for path, (mtime, _size) in disk.items():
            if path not in freshness:
                changed.append(path)
                continue
            indexed_at = self._parse_indexed_at(freshness[path])
            if indexed_at is None:
                unknown_age += 1
                continue
            if datetime.fromtimestamp(mtime, tz=timezone.utc) > indexed_at:
                changed.append(path)
        deleted = sorted(path for path in freshness if path not in disk)
        if unknown_age:
            print(
                f"[INFO] Obsidian bootstrap: {unknown_age} indexed notes have no "
                f"indexed_at stamp; assuming current. Use the registry audit's "
                f"stale list to repair any that changed before this upgrade."
            )
        return {"changed": sorted(changed), "deleted": deleted, "disk": disk, "bootstrap": True}

    def _persist_vault_state(self, obsidian_delta: Optional[Dict[str, Any]]) -> None:
        """Record the vault snapshot for files that are now reliably indexed."""
        try:
            if obsidian_delta is not None:
                disk = obsidian_delta["disk"]
                changed = set(obsidian_delta["changed"])
                entries = {}
                for path, state in disk.items():
                    if path in changed:
                        doc_id = f"obsidian-{path}"
                        version = ObsidianSource.content_version_for_state(state)
                        if not self.progress.has_completed_status(doc_id, version):
                            continue
                    entries[path] = state
                self.registry.set_vault_state_entries(entries)
            elif self._delta_enabled():
                # Full-fetch run (resume / fallback): seed the snapshot for
                # every note whose document is stored, so the next run can
                # delta properly.
                source = self._get_obsidian_source()
                if source is None:
                    return
                disk = source.get_file_states()
                entries = {
                    path: state
                    for path, state in disk.items()
                    if self.progress.get_status(f"obsidian-{path}") == DocumentStatus.STORED
                }
                self.registry.set_vault_state_entries(entries)
        except Exception as e:
            print(f"[WARN] Failed to persist vault state: {e}")

    def _delete_existing_zotero_chunks(self, item_keys: List[str]) -> None:
        """Delete all indexed chunks for the given Zotero items, batched.

        There is deliberately no upper key limit: skipping deletes while still
        re-indexing creates stale duplicates (stable chunk IDs include content,
        so changed content gets new IDs and the old ones survive). A delete
        failure raises and aborts the run instead.

        The source_type guard keeps Obsidian notes that cite an item (and so
        carry its zotero_key in metadata) out of the deletion.
        """
        if not item_keys:
            return
        batch_size = max(1, int(self._delta_cfg().get("delete_batch_size", 100)))
        source_types = ["zotero", "zotero_note", "zotero_fulltext", "zotero_annotation"]
        total = len(item_keys)
        for i in range(0, total, batch_size):
            batch = item_keys[i : i + batch_size]
            self.vector_store.delete_where(
                {
                    "$and": [
                        {"zotero_key": {"$in": batch}},
                        {"source_type": {"$in": source_types}},
                    ]
                }
            )
            for key in batch:
                try:
                    self.registry.delete_source_chunks("zotero_key", key)
                except Exception as e:
                    print(f"[WARN] Registry delete failed for zotero_key={key}: {e}")
            if total > batch_size:
                print(f"[INFO] Deleted old chunks for {min(i + batch_size, total)}/{total} changed Zotero items")

    def _delete_obsidian_sources(
        self, relative_paths: List[str], *, removing: bool = False
    ) -> None:
        """Delete indexed chunks for vault notes (changed: replace; removing: gone).

        Clears the progress record so the note is re-indexed when changed, and
        drops the vault-state row when the note was deleted from disk.
        """
        if not relative_paths:
            return
        batch_size = 100
        source_ids = [f"obsidian-{path}" for path in relative_paths]
        for i in range(0, len(source_ids), batch_size):
            batch = source_ids[i : i + batch_size]
            if len(batch) == 1:
                where: Dict[str, Any] = {"source_id": batch[0]}
            else:
                where = {"source_id": {"$in": batch}}
            self.vector_store.delete_where(where)
            for source_id in batch:
                try:
                    self.registry.delete_source_chunks("source_id", source_id)
                except Exception as e:
                    print(f"[WARN] Registry delete failed for {source_id}: {e}")
                self.progress.forget_document(source_id)
        if removing:
            try:
                self.registry.delete_vault_state_entries(list(relative_paths))
            except Exception as e:
                print(f"[WARN] Vault-state cleanup failed: {e}")

    def _compute_source_hashes(self) -> Dict[str, str]:
        """Per-source change hashes, so one source's edits never trigger a
        full re-fetch of the others (an edited note used to force a full
        Zotero scan via the old combined hash)."""
        config_h = hashlib.md5()
        if self.config_path.exists():
            config_h.update(self.config_path.read_bytes())

        zotero_h = hashlib.md5()
        zotero_cfg = self.config.get("zotero", {})
        if zotero_cfg.get("enabled"):
            zotero_db = Path(zotero_cfg.get("data_directory", "")).expanduser() / "zotero.sqlite"
            if zotero_db.exists():
                zotero_h.update(str(zotero_db.stat().st_mtime).encode())

        obsidian_h = hashlib.md5()
        obsidian_cfg = self.config.get("obsidian", {})
        if obsidian_cfg.get("enabled"):
            vault_path = Path(obsidian_cfg.get("vault_path", "")).expanduser()
            if vault_path.exists():
                for md_file in sorted(vault_path.rglob("*.md")):
                    obsidian_h.update(str(md_file.stat().st_mtime).encode())

        return {
            "config": config_h.hexdigest(),
            "zotero": zotero_h.hexdigest(),
            "obsidian": obsidian_h.hexdigest(),
        }

    def _load_source_hashes(self) -> Dict[str, str]:
        """Load saved per-source hashes; legacy single-hash files return {}."""
        hash_file = self.output_dir / "source_hash.txt"
        if not hash_file.exists():
            return {}
        raw = hash_file.read_text().strip()
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return {str(k): str(v) for k, v in loaded.items()}
        except json.JSONDecodeError:
            pass
        return {}

    def _needs_reindex(self) -> bool:
        """Check if re-indexing is needed based on source changes."""
        previous = self._load_source_hashes()
        if not previous:
            # Missing or legacy-format hash file: assume changed once; the
            # version-keyed progress check prevents redundant re-embedding.
            return True
        current = self._compute_source_hashes()
        return any(previous.get(key) != value for key, value in current.items())

    def _save_source_hash(self):
        """Save current per-source hashes (durable)."""
        hash_file = self.output_dir / "source_hash.txt"
        write_json_durable(hash_file, self._compute_source_hashes(), indent=2)

    def _reset_index_state(self):
        """Reset progress and storage for a full re-index."""
        self.progress.clear()

        try:
            self.registry.reset()
        except Exception as e:
            print(f"[WARN] Registry reset failed: {e}")

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
            chunk_level: Filter by chunk level (v0.6: mid/atomic; legacy: coarse/fine)
            author_contains: Filter results where author field contains this string
            title_contains: Filter results where title field contains this string
            where: Advanced Chroma where clause for custom filtering

        Returns:
            List of search results as (doc_id, text, score, metadata) tuples
        """
        print(f"\nQuery: {query_text}\n", file=sys.stderr)
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
                + " ".join(f"{k}={v:.1f}" for k, v in timings.items()),
                file=sys.stderr,
            )
        return results

    def survey_sources(
        self,
        query_text: str,
        k: int = 20,
        *,
        retrieval_mode: Optional[str] = None,
        k_recall_override: Optional[int] = None,
        source_type: Optional[str] = None,
        zotero_key: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        chunk_level: Optional[str] = None,
        author_contains: Optional[str] = None,
        title_contains: Optional[str] = None,
        collection: Optional[str] = None,
        item_type: Optional[str] = None,
        doi: Optional[str] = None,
        language: Optional[str] = None,
        tag: Optional[str] = None,
        representative_limit: int = 3,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a broad survey by aggregating chunk hits into source rows."""
        print(f"\nSurvey query: {query_text}\n", file=sys.stderr)
        t_total_start = time.perf_counter()
        timings: Dict[str, float] = {}

        retrieval_config = self.config.get("retrieval", {})
        survey_config = retrieval_config.get("survey", {}) or {}
        k_recall_cfg = survey_config.get("k_recall", retrieval_config.get("k_recall", 50))
        k_recall = int(k_recall_override) if k_recall_override is not None else int(k_recall_cfg)
        survey_chunk_level = chunk_level or survey_config.get("chunk_level", "mid")

        mode_default = str(retrieval_config.get("mode_default", "fast")).lower()
        mode = str(retrieval_mode or mode_default).lower()
        if mode not in {"fast", "strict"}:
            mode = "fast"

        t_embed_start = time.perf_counter()
        query_embedding = self.embedder.embed_query(query_text)
        timings["embed_ms"] = (time.perf_counter() - t_embed_start) * 1000.0

        where_filter = build_where_filter(
            source_type=source_type,
            zotero_key=zotero_key,
            chunk_level=survey_chunk_level,
            extra_where=where,
        )
        if mode == "strict":
            where_filter = build_where_filter(
                source_type=source_type,
                zotero_key=zotero_key,
                year_min=year_min,
                year_max=year_max,
                chunk_level=survey_chunk_level,
                extra_where=where,
            )

        t_vector_start = time.perf_counter()
        results = self.vector_store.search(query_embedding, k=k_recall, filter=where_filter)
        timings["vector_ms"] = (time.perf_counter() - t_vector_start) * 1000.0

        t_post_filter_start = time.perf_counter()
        if (
            author_contains
            or title_contains
            or (mode == "fast" and (year_min is not None or year_max is not None))
        ):
            results = apply_post_filters(
                results,
                year_min=(year_min if mode == "fast" else None),
                year_max=(year_max if mode == "fast" else None),
                author_contains=author_contains,
                title_contains=title_contains,
            )
        timings["postfilter_ms"] = (time.perf_counter() - t_post_filter_start) * 1000.0

        t_aggregate_start = time.perf_counter()
        payload = aggregate_hits_by_source(
            results,
            self.registry,
            limit=k,
            representative_limit=representative_limit,
            source_type=source_type,
            title_contains=title_contains,
            author=author_contains,
            collection=collection,
            item_type=item_type,
            doi=doi,
            language=language,
            tag=tag,
        )
        timings["aggregate_ms"] = (time.perf_counter() - t_aggregate_start) * 1000.0
        timings["total_ms"] = (time.perf_counter() - t_total_start) * 1000.0

        payload["query"] = query_text
        payload["filters"] = {
            "source_type": source_type,
            "zotero_key": zotero_key,
            "year_min": year_min,
            "year_max": year_max,
            "chunk_level": survey_chunk_level,
            "author": author_contains,
            "title_contains": title_contains,
            "collection": collection,
            "item_type": item_type,
            "doi": doi,
            "language": language,
            "tag": tag,
        }
        payload["recall"] = {"k_recall": k_recall, "mode": mode}

        telemetry_cfg = retrieval_config.get("telemetry", {})
        if telemetry_cfg.get("enabled", True):
            print(
                "[TIMING] "
                f"mode={mode} survey=true "
                + " ".join(f"{key}={value:.1f}" for key, value in timings.items()),
                file=sys.stderr,
            )
        return payload
