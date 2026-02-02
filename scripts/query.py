#!/usr/bin/env python3
"""CLI script for querying the research library."""

import argparse
import sys
import textwrap
from pathlib import Path

# Fix Unicode encoding for Windows console
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline import ResearchRAGPipeline


def print_results(results, show_full_text=False):
    """Print search results in a formatted way."""
    if not results:
        print("No results found.")
        return

    print(f"\n{'=' * 80}")
    print(f"Found {len(results)} results")
    print(f"{'=' * 80}\n")

    for rank, (doc_id, text, score, metadata) in enumerate(results, 1):
        # Header
        print(f"#{rank} - Score: {score:.4f}")
        print(f"ID: {doc_id}")

        # Metadata
        title = metadata.get("title", "Untitled")
        authors = metadata.get("authors", "Unknown")
        source_type = metadata.get("source_type", "unknown")

        print(f"Title: {title}")
        print(f"Authors: {authors}")
        print(f"Source: {source_type}")

        # Backlink
        if "backlink" in metadata:
            print(f"Link: {metadata['backlink']}")

        # Text preview
        print("\nText:")
        if show_full_text:
            print(text)
        else:
            preview = textwrap.shorten(text, width=500, placeholder="...")
            print(preview)

        print(f"\n{'-' * 80}\n")


def interactive_mode(pipeline):
    """Run interactive query mode."""
    print("\n" + "=" * 80)
    print("Research RAG - Interactive Query Mode")
    print("=" * 80)
    print("\nCommands:")
    print("  <query text>  - Search for documents")
    print("  :full         - Toggle full text display")
    print("  :k <number>   - Set number of results (default: 5)")
    print("  :stats        - Show collection statistics")
    print("  :quit or :q   - Exit")
    print("=" * 80 + "\n")

    k = 5
    show_full_text = False

    while True:
        try:
            query = input("🔍 > ").strip()

            if not query:
                continue

            # Handle commands
            if query.startswith(":"):
                if query in [":quit", ":q", ":exit"]:
                    print("Goodbye!")
                    break
                elif query == ":full":
                    show_full_text = not show_full_text
                    print(f"Full text display: {'ON' if show_full_text else 'OFF'}")
                elif query.startswith(":k "):
                    try:
                        k = int(query.split()[1])
                        print(f"Results limit set to: {k}")
                    except (IndexError, ValueError):
                        print("Usage: :k <number>")
                elif query == ":stats":
                    stats = pipeline.vector_store.get_collection_stats()
                    print("\nCollection Statistics:")
                    for key, value in stats.items():
                        print(f"  {key}: {value}")
                    print()
                else:
                    print(f"Unknown command: {query}")
                continue

            # Run query
            results = pipeline.query(query, k=k)
            print_results(results, show_full_text=show_full_text)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"[ERROR] {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Query your research library"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Query text (if omitted, starts interactive mode)",
    )
    parser.add_argument(
        "-k",
        type=int,
        default=5,
        help="Number of results to return (default: 5)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full text instead of preview",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Disable reranking for this run (overrides config)",
    )

    args = parser.parse_args()

    # Check if config exists
    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        print("\nPlease create config.yaml from config.example.yaml")
        sys.exit(1)

    try:
        # If requested, override config without modifying the original file.
        config_path = args.config
        if args.no_rerank:
            import tempfile
            import yaml

            cfg = yaml.safe_load(config_path.read_text())
            cfg.setdefault("retrieval", {}).setdefault("rerank", {})
            cfg["retrieval"]["rerank"]["enabled"] = False

            tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
            yaml.safe_dump(cfg, tmp, sort_keys=False)
            tmp.flush()
            config_path = Path(tmp.name)

        # Initialize pipeline
        pipeline = ResearchRAGPipeline(config_path)

        # Check if collection has any data
        stats = pipeline.vector_store.get_collection_stats()
        if stats.get("document_count", 0) == 0:
            print("[WARN] Collection is empty! Run 'python scripts/index.py' first.")
            sys.exit(1)

        # Run query or interactive mode
        if args.query:
            # Single query mode
            query_text = " ".join(args.query)
            results = pipeline.query(query_text, k=args.k)
            print_results(results, show_full_text=args.full)
        else:
            # Interactive mode
            interactive_mode(pipeline)

    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
