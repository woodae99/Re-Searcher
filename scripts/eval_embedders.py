#!/usr/bin/env python3
"""Embedder bake-off — compare embedding models on retrieval quality *and* cost.

The embedder is the one v0.6 decision baked into the rebuild (every stored vector
depends on it), so it should be decided on measured retrieval *before* the
production rebuild — the same evidence-based way the chunk grain was. This script
fixes the grain (700/100) and the gold, then sweeps embedders with RAW vector
retrieval (no rerank, to isolate the embedder), reporting passage hit@k / MRR /
strict@5, embedding throughput, and the projected vector-store size at full scale.

Fairness note: different embedder families want different prompt prefixes (bge-m3
none; nomic `search_document:`/`search_query:`; EmbeddingGemma task templates;
Qwen3-Embedding a query instruction). PREFIXES below applies the right ones per
model so no family is unfairly handicapped — these are best-effort from the model
cards and are a tuning lever; refine if a model underperforms its leaderboard rep.

Specs are LM Studio model identifiers (see `lms ls`). Gold must pre-exist
(scripts/eval_passage.py). All compute is local. See docs/EMBEDDER_BAKEOFF.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # import sibling eval_passage

import yaml  # noqa: E402

import eval_passage as ep  # noqa: E402
from src.embedding.lmstudio import LMStudioEmbedding  # noqa: E402
from src.passage_eval import PassageProbe, evaluate_passage  # noqa: E402

LMS = os.path.expanduser("~/.lmstudio/bin/lms")

# Per-family (doc_prefix, query_prefix). Matched by case-insensitive substring.
# Asymmetric prefixes are how instruct/late-pooling embedders are *meant* to be
# used; omitting them handicaps those models relative to bge-m3 (which needs none).
PREFIXES: List[Tuple[str, Tuple[str, str]]] = [
    ("qwen3-embedding", ("", "Instruct: Given a research question, retrieve passages that answer it\nQuery:")),
    ("nomic-embed", ("search_document: ", "search_query: ")),
    ("embeddinggemma", ("title: none | text: ", "task: search result | query: ")),
    ("bge-m3", ("", "")),
]


def prefixes_for(model: str) -> Tuple[str, str]:
    low = model.lower()
    for key, pair in PREFIXES:
        if key in low:
            return pair
    return ("", "")


class PrefixEmbedder:
    """Wrap an embedder, applying family-specific doc/query prefixes."""

    def __init__(self, inner, doc_prefix: str = "", query_prefix: str = ""):
        self.inner = inner
        self.dp = doc_prefix
        self.qp = query_prefix

    def embed_texts(self, texts):
        return self.inner.embed_texts([self.dp + t for t in texts] if self.dp else texts)

    def embed_query(self, q):
        return self.inner.embed_query(self.qp + q if self.qp else q)

    @property
    def _dimension(self):
        return getattr(self.inner, "_dimension", None)


def make_embedder(base_cfg: Dict, model: str):
    cfg = json.loads(json.dumps(base_cfg))
    emb = cfg.setdefault("embedding", {})
    emb["model"] = model
    emb.pop("lmstudio", None)  # ensure embedding.model (above) wins; endpoint still used
    inner = LMStudioEmbedding(cfg)
    dp, qp = prefixes_for(model)
    return PrefixEmbedder(inner, dp, qp)


def _safe_name(model: str) -> str:
    n = re.sub(r"[^a-zA-Z0-9_-]", "_", model).strip("_")[:60]
    return n or "emb"


def _lms(*args):
    subprocess.run([LMS, *args], check=False)


def main() -> int:
    args = parse_args()
    os.environ.setdefault("OPENAI_API_KEY", "lm-studio")
    base_cfg = yaml.safe_load(args.config.read_text()) or {}

    if not args.gold.exists():
        sys.exit(f"Gold not found: {args.gold}\nGenerate it once with scripts/eval_passage.py.")
    gold = json.loads(args.gold.read_text())
    probes = [PassageProbe(query=g["query"], source_id=g["source_id"],
                           gold_start=g["gold_start"], gold_end=g["gold_end"],
                           probe_id=g["probe_id"]) for g in gold]
    targets = sorted({g["source_id"] for g in gold})
    corpus, labels = ep.load_corpus(args.source_root, targets, args.n_distractors,
                                    args.target_max_chars, args.distractor_max_chars,
                                    args.min_chars)
    chunk_cfg = ep.CONFIG_VARIANTS[args.variant]
    print(f"Corpus {len(corpus)} sources ({len(targets)} gold targets); grain {args.variant} "
          f"{chunk_cfg}; {len(probes)} probes; RAW retrieval (no rerank)")
    print(f"storage projected at {args.total_chunks:,} chunks\n")

    rows: List[Dict] = []
    for model in args.embedders:
        if args.autoload:
            print(f"loading {model} …", flush=True)
            _lms("load", model, "--yes", "--ttl", "3600")
        dp, qp = prefixes_for(model)
        try:
            embedder = make_embedder(base_cfg, model)
            coll, chunk_index, n_chunks, embed_secs = ep.build_collection(
                _safe_name(model), corpus, chunk_cfg, embedder)
            search = ep.make_chunk_search_fn(coll, embedder)
            rep = evaluate_passage(search, probes, chunk_index, k_values=(1, 3, 5, 10), expansions=(1,))
            dim = embedder._dimension
        except Exception as exc:  # noqa: BLE001
            print(f"  {model:42} FAILED: {str(exc)[:150]}", flush=True)
            if args.autoload:
                _lms("unload", model)
            continue
        store_gb = round(dim * 4 * args.total_chunks / 1e9, 1) if dim else None
        row = {"embedder": model, "dim": dim,
               "hit@1": rep["passage_hit_at"].get("hit@1"),
               "hit@3": rep["passage_hit_at"].get("hit@3"),
               "hit@5": rep["passage_hit_at"].get("hit@5"),
               "hit@10": rep["passage_hit_at"].get("hit@10"),
               "mrr": rep["passage_mrr"],
               "strict@5": rep["strict_hit_at"].get("hit@5"),
               "embed_secs": embed_secs, "chunks": n_chunks,
               "est_store_gb": store_gb,
               "prefixed": bool(dp or qp)}
        rows.append(row)
        print(f"  {model:42} dim={dim} hit@1={row['hit@1']} hit@5={row['hit@5']} "
              f"mrr={row['mrr']} strict@5={row['strict@5']} embed={embed_secs}s "
              f"store~{store_gb}GB", flush=True)
        if args.autoload:
            _lms("unload", model)

    rows.sort(key=lambda r: (r["mrr"] or 0, r["hit@5"] or 0), reverse=True)
    print("\n== embedder ranking (mrr, then hit@5) ==")
    for r in rows:
        print(f"  {r['embedder']:42} dim={r['dim']} mrr={r['mrr']} hit@1={r['hit@1']} "
              f"hit@3={r['hit@3']} hit@5={r['hit@5']} hit@10={r['hit@10']} "
              f"strict@5={r['strict@5']} store~{r['est_store_gb']}GB")

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(
        {"grain": args.variant, "chunk_cfg": chunk_cfg, "probes": len(probes),
         "total_chunks_for_storage": args.total_chunks, "gold": str(args.gold),
         "rows": rows}, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output_json}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config.example.yaml")
    p.add_argument("--source-root", type=Path, default=ep.DEFAULT_SOURCE_ROOT)
    p.add_argument("--variant", default="recursive_700_100", choices=list(ep.CONFIG_VARIANTS),
                   help="Fixed chunk grain (settled at 700/100).")
    p.add_argument("--embedders", nargs="+",
                   default=["text-embedding-bge-m3",
                            "text-embedding-nomic-embed-text-v1.5",
                            "text-embedding-embeddinggemma-300m-qat"],
                   help="LM Studio embedding model ids to compare (see `lms ls`).")
    p.add_argument("--total-chunks", type=int, default=9_850_000,
                   help="Corpus size used for the projected vector-store size column.")
    p.add_argument("--n-distractors", type=int, default=100)
    p.add_argument("--target-max-chars", type=int, default=4_000_000)
    p.add_argument("--distractor-max-chars", type=int, default=40_000)
    p.add_argument("--min-chars", type=int, default=3000)
    p.add_argument("--autoload", action=argparse.BooleanOptionalAction, default=True,
                   help="lms load each embedder before testing and unload after.")
    p.add_argument("--gold", type=Path, default=REPO_ROOT / "output" / "eval" / "gold_passage_curated.json")
    p.add_argument("--output-json", type=Path, default=REPO_ROOT / "output" / "eval" / "embedder_bakeoff.json")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
