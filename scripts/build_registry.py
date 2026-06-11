#!/usr/bin/env python3
"""Build (and audit) the source registry from an existing ChromaDB collection.

One-time backfill for collections indexed before the registry existed. The
scan offset is committed atomically with each recorded batch, so the script
can be interrupted (Ctrl-C, reboot) and re-run to resume where it left off.

After the scan it runs an integrity audit: registry vs Zotero SQLite, registry
vs Obsidian vault, duplicate chunk slots, legacy IDs, and (optionally) a
sampled zero-vector check. The report is written to output/registry_audit.json.

Usage:
  python scripts/build_registry.py                 # backfill (resume) + audit
  python scripts/build_registry.py --restart       # wipe registry, rebuild from scratch
  python scripts/build_registry.py --audit-only    # skip scan, just audit
  python scripts/build_registry.py --check-embeddings 2000
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.registry import SourceRegistry, backfill_from_collection, registry_path_for
from src.registry_audit import run_audit, summarize_report
from src.storage.chroma import ChromaVectorStore


def main():
    parser = argparse.ArgumentParser(
        description="Backfill and audit the source registry from ChromaDB"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Chunks per scan batch (default: 5000)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Discard existing registry data and scan from the beginning",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Skip the backfill scan and only run the integrity audit",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Run the backfill scan without the integrity audit",
    )
    parser.add_argument(
        "--check-embeddings",
        type=int,
        default=0,
        metavar="N",
        help="Sample N chunks and check for zero vectors (failed-embed sentinel)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Audit report path (default: <output_folder>/registry_audit.json)",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    registry_path = registry_path_for(config)
    print(f"[OK] Registry database: {registry_path}")
    registry = SourceRegistry(registry_path)

    store = ChromaVectorStore(config)
    collection = store.collection

    if not args.audit_only:
        try:
            result = backfill_from_collection(
                registry,
                collection,
                batch_size=max(100, args.batch_size),
                restart=args.restart,
            )
        except KeyboardInterrupt:
            offset = registry.get_meta("backfill_offset", "0")
            print(
                f"\n[INFO] Backfill interrupted at offset {int(offset or 0):,}. "
                f"Progress is saved; re-run the same command to resume."
            )
            sys.exit(130)

        if result.get("skipped"):
            print(f"[INFO] Backfill skipped: {result.get('reason')}")
        else:
            print(
                f"[OK] Backfill complete: {result['total_offset']:,} chunks scanned, "
                f"{result['source_count']:,} sources registered."
            )

    if args.skip_audit:
        return

    print("\n[INFO] Running integrity audit...")
    chroma_count = collection.count()
    report = run_audit(
        registry,
        config,
        collection=collection if args.check_embeddings > 0 else None,
        embedding_sample=args.check_embeddings,
        chroma_count=chroma_count,
    )

    output_dir = Path(config.get("output_folder", "./output"))
    output_dir.mkdir(exist_ok=True)
    report_path = args.report or (output_dir / "registry_audit.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print()
    print(summarize_report(report))
    print(f"\n[OK] Full report written to {report_path}")


if __name__ == "__main__":
    main()
