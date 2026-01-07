#!/usr/bin/env python3
"""
Document Hygiene Scanner - Pre-indexing quality check tool.

Scans Zotero and Obsidian documents for quality issues before indexing:
- PDF extraction problems (no OCR, corrupted files)
- Text quality issues (garbage characters, encoding errors)
- Chunk size warnings (content that will produce oversized chunks)
- Obsidian-specific issues (empty files, broken wikilinks)

Usage:
    python scripts/scan_documents.py                  # Full scan
    python scripts/scan_documents.py --limit 100     # Scan first 100 docs
    python scripts/scan_documents.py --zotero-only   # Only scan Zotero
    python scripts/scan_documents.py --obsidian-only # Only scan Obsidian
    python scripts/scan_documents.py -v              # Verbose output

Output:
    - document_hygiene_report.md  (human-readable)
    - document_hygiene_report.json (machine-readable, with exclusion list)
"""

import argparse
import sys
from pathlib import Path

# Fix Unicode encoding for Windows console
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from tqdm import tqdm


def load_config(config_path: Path) -> dict:
    """Load configuration from YAML file."""
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Scan documents for quality issues before indexing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=project_root / "config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=project_root / "output",
        help="Output directory for reports (default: ./output)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Limit number of documents to scan per source",
    )
    parser.add_argument(
        "--zotero-only",
        action="store_true",
        help="Only scan Zotero documents",
    )
    parser.add_argument(
        "--obsidian-only",
        action="store_true",
        help="Only scan Obsidian documents",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Load config
    print(f"Loading configuration from: {args.config}")
    config = load_config(args.config)

    # Import scanner (after path setup)
    from src.hygiene.scanner import DocumentHygieneScanner

    # Initialize scanner
    scanner = DocumentHygieneScanner(config)

    # Progress tracking
    current_source = ""
    pbar = None

    def progress_callback(current, total, message):
        nonlocal pbar, current_source
        if pbar is not None:
            pbar.update(1)
            pbar.set_description(message[:60])

    # Print scan configuration
    print("\n" + "=" * 70)
    print("DOCUMENT HYGIENE SCANNER")
    print("=" * 70)
    print(f"\nConfiguration:")
    print(f"  - Zotero: {'enabled' if config.get('zotero', {}).get('enabled') else 'disabled'}")
    print(f"  - Obsidian: {'enabled' if config.get('obsidian', {}).get('enabled') else 'disabled'}")
    print(f"  - Chunk size: {config.get('chunking', {}).get('chunk_size', 2048)}")
    if args.limit:
        print(f"  - Limit: {args.limit} documents per source")
    if args.zotero_only:
        print("  - Mode: Zotero only")
    elif args.obsidian_only:
        print("  - Mode: Obsidian only")
    print("")

    # Run scan
    print("Scanning documents for quality issues...")
    print("-" * 70)

    # Create progress bars based on what we're scanning
    if not args.obsidian_only and config.get("zotero", {}).get("enabled"):
        print("\n[1/2] Scanning Zotero documents...")
        pbar = tqdm(desc="Scanning", unit="docs", leave=True)
        current_source = "zotero"

    report = scanner.scan_all(
        limit=args.limit,
        zotero_only=args.zotero_only,
        obsidian_only=args.obsidian_only,
        verbose=args.verbose,
        progress_callback=progress_callback if not args.verbose else None,
    )

    if pbar is not None:
        pbar.close()

    # Print summary
    print("\n" + "=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)

    summary = report.summary
    print(f"""
Summary:
  - Documents with issues: {summary['total_with_issues']}
    - Errors:   {summary['errors']}
    - Warnings: {summary['warnings']}
    - Info:     {summary['info']}
""")

    # Generate reports
    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / "document_hygiene_report.md"
    json_path = output_dir / "document_hygiene_report.json"

    print(f"Generating reports...")
    report.to_markdown(md_path)
    report.to_json(json_path)

    print(f"  - Markdown: {md_path}")
    print(f"  - JSON:     {json_path}")

    # Print top issues
    if report.documents:
        print("\n" + "-" * 70)
        print("TOP ISSUES (first 5 errors):")
        print("-" * 70)

        errors = [d for d in report.documents if d.severity == "error"]
        for doc in errors[:5]:
            print(f"\n  [{doc.source.upper()}] {doc.title[:50]}")
            print(f"  File: {doc.file_path}")
            for issue in doc.issues:
                if issue.severity == "error":
                    print(f"    X {issue.check}: {issue.message}")
                    print(f"      -> {issue.suggestion}")

        if len(errors) > 5:
            print(f"\n  ... and {len(errors) - 5} more errors (see full report)")

    # Exclusion list summary
    if report.exclusion_list:
        print(f"\n" + "-" * 70)
        print(f"EXCLUSION LIST: {len(report.exclusion_list)} documents recommended for exclusion")
        print(f"Copy from JSON report to config.yaml to skip during indexing.")
        print("-" * 70)

    print("\nDone!")

    # Return exit code based on errors
    return 1 if summary["errors"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
