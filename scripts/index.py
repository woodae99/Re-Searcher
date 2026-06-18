#!/usr/bin/env python3
"""CLI script for indexing research library into ChromaDB."""

import argparse
from pathlib import Path
import sys

import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline import ResearchRAGPipeline
from src.preflight import run_preflight, check_services
from src.embedding.vllm_server import managed_embedding_backend


def resolve_stop_flag_path(config: dict) -> Path:
    """Resolve the stop-after-batch flag path from config."""
    output_dir = Path(config.get("output_folder", "./output"))
    stop_cfg = config.get("indexing", {}).get("stop_after_batch", {}) or {}
    flag_path = Path(stop_cfg.get("flag_file", "stop_after_batch.flag"))
    if not flag_path.is_absolute():
        flag_path = output_dir / flag_path
    return flag_path


def main():
    parser = argparse.ArgumentParser(
        description="Index your research library (Zotero + Obsidian) into ChromaDB"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-indexing even if sources haven't changed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and print settings without running indexing",
    )
    parser.add_argument(
        "--allow-legacy-chunking",
        action="store_true",
        help="Deprecated no-op; legacy hierarchical chunking is retired in v0.6.",
    )
    parser.add_argument(
        "--allow-default-config",
        action="store_true",
        help="Allow running with missing chunking configuration block",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight validation (not recommended for production)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output (errors and warnings still shown)",
    )
    parser.add_argument(
        "--plain-progress",
        action="store_true",
        help="Use plain text progress output (no rich terminal UI)",
    )
    parser.add_argument(
        "--limit-zotero",
        type=int,
        default=None,
        help="Limit Zotero items processed (for test runs)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Override storage.collection_name (for test runs)",
    )
    parser.add_argument(
        "--disable-obsidian",
        action="store_true",
        help="Disable Obsidian source (for test runs)",
    )
    parser.add_argument(
        "--request-stop",
        action="store_true",
        help="Request that a running indexer stop cleanly after the current batch",
    )
    parser.add_argument(
        "--clear-stop-request",
        action="store_true",
        help="Clear any pending stop-after-batch request and exit",
    )

    args = parser.parse_args()

    # Determine progress mode
    if args.quiet:
        progress_mode = "quiet"
    elif args.plain_progress:
        progress_mode = "plain"
    else:
        progress_mode = "auto"

    # Check if config exists
    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        print("\nPlease create config.yaml from config.example.yaml:")
        print("  cp config.example.yaml config.yaml")
        print("  # Then edit config.yaml with your settings")
        sys.exit(1)

    # Load configuration
    try:
        with open(args.config) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"[ERROR] Failed to parse configuration: {e}")
        sys.exit(1)

    overrides_applied = False
    if args.collection:
        storage = config.setdefault("storage", {})
        storage["collection_name"] = args.collection
        overrides_applied = True

    if args.limit_zotero is not None:
        zotero = config.setdefault("zotero", {})
        zotero["limit_items"] = args.limit_zotero
        overrides_applied = True

    if args.disable_obsidian:
        obsidian = config.setdefault("obsidian", {})
        obsidian["enabled"] = False
        overrides_applied = True

    if args.request_stop and args.clear_stop_request:
        print("[ERROR] Choose either --request-stop or --clear-stop-request, not both.")
        sys.exit(1)

    if args.request_stop or args.clear_stop_request:
        stop_flag = resolve_stop_flag_path(config)
        stop_flag.parent.mkdir(parents=True, exist_ok=True)

        if args.request_stop:
            stop_flag.write_text("stop_after_batch=true\n", encoding="utf-8")
            print(f"[OK] Stop-after-batch requested via {stop_flag}")
        else:
            if stop_flag.exists():
                stop_flag.unlink()
                print(f"[OK] Cleared stop-after-batch request: {stop_flag}")
            else:
                print(f"[INFO] No stop-after-batch request present: {stop_flag}")
        sys.exit(0)

    config_path = args.config
    if overrides_applied:
        output_dir = Path(config.get("output_folder", "./output"))
        output_dir.mkdir(exist_ok=True)
        config_path = output_dir / "config.override.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(config, f, sort_keys=False)

    # Run preflight validation
    if not args.skip_preflight:
        preflight_passed = run_preflight(
            config=config,
            config_path=config_path,
            allow_legacy_chunking=args.allow_legacy_chunking,
            allow_default_config=args.allow_default_config,
            quiet=args.quiet,
        )

        if not preflight_passed:
            sys.exit(1)

        # Check services are available
        if not args.dry_run:
            services_ok, service_errors = check_services(config)
            if not services_ok:
                print("[ERROR] Required services not available:")
                for error in service_errors:
                    print(f"  - {error}")
                print("\nPlease ensure all services are running before indexing.")
                sys.exit(1)

    # Dry run - just validate and exit
    if args.dry_run:
        print("[OK] Dry run complete - configuration is valid")
        sys.exit(0)

    try:
        # Stand up the managed embedding backend (vLLM) for the duration of the run,
        # then tear it down. No-op unless provider==vllm with managed lifecycle.
        with managed_embedding_backend(config):
            pipeline = ResearchRAGPipeline(config_path, progress_mode=progress_mode)
            pipeline.run(force_reindex=args.force)

    except KeyboardInterrupt:
        print("\n\n[WARN] Indexing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Error during indexing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
