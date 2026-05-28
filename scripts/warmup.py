#!/usr/bin/env python3
"""Warm up the Re-Searcher query stack."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from functools import partial

if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

print = partial(print, flush=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import ResearchRAGPipeline


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm up ChromaDB, LM Studio embeddings, and vector search.")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config.yaml",
        help="Path to config.yaml.",
    )
    parser.add_argument(
        "--query",
        default="Whitehead process philosophy coaching",
        help="Semantic query used for the warm-up search.",
    )
    parser.add_argument(
        "-k",
        type=_positive_int,
        default=1,
        help="Number of warm-up results to request.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=300,
        help="Maximum seconds to wait for the stack to warm up.",
    )
    parser.add_argument(
        "--interval",
        type=_positive_int,
        default=5,
        help="Seconds between retry attempts.",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Skip reranking during warm-up and only verify embeddings plus Chroma vector search.",
    )
    return parser.parse_args()


def warm_once(config_path: Path, query: str, k: int, no_rerank: bool) -> tuple[int, int, str]:
    pipeline = ResearchRAGPipeline(config_path, progress_mode="quiet")

    stats = pipeline.vector_store.get_collection_stats()
    document_count = int(stats.get("document_count") or 0)
    if document_count <= 0:
        raise RuntimeError("Chroma collection is reachable, but it contains no documents.")

    embedding = pipeline.embedder.embed_query(query)
    embedding_dimension = len(embedding)
    if embedding_dimension != 1024:
        raise RuntimeError(
            f"Embedding endpoint returned {embedding_dimension} dimensions; expected 1024 for BGE-M3."
        )

    results = pipeline.query(
        query,
        k=k,
        rerank_enabled=False if no_rerank else None,
    )
    if not results:
        raise RuntimeError("Warm-up query returned no results.")

    return document_count, embedding_dimension, results[0][0]


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()

    if not config_path.exists():
        print(f"[ERROR] Config file not found: {config_path}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + args.timeout
    attempt = 0
    last_error = ""

    print("Re-Searcher warm-up starting")
    print(f"Config: {config_path}")
    print(f"Query: {args.query!r}")
    print(f"Timeout: {args.timeout}s")
    print()

    while time.monotonic() <= deadline:
        attempt += 1
        print(f"[{attempt}] Checking query stack...")
        try:
            document_count, embedding_dimension, top_result_id = warm_once(
                config_path=config_path,
                query=args.query,
                k=args.k,
                no_rerank=args.no_rerank,
            )
        except Exception as exc:
            last_error = str(exc)
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining <= 0:
                break
            print(f"    Not ready yet: {last_error}")
            print(f"    Retrying in {args.interval}s ({remaining}s remaining)...")
            time.sleep(args.interval)
            continue

        print()
        print("=" * 72)
        print("Warmed Up")
        print("=" * 72)
        print(f"Chroma documents: {document_count:,}")
        print(f"Embedding dimension: {embedding_dimension}")
        print(f"Top warm-up result: {top_result_id}")
        return 0

    print()
    print("[ERROR] Re-Searcher did not warm up before the timeout.", file=sys.stderr)
    if last_error:
        print(f"Last error: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
