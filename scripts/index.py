#!/usr/bin/env python3
"""CLI script for indexing research library into ChromaDB."""

import argparse
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline import ResearchRAGPipeline


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

    args = parser.parse_args()

    # Check if config exists
    if not args.config.exists():
        print(f"❌ Configuration file not found: {args.config}")
        print("\nPlease create config.yaml from config.example.yaml:")
        print("  cp config.example.yaml config.yaml")
        print("  # Then edit config.yaml with your settings")
        sys.exit(1)

    try:
        # Initialize and run pipeline
        pipeline = ResearchRAGPipeline(args.config)
        pipeline.run(force_reindex=args.force)

    except KeyboardInterrupt:
        print("\n\n⚠️  Indexing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during indexing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
