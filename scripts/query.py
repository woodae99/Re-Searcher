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
    print("\nBasic Commands:")
    print("  <query text>     - Search for documents using natural language")
    print("  :k <number>      - Set number of results (default: 5)")
    print("  :full            - Toggle full text display (on/off)")
    print("  :stats           - Show collection statistics")
    print("  :help            - Show this help message")
    print("  :quit or :q      - Exit interactive mode")
    print("\nAdvanced Filtering (use CLI mode with arguments):")
    print("  --chunk-level    - Filter by chunk size (coarse/mid/fine)")
    print("  --max-per-source - Limit results per source (diversity control)")
    print("  --author         - Filter by author name")
    print("  --year-min/max   - Filter by publication year range")
    print("  --source-type    - Filter by source type (zotero/obsidian)")
    print("  --no-rerank      - Disable reranking")
    print("\nTip: For advanced options, use CLI mode:")
    print("  python scripts/query.py \"your query\" --chunk-level coarse -k 5")
    print("  python scripts/query.py --help  (for all options)")
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
                elif query == ":help":
                    # Show help message
                    print("\nBasic Commands:")
                    print("  <query text>     - Search for documents using natural language")
                    print("  :k <number>      - Set number of results (default: 5)")
                    print("  :full            - Toggle full text display (on/off)")
                    print("  :stats           - Show collection statistics")
                    print("  :help            - Show this help message")
                    print("  :quit or :q      - Exit interactive mode")
                    print("\nAdvanced Filtering (use CLI mode with arguments):")
                    print("  --chunk-level    - Filter by chunk size (coarse/mid/fine)")
                    print("  --max-per-source - Limit results per source (diversity control)")
                    print("  --author         - Filter by author name")
                    print("  --year-min/max   - Filter by publication year range")
                    print("  --source-type    - Filter by source type (zotero/obsidian)")
                    print("  --no-rerank      - Disable reranking")
                    print("\nTip: For advanced options, use CLI mode:")
                    print("  python scripts/query.py \"your query\" --chunk-level coarse -k 5")
                    print("  python scripts/query.py --help  (for all options)\n")
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
                    print("Type :help to see available commands")
                continue

            # Run query
            results = pipeline.query(query, k=k)
            # Note: interactive mode does not currently expose rerank/diversity overrides.
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
        help="Number of final results to return after all filtering and reranking (default: 5). "
             "This is your top-k output size.",
    )
    parser.add_argument(
        "--k-recall",
        type=int,
        default=None,
        help="Override how many candidates to retrieve from vector store before reranking/diversity "
             "(default: from config, typically 50). Use higher values when applying heavy post-filters. "
             "Example: --k-recall 100 to get more candidates when filtering by author or year.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full text of results instead of truncated preview. Useful for detailed analysis.",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Disable LLM-based reranking for this query (falls back to pure vector similarity). "
             "Use when you want faster results or to debug embedding quality.",
    )
    parser.add_argument(
        "--no-diversity",
        action="store_true",
        help="Disable diversity/deduplication filtering (allows multiple chunks from same source). "
             "Use for deep dives into specific sources where you want all relevant chunks.",
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=None,
        help="Maximum results allowed per source document (auto-enables diversity if not already on). "
             "Examples: --max-per-source 1 for broad survey across sources, "
             "--max-per-source 10 for deep dive into each relevant source. "
             "Default from config is typically 2.",
    )

    # Filters (deep dives)
    parser.add_argument(
        "--chunk-level",
        type=str,
        choices=["coarse", "mid", "fine"],
        default=None,
        help="Filter by hierarchical chunk granularity level. "
             "COARSE: Large sections with broad context (good for overview/gist, ~1500-2500 chars). "
             "MID: Medium sections with balanced context (good for general queries, ~800-1500 chars). "
             "FINE: Small focused segments like paragraphs/headings (good for precise matches, may lack context). "
             "Omit to search all levels (default). Use coarse/mid for better context in results.",
    )
    parser.add_argument(
        "--source-type",
        type=str,
        default=None,
        help="Restrict search to a specific source type. "
             "Options: 'zotero_fulltext' (PDF/doc full text), 'zotero_note' (Zotero notes), "
             "'zotero_annotation' (PDF highlights/comments), 'obsidian' (vault markdown notes). "
             "Useful for focusing on specific content types.",
    )
    parser.add_argument(
        "--zotero-key",
        type=str,
        default=None,
        help="Restrict search to a single Zotero item by its key (exact match). "
             "Use this for deep diving into one specific paper/book. "
             "Example: --zotero-key XMN6HI9Y to search only within that item.",
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Filter results where author field contains this substring (case-insensitive). "
             "Example: --author 'Smith' finds 'John Smith', 'Smith et al', etc. "
             "Useful for finding all works by or involving a specific researcher.",
    )
    parser.add_argument(
        "--title-contains",
        type=str,
        default=None,
        help="Filter results where title contains this substring (case-insensitive). "
             "Example: --title-contains 'coaching' finds any title mentioning coaching. "
             "Useful for narrowing to specific topics or book titles.",
    )
    parser.add_argument(
        "--year-min",
        type=int,
        default=None,
        help="Restrict to publications from this year onwards (inclusive). "
             "Example: --year-min 2020 for recent research only. "
             "Combine with --year-max for a range (e.g., --year-min 2015 --year-max 2020).",
    )
    parser.add_argument(
        "--year-max",
        type=int,
        default=None,
        help="Restrict to publications up to this year (inclusive). "
             "Example: --year-max 2010 for historical research. "
             "Combine with --year-min for a specific time period.",
    )

    args = parser.parse_args()

    # Check if config exists
    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        print("\nPlease create config.yaml from config.example.yaml")
        sys.exit(1)

    try:
        # Initialize pipeline
        pipeline = ResearchRAGPipeline(args.config)

        # Check if collection has any data
        stats = pipeline.vector_store.get_collection_stats()
        if stats.get("document_count", 0) == 0:
            print("[WARN] Collection is empty! Run 'python scripts/index.py' first.")
            sys.exit(1)

        # Run query or interactive mode
        if args.query:
            # Single query mode
            query_text = " ".join(args.query)
            results = pipeline.query(
                query_text,
                k=args.k,
                rerank_enabled=(False if args.no_rerank else None),
                diversity_enabled=(False if args.no_diversity else None),
                diversity_max_per_key=args.max_per_source,
                k_recall_override=args.k_recall,
                chunk_level=args.chunk_level,
                source_type=args.source_type,
                zotero_key=args.zotero_key,
                author_contains=args.author,
                title_contains=args.title_contains,
                year_min=args.year_min,
                year_max=args.year_max,
            )
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
