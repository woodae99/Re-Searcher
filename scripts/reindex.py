#!/usr/bin/env python3
"""Targeted re-indexing for repair workflows.

Two modes:

  --obsidian-all
      Wipe every Obsidian chunk (vector store + registry + vault snapshot +
      progress records) and re-index the whole vault fresh. This removes ghost
      identities (deleted/renamed/case-variant notes), collapses duplicates,
      and stamps every note chunk with indexed_at + content versions.
      The vault is a small fraction of the corpus, so this is cheap.

  --zotero-keys KEY1,KEY2 / --zotero-keys-file FILE
      Delete and re-index specific Zotero items (one key per line in the
      file; '#' comments allowed). Use for duplicate-affected items or
      annotation-identity repairs flagged by the registry audit.

Both modes use the normal pipeline batch machinery, so they checkpoint per
document. If a run is interrupted, re-run with --skip-wipe to resume without
repeating the deletion step.
"""

import argparse
import sys
from pathlib import Path

import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline import ResearchRAGPipeline
from src.embedding.vllm_server import managed_embedding_backend


def _load_keys(args) -> list:
    keys = []
    if args.zotero_keys:
        keys.extend(k.strip() for k in args.zotero_keys.split(","))
    if args.zotero_keys_file:
        for line in Path(args.zotero_keys_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                keys.append(line)
    return sorted({k for k in keys if k})


def reindex_obsidian_all(pipeline: ResearchRAGPipeline, skip_wipe: bool) -> bool:
    if not skip_wipe:
        print("[INFO] Wiping all Obsidian chunks from the vector store...")
        pipeline.vector_store.delete_where({"source_type": "obsidian"})
        removed = pipeline.registry.delete_sources_like("source_id", "obsidian-%")
        pipeline.registry.clear_vault_state()
        forgotten = pipeline.progress.forget_with_prefix("obsidian-")
        print(f"[OK] Wiped: {removed:,} registry chunk rows, {forgotten:,} progress records.")
    else:
        print("[INFO] --skip-wipe: resuming re-index from existing progress.")

    print("[INFO] Fetching all vault notes...")
    documents = pipeline._fetch_all_documents(
        zotero_item_keys=[],  # skip Zotero entirely
        obsidian_relative_paths=None,  # full vault
    )
    if not documents:
        print("[WARN] No Obsidian documents found; nothing to do.")
        return True

    return _process(pipeline, documents)


def reindex_zotero_keys(pipeline: ResearchRAGPipeline, keys: list, skip_wipe: bool) -> bool:
    print(f"[INFO] Re-indexing {len(keys)} Zotero items.")
    if not skip_wipe:
        pipeline._delete_existing_zotero_chunks(keys)

    documents = pipeline._fetch_all_documents(
        zotero_item_keys=keys,
        obsidian_relative_paths=[],  # skip Obsidian entirely
    )
    if not documents:
        print(
            "[WARN] No documents fetched for the given keys. Old chunks (if any) "
            "have been deleted; keys may be deleted items or have no content."
        )
        pipeline._refresh_registry()
        return True

    pipeline.progress.forget_many([doc.doc_id for doc in documents])
    return _process(pipeline, documents)


def _process(pipeline: ResearchRAGPipeline, documents: list) -> bool:
    pipeline.progress.set_total_documents(len(documents))
    pipeline._overall_total_chunks = 0
    pipeline._overall_embedded = 0
    pipeline._overall_stored = 0

    print(f"[INFO] Processing {len(documents)} documents...")
    pipeline.progress_display.start()
    try:
        completed = pipeline._process_batches(documents)
    finally:
        pipeline.progress_display.stop()

    # Seed/refresh durable state for what was stored.
    pipeline._persist_vault_state(None)
    pipeline._refresh_registry()

    if completed:
        print("[OK] Re-index complete.")
    else:
        print(
            "[INFO] Re-index paused (stop requested). "
            "Re-run with --skip-wipe to resume."
        )
    return completed


def main():
    parser = argparse.ArgumentParser(
        description="Targeted re-indexing for repair workflows"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--obsidian-all",
        action="store_true",
        help="Wipe and re-index the entire Obsidian vault",
    )
    mode.add_argument(
        "--zotero-keys",
        default=None,
        help="Comma-separated Zotero item keys to delete and re-index",
    )
    mode.add_argument(
        "--zotero-keys-file",
        type=Path,
        default=None,
        help="File with one Zotero item key per line ('#' comments allowed)",
    )
    parser.add_argument(
        "--skip-wipe",
        action="store_true",
        help="Skip the deletion step (resume an interrupted run)",
    )
    parser.add_argument(
        "--plain-progress",
        action="store_true",
        help="Use plain text progress output",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        sys.exit(1)

    progress_mode = "plain" if args.plain_progress else "auto"
    config = yaml.safe_load(args.config.read_text()) or {}

    try:
        # Stand up the managed embedding backend (vLLM) for the reindex, then tear it
        # down. No-op unless provider==vllm with managed lifecycle.
        with managed_embedding_backend(config):
            pipeline = ResearchRAGPipeline(args.config, progress_mode=progress_mode)
            if args.obsidian_all:
                ok = reindex_obsidian_all(pipeline, args.skip_wipe)
            else:
                keys = _load_keys(args)
                if not keys:
                    print("[ERROR] No Zotero keys given.")
                    sys.exit(1)
                ok = reindex_zotero_keys(pipeline, keys, args.skip_wipe)
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted. Progress is checkpointed; re-run with --skip-wipe to resume.")
        sys.exit(130)

    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
