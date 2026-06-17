#!/usr/bin/env python3
"""CLI script for querying the research library."""

import argparse
import sys
import textwrap
from pathlib import Path

# Fix Unicode encoding for Windows console
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
        print(f"#{rank} - Score: {score:.4f}")
        print(f"ID: {doc_id}")

        title = metadata.get("title", "Untitled")
        authors = metadata.get("authors", "Unknown")
        source_type = metadata.get("source_type", "unknown")

        print(f"Title: {title}")
        print(f"Authors: {authors}")
        print(f"Source: {source_type}")

        if "backlink" in metadata:
            print(f"Link: {metadata['backlink']}")

        print("\nText:")
        if show_full_text:
            print(text)
        else:
            preview = textwrap.shorten(text, width=500, placeholder="...")
            print(preview)

        print(f"\n{'-' * 80}\n")


def print_survey_results(payload):
    """Print source-level survey results."""
    sources = payload.get("sources", [])
    if not sources:
        print("No source-level survey results found.")
        return

    print(f"\n{'=' * 80}")
    print(f"Found {payload.get('total_sources', len(sources))} source matches")
    recall = payload.get("recall", {}) or {}
    print(
        "Survey: "
        f"k_recall={recall.get('k_recall', 'unknown')}, "
        f"mode={recall.get('mode', 'unknown')}"
    )
    print(f"{'=' * 80}\n")

    for rank, source in enumerate(sources, 1):
        print(
            f"#{rank} - Best Score: {source.get('best_score', 0.0):.4f} "
            f"({source.get('hit_count', 0)} hits)"
        )
        print(
            f"Identity: {source.get('identity_field', 'unknown')}="
            f"{source.get('identity_value', 'unknown')}"
        )
        print(f"Title: {source.get('title', 'Untitled')}")
        print(f"Authors: {source.get('authors', 'Unknown')}")
        if source.get("year"):
            print(f"Year: {source['year']}")
        if source.get("item_type"):
            print(f"Item Type: {source['item_type']}")
        if source.get("venue"):
            print(f"Venue: {source['venue']}")
        if source.get("doi"):
            print(f"DOI: {source['doi']}")
        if source.get("language"):
            print(f"Language: {source['language']}")
        if source.get("tags"):
            print(f"Tags: {source['tags']}")
        if source.get("collections"):
            print(f"Collections: {source['collections']}")
        if source.get("abstract"):
            print(f"Abstract: {textwrap.shorten(source['abstract'], width=500, placeholder='...')}")
        if source.get("backlink"):
            print(f"Link: {source['backlink']}")

        representatives = source.get("representative_chunks", []) or []
        if representatives:
            print("\nRepresentative chunks:")
            for chunk in representatives:
                print(
                    f"  - {chunk.get('chunk_id')} "
                    f"(score={chunk.get('score', 0.0):.4f}, "
                    f"level={chunk.get('chunk_level')}, "
                    f"index={chunk.get('chunk_index')})"
                )
                print(f"    {chunk.get('snippet', '')}")

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
    print("\nLive Controls:")
    print("  :mode <fast|strict>         - Retrieval mode")
    print("  :krecall <number|clear>     - Override recall candidate count")
    print("  :source <type|clear>        - source_type filter")
    print("  :chunk <coarse|mid|fine|clear>")
    print("  :author <name|clear>        - Author contains filter")
    print("  :title <text|clear>         - Title contains filter")
    print("  :year <min> [max] | clear   - Year range filter")
    print("  :rerank <on|off>            - Toggle reranking")
    print("  :diversity <on|off>         - Toggle diversity")
    print("  :maxsource <n|clear>        - Diversity max per source")
    print("  :filters                    - Show active query settings")
    print("=" * 80 + "\n")

    k = 5
    show_full_text = False
    retrieval_mode = "fast"
    k_recall = None
    source_type = None
    chunk_level = None
    author_contains = None
    title_contains = None
    year_min = None
    year_max = None
    rerank_enabled = None
    diversity_enabled = None
    max_per_source = None

    while True:
        try:
            query = input("search > ").strip()

            if not query:
                continue

            if query.startswith(":"):
                if query in [":quit", ":q", ":exit"]:
                    print("Goodbye!")
                    break
                elif query == ":help":
                    print("\nType :filters to show active settings.\n")
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
                elif query.startswith(":mode "):
                    mode = query.split(maxsplit=1)[1].strip().lower()
                    if mode in {"fast", "strict"}:
                        retrieval_mode = mode
                        print(f"Retrieval mode set to: {retrieval_mode}")
                    else:
                        print("Usage: :mode <fast|strict>")
                elif query.startswith(":krecall "):
                    value = query.split(maxsplit=1)[1].strip()
                    if value.lower() == "clear":
                        k_recall = None
                        print("k_recall override cleared")
                    else:
                        try:
                            k_recall = int(value)
                            print(f"k_recall override set to: {k_recall}")
                        except ValueError:
                            print("Usage: :krecall <number|clear>")
                elif query.startswith(":source "):
                    value = query.split(maxsplit=1)[1].strip()
                    if value.lower() == "clear":
                        source_type = None
                        print("source_type filter cleared")
                    else:
                        source_type = value
                        print(f"source_type filter set to: {source_type}")
                elif query.startswith(":chunk "):
                    value = query.split(maxsplit=1)[1].strip().lower()
                    if value == "clear":
                        chunk_level = None
                        print("chunk_level filter cleared")
                    elif value in {"coarse", "mid", "fine"}:
                        chunk_level = value
                        print(f"chunk_level filter set to: {chunk_level}")
                    else:
                        print("Usage: :chunk <coarse|mid|fine|clear>")
                elif query.startswith(":author "):
                    value = query.split(maxsplit=1)[1].strip()
                    author_contains = None if value.lower() == "clear" else value
                    print(
                        "author filter cleared"
                        if author_contains is None
                        else f"author filter set to: {author_contains}"
                    )
                elif query.startswith(":title "):
                    value = query.split(maxsplit=1)[1].strip()
                    title_contains = None if value.lower() == "clear" else value
                    print(
                        "title filter cleared"
                        if title_contains is None
                        else f"title filter set to: {title_contains}"
                    )
                elif query.startswith(":year "):
                    value = query.split(maxsplit=1)[1].strip()
                    if value.lower() == "clear":
                        year_min = None
                        year_max = None
                        print("year range filter cleared")
                    else:
                        parts = value.split()
                        try:
                            if len(parts) == 1:
                                year_min = int(parts[0])
                                year_max = None
                            elif len(parts) == 2:
                                year_min = int(parts[0])
                                year_max = int(parts[1])
                            else:
                                raise ValueError()
                            print(f"year range set to: {year_min}..{year_max if year_max else 'max'}")
                        except ValueError:
                            print("Usage: :year <min> [max] | clear")
                elif query.startswith(":rerank "):
                    value = query.split(maxsplit=1)[1].strip().lower()
                    if value == "on":
                        rerank_enabled = True
                        print("rerank enabled")
                    elif value == "off":
                        rerank_enabled = False
                        print("rerank disabled")
                    else:
                        print("Usage: :rerank <on|off>")
                elif query.startswith(":diversity "):
                    value = query.split(maxsplit=1)[1].strip().lower()
                    if value == "on":
                        diversity_enabled = True
                        print("diversity enabled")
                    elif value == "off":
                        diversity_enabled = False
                        print("diversity disabled")
                    else:
                        print("Usage: :diversity <on|off>")
                elif query.startswith(":maxsource "):
                    value = query.split(maxsplit=1)[1].strip()
                    if value.lower() == "clear":
                        max_per_source = None
                        print("max-per-source cleared")
                    else:
                        try:
                            max_per_source = int(value)
                            print(f"max-per-source set to: {max_per_source}")
                        except ValueError:
                            print("Usage: :maxsource <number|clear>")
                elif query == ":filters":
                    print("\nActive Query Settings:")
                    print(f"  mode: {retrieval_mode}")
                    print(f"  k: {k}")
                    print(f"  k_recall: {k_recall}")
                    print(f"  source_type: {source_type}")
                    print(f"  chunk_level: {chunk_level}")
                    print(f"  author_contains: {author_contains}")
                    print(f"  title_contains: {title_contains}")
                    print(f"  year_min: {year_min}")
                    print(f"  year_max: {year_max}")
                    print(f"  rerank_enabled: {rerank_enabled}")
                    print(f"  diversity_enabled: {diversity_enabled}")
                    print(f"  max_per_source: {max_per_source}\n")
                else:
                    print(f"Unknown command: {query}")
                    print("Type :help to see available commands")
                continue

            results = pipeline.query(
                query,
                k=k,
                retrieval_mode=retrieval_mode,
                rerank_enabled=rerank_enabled,
                diversity_enabled=diversity_enabled,
                diversity_max_per_key=max_per_source,
                k_recall_override=k_recall,
                source_type=source_type,
                chunk_level=chunk_level,
                author_contains=author_contains,
                title_contains=title_contains,
                year_min=year_min,
                year_max=year_max,
            )
            print_results(results, show_full_text=show_full_text)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"[ERROR] {e}")


def main():
    parser = argparse.ArgumentParser(description="Query your research library")
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
        help=(
            "Number of final results to return after all filtering and reranking "
            "(default: 5). This is your top-k output size."
        ),
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["fast", "strict"],
        default=None,
        help=(
            "Retrieval mode. 'fast' performs broad vector recall followed by post-filtering "
            "(better latency on large corpora). 'strict' applies metadata filters directly in "
            "the vector-store query path."
        ),
    )
    parser.add_argument(
        "--k-recall",
        type=int,
        default=None,
        help=(
            "Override how many candidates to retrieve from vector store before reranking/diversity "
            "(default: from config, typically 50). Use higher values when applying heavy post-filters. "
            "Example: --k-recall 100 to get more candidates when filtering by author or year."
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full text of results instead of truncated preview. Useful for detailed analysis.",
    )
    parser.add_argument(
        "--survey",
        action="store_true",
        help=(
            "Return source-level survey rows by aggregating recalled mid chunks. "
            "This is the v0.6 broad-survey replacement for coarse chunk search."
        ),
    )
    parser.add_argument(
        "--representative-chunks",
        type=int,
        default=3,
        help="Number of representative chunk snippets to show per source in --survey mode.",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help=(
            "Disable LLM-based reranking for this query (falls back to pure vector similarity). "
            "Use when you want faster results or to debug embedding quality."
        ),
    )
    parser.add_argument(
        "--no-diversity",
        action="store_true",
        help=(
            "Disable diversity/deduplication filtering (allows multiple chunks from same source). "
            "Use for deep dives into specific sources where you want all relevant chunks."
        ),
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=None,
        help=(
            "Maximum results allowed per source document (auto-enables diversity if not already on). "
            "Examples: --max-per-source 1 for broad survey across sources, "
            "--max-per-source 10 for deep dive into each relevant source. Default from config is typically 2."
        ),
    )
    parser.add_argument(
        "--chunk-level",
        type=str,
        choices=["mid", "atomic", "coarse", "fine"],
        default=None,
        help=(
            "Filter by chunk level. v0.6 production uses MID for text/markdown and "
            "ATOMIC for Zotero annotations; COARSE/FINE are legacy/experimental."
        ),
    )
    parser.add_argument(
        "--source-type",
        type=str,
        default=None,
        help=(
            "Restrict search to a specific source type. Options: 'zotero' (base item metadata), "
            "'zotero_fulltext', 'zotero_note', 'zotero_annotation', 'obsidian'."
        ),
    )
    parser.add_argument(
        "--zotero-key",
        type=str,
        default=None,
        help=(
            "Restrict search to a single Zotero item by its key (exact match). "
            "Example: --zotero-key XMN6HI9Y."
        ),
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Filter results where author field contains this substring (case-insensitive).",
    )
    parser.add_argument(
        "--title-contains",
        type=str,
        default=None,
        help="Filter results where title contains this substring (case-insensitive).",
    )
    parser.add_argument(
        "--year-min",
        type=int,
        default=None,
        help="Restrict to publications from this year onwards (inclusive).",
    )
    parser.add_argument(
        "--year-max",
        type=int,
        default=None,
        help="Restrict to publications up to this year (inclusive).",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Filter survey source rows by Zotero collection name substring.",
    )
    parser.add_argument(
        "--item-type",
        type=str,
        default=None,
        help="Filter survey source rows by exact Zotero item type, e.g. book or journalArticle.",
    )
    parser.add_argument(
        "--doi",
        type=str,
        default=None,
        help="Filter survey source rows by DOI substring.",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Filter survey source rows by exact Zotero language code, e.g. en.",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Filter survey source rows by exact Zotero tag.",
    )

    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        print("\nPlease create config.yaml from config.example.yaml")
        sys.exit(1)

    try:
        pipeline = ResearchRAGPipeline(args.config)
        stats = pipeline.vector_store.get_collection_stats()
        if stats.get("document_count", 0) == 0:
            print("[WARN] Collection is empty! Run 'python scripts/index.py' first.")
            sys.exit(1)

        if args.query:
            query_text = " ".join(args.query)
            if args.survey:
                payload = pipeline.survey_sources(
                    query_text,
                    k=args.k,
                    retrieval_mode=args.mode,
                    k_recall_override=args.k_recall,
                    chunk_level=args.chunk_level,
                    source_type=args.source_type,
                    zotero_key=args.zotero_key,
                    author_contains=args.author,
                    title_contains=args.title_contains,
                    year_min=args.year_min,
                    year_max=args.year_max,
                    collection=args.collection,
                    item_type=args.item_type,
                    doi=args.doi,
                    language=args.language,
                    tag=args.tag,
                    representative_limit=args.representative_chunks,
                )
                print_survey_results(payload)
            else:
                results = pipeline.query(
                    query_text,
                    k=args.k,
                    retrieval_mode=args.mode,
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
