#!/usr/bin/env python3
"""CLI parity surface for the MCP enumeration tools.

Subcommands mirror the MCP tools one-to-one and share the same underlying
logic and output formatting, so agent (MCP) and human (CLI) surfaces cannot
diverge:

  sources.py list     <-> list_sources       (registry-backed)
  sources.py chunks   <-> get_source_chunks  (direct Chroma enumeration)
  sources.py status   <-> index_status       (registry + Chroma health)

Use --json on any subcommand for machine-readable output.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.enumeration import build_source_chunks_payload
from src.mcp_formatters.formatters import (
    format_index_status,
    format_list_sources,
    format_source_chunks,
)
from src.registry import SourceRegistry, registry_path_for


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        print(f"[ERROR] Configuration file not found: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _open_registry(config: dict) -> SourceRegistry:
    return SourceRegistry(registry_path_for(config))


def cmd_list(args) -> None:
    config = _load_config(args.config)
    registry = _open_registry(config)

    if not registry.is_ready():
        print(
            "[ERROR] Source registry is empty. Run the one-time backfill first:\n"
            "  python scripts/build_registry.py"
        )
        sys.exit(1)

    payload = registry.list_sources_payload(
        source_type=args.source_type,
        title_contains=args.title_contains,
        author=args.author,
        collection=args.collection,
        limit=args.limit,
        offset=args.offset,
    )
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_list_sources(payload))


def cmd_chunks(args) -> None:
    config = _load_config(args.config)

    # Chroma is only needed for this subcommand; import lazily so `list` and
    # `status` work even when the vector store is down.
    from src.storage.chroma import ChromaVectorStore

    store = ChromaVectorStore(config)
    payload = build_source_chunks_payload(
        store.collection,
        zotero_key=args.zotero_key,
        source_path=args.source_path,
        chunk_level=args.chunk_level,
        include_text=not args.no_text,
        limit=args.limit,
        offset=args.offset,
    )
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_source_chunks(payload))


def cmd_status(args) -> None:
    config = _load_config(args.config)
    registry = _open_registry(config)
    registry_status = registry.status()

    payload = {
        "git_sha": "n/a (CLI)",
        "collection_name": str(
            config.get("storage", {}).get("collection_name", "research_library")
        ),
        "endpoint": str(config.get("storage", {}).get("endpoint", "unknown")),
        "chroma_chunk_count": 0,
        "registry": registry_status,
        "drift": 0,
    }

    if not args.no_chroma:
        try:
            from src.storage.chroma import ChromaVectorStore

            store = ChromaVectorStore(config)
            chroma_count = store.collection.count()
            payload["chroma_chunk_count"] = chroma_count
            payload["drift"] = chroma_count - registry_status.get("chunk_count", 0)
        except Exception as e:
            payload["chroma_error"] = str(e)
            print(f"[WARN] Could not reach ChromaDB: {e}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_index_status(payload))
        if "chroma_error" in payload:
            print("\n[WARN] Chroma counts unavailable; drift not computed.")


def main():
    parser = argparse.ArgumentParser(
        description="Enumerate indexed sources and chunks (CLI parity with MCP tools)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="List distinct indexed sources (mirrors list_sources)")
    p_list.add_argument(
        "--source-type",
        choices=["zotero", "zotero_fulltext", "zotero_note", "zotero_annotation", "obsidian"],
        default=None,
        help="Only sources with chunks of this type",
    )
    p_list.add_argument("--title-contains", default=None, help="Case-insensitive title substring filter")
    p_list.add_argument("--author", default=None, help="Case-insensitive author substring filter")
    p_list.add_argument(
        "--collection",
        default=None,
        help="Case-insensitive Zotero collection name substring (scope a register to one collection)",
    )
    p_list.add_argument("--limit", type=int, default=100, help="Max sources per page (default 100, max 500)")
    p_list.add_argument("--offset", type=int, default=0, help="Page offset after sorting")
    p_list.add_argument("--json", action="store_true", help="Emit raw JSON payload")
    p_list.set_defaults(func=cmd_list)

    p_chunks = subparsers.add_parser("chunks", help="Enumerate chunks of one source (mirrors get_source_chunks)")
    p_chunks.add_argument("--zotero-key", default=None, help="Exact Zotero item key")
    p_chunks.add_argument("--source-path", default=None, help="Exact source_id for Obsidian/local sources")
    p_chunks.add_argument(
        "--chunk-level",
        choices=["coarse", "mid", "fine", "atomic"],
        default=None,
        help="Optional granularity filter",
    )
    p_chunks.add_argument("--no-text", action="store_true", help="Census mode: ids + metadata only")
    p_chunks.add_argument("--limit", type=int, default=50, help="Max chunks per page (default 50, max 200)")
    p_chunks.add_argument("--offset", type=int, default=0, help="Page offset after stable sorting")
    p_chunks.add_argument("--json", action="store_true", help="Emit raw JSON payload")
    p_chunks.set_defaults(func=cmd_chunks)

    p_status = subparsers.add_parser("status", help="Index health summary (mirrors index_status)")
    p_status.add_argument(
        "--no-chroma",
        action="store_true",
        help="Skip the live ChromaDB count (registry-only status)",
    )
    p_status.add_argument("--json", action="store_true", help="Emit raw JSON payload")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
