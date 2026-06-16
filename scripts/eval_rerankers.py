#!/usr/bin/env python3
"""Reranker bake-off — compare rerank backends on speed *and* passage accuracy.

The chunk-grain decision is settled (recursive 700/100; see docs/PASSAGE_EVAL.md).
This script holds the grain and the gold fixed and sweeps *rerankers*, so you can
answer operational questions later without re-deriving anything:

  * "gemma-4-12b (dense) vs qwen3.6-35b-a3b (MoE) — speed vs accuracy?"
  * "is the small e4b reranker good enough, or does a bigger model earn its latency?"
  * "how would a real cross-encoder (BGE) reranker compare?"

It builds the collection + embeddings ONCE, then runs each reranker over the same
probes, reporting passage hit@k / MRR / strict@5 and mean seconds-per-query.

Reranker specs (--rerankers, space-separated):
  none                              raw vector order (baseline)
  lmstudio:<model>                  production LLMReranker via LM Studio
                                    e.g. lmstudio:google/gemma-4-12b
                                         lmstudio:qwen/qwen3.6-35b-a3b
  http://host:port/rerank[#model]   HTTP cross-encoder service (TEI / Infinity /
                                    vLLM). LM Studio has NO rerank endpoint, so a
                                    BGE cross-encoder (e.g. BAAI/bge-reranker-v2-m3,
                                    the natural partner to bge-m3) must run as a
                                    separate service. See docs/RERANKER_BAKEOFF.md.

All compute is local. Gold must already exist (generate it once with
scripts/eval_passage.py) so a reranker sweep never burns LLM calls regenerating it.

VRAM note (RTX 5090, 32 GB): models above ~31 GB (gemma-4-31b, qwen3.6-35b-a3b at
~37 GB, the 122b) won't fully fit alongside bge-m3 and will CPU-offload — slow, but
the bake-off will measure exactly that. Use --autoload to load/unload each model.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # import sibling eval_passage

import yaml  # noqa: E402

import eval_passage as ep  # noqa: E402  (shared corpus / collection / reranker plumbing)
from src.embedding.lmstudio import LMStudioEmbedding  # noqa: E402
from src.passage_eval import PassageProbe, evaluate_passage  # noqa: E402
from src.retrieval.rerank import NoRerank  # noqa: E402

LMS = os.path.expanduser("~/.lmstudio/bin/lms")


class TimingReranker:
    """Wrap any reranker; accumulate rerank() wall-time and call count."""

    def __init__(self, inner):
        self.inner = inner
        self.seconds = 0.0
        self.calls = 0

    def rerank(self, query, results):
        t = time.perf_counter()
        try:
            return self.inner.rerank(query, results)
        finally:
            self.seconds += time.perf_counter() - t
            self.calls += 1


class CrossEncoderReranker:
    """HTTP cross-encoder rerank client (TEI / Infinity / Jina-style /rerank).

    POSTs {"query": ..., "texts": [...]} and accepts the common response shapes:
    {"results": [{"index": i, "score"|"relevance_score": s}, ...]} or a bare list.
    Untested here unless --rerankers includes a live http:// service; see
    docs/RERANKER_BAKEOFF.md for standing one up (BAAI/bge-reranker-v2-m3 pairs
    with bge-m3). Implements the same (id, text, score, meta) tuple contract as the
    production rerankers, so it slots straight into make_reranked_search_fn.
    """

    def __init__(self, url: str, model: Optional[str] = None, timeout: int = 60):
        self.url = url
        self.model = model
        self.timeout = timeout

    def rerank(self, query, results):
        texts = [t for (_id, t, _s, _m) in results]
        body = {"query": query, "texts": texts}
        if self.model:
            body["model"] = self.model
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data["results"] if isinstance(data, dict) and "results" in data else data
        scored: Dict[int, float] = {}
        for it in items:
            idx = it.get("index", it.get("idx"))
            if idx is None:
                continue
            scored[int(idx)] = float(it.get("score", it.get("relevance_score", 0.0)))
        order = sorted(range(len(results)), key=lambda i: scored.get(i, float("-inf")), reverse=True)
        return [results[i] for i in order]


def make_backend(spec: str, base_cfg: Dict):
    """Resolve a reranker spec to (reranker, lmstudio_model_or_None)."""
    if spec == "none":
        return NoRerank(base_cfg), None
    if spec.startswith("lmstudio:"):
        model = spec.split(":", 1)[1]
        return ep.build_reranker(base_cfg, model), model
    if spec.startswith("http://") or spec.startswith("https://"):
        url, model = (spec.rsplit("#", 1) + [None])[:2] if "#" in spec else (spec, None)
        return CrossEncoderReranker(url, model=model), None
    raise ValueError(f"Unknown reranker spec: {spec!r}")


def _lms(*args):
    subprocess.run([LMS, *args], check=False)


def main() -> int:
    args = parse_args()
    os.environ.setdefault("OPENAI_API_KEY", "lm-studio")
    base_cfg = yaml.safe_load(args.config.read_text()) or {}
    embedder = LMStudioEmbedding(base_cfg)

    if not args.gold.exists():
        sys.exit(f"Gold not found: {args.gold}\nGenerate it once with scripts/eval_passage.py "
                 f"(its --gold output), then re-run this bake-off.")
    gold = json.loads(args.gold.read_text())
    probes = [PassageProbe(query=g["query"], source_id=g["source_id"],
                           gold_start=g["gold_start"], gold_end=g["gold_end"],
                           probe_id=g["probe_id"]) for g in gold]
    # Embed exactly the sources the gold references (+ distractors), same coords.
    targets = sorted({g["source_id"] for g in gold})
    corpus, labels = ep.load_corpus(args.source_root, targets, args.n_distractors,
                                    args.target_max_chars, args.distractor_max_chars,
                                    args.min_chars)
    chunk_cfg = ep.CONFIG_VARIANTS[args.variant]
    print(f"Corpus {len(corpus)} sources ({len(targets)} gold targets); "
          f"grain {args.variant} {chunk_cfg}; {len(probes)} probes; k_recall={args.k_recall}")
    coll, chunk_index, n_chunks, embed_secs = ep.build_collection(args.variant, corpus, chunk_cfg, embedder)
    print(f"built collection: chunks={n_chunks} embed={embed_secs}s\n")

    rows: List[Dict] = []
    for spec in args.rerankers:
        autoloaded = spec.split(":", 1)[1] if (args.autoload and spec.startswith("lmstudio:")) else None
        if autoloaded:
            print(f"loading {autoloaded} …", flush=True)
            _lms("load", autoloaded, "--yes", "--ttl", "3600")
        ep._RERANK_FAILURES.clear()
        if spec == "none":
            search = ep.make_chunk_search_fn(coll, embedder)
            timer = None
        else:
            backend, _ = make_backend(spec, base_cfg)
            timer = TimingReranker(backend)
            search = ep.make_reranked_search_fn(coll, embedder, timer, args.k_recall)

        rep = evaluate_passage(search, probes, chunk_index, k_values=(1, 3, 5, 10), expansions=(1,))
        s_per_q = round(timer.seconds / timer.calls, 2) if (timer and timer.calls) else 0.0
        row = {"reranker": spec,
               "hit@1": rep["passage_hit_at"].get("hit@1"),
               "hit@3": rep["passage_hit_at"].get("hit@3"),
               "hit@5": rep["passage_hit_at"].get("hit@5"),
               "mrr": rep["passage_mrr"],
               "strict@5": rep["strict_hit_at"].get("hit@5"),
               "s_per_query": s_per_q,
               "fallbacks": len(ep._RERANK_FAILURES)}
        rows.append(row)
        print(f"  {spec:34} hit@1={row['hit@1']} hit@5={row['hit@5']} mrr={row['mrr']} "
              f"strict@5={row['strict@5']} {row['s_per_query']}s/q fb={row['fallbacks']}", flush=True)
        if autoloaded:
            _lms("unload", autoloaded)

    rows.sort(key=lambda r: (r["mrr"] or 0, r["hit@5"] or 0), reverse=True)
    print("\n== reranker ranking (mrr, then hit@5) ==")
    for r in rows:
        print(f"  {r['reranker']:34} mrr={r['mrr']} hit@1={r['hit@1']} hit@3={r['hit@3']} "
              f"hit@5={r['hit@5']} strict@5={r['strict@5']} {r['s_per_query']}s/q fb={r['fallbacks']}")

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(
        {"grain": args.variant, "chunk_cfg": chunk_cfg, "probes": len(probes),
         "k_recall": args.k_recall, "gold": str(args.gold), "rows": rows}, indent=2) + "\n",
        encoding="utf-8")
    print(f"\nWrote {args.output_json}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config.example.yaml")
    p.add_argument("--source-root", type=Path, default=ep.DEFAULT_SOURCE_ROOT)
    p.add_argument("--variant", default="recursive_700_100", choices=list(ep.CONFIG_VARIANTS),
                   help="Fixed chunk grain to hold while sweeping rerankers.")
    p.add_argument("--rerankers", nargs="+",
                   default=["none", "lmstudio:google/gemma-4-e4b", "lmstudio:google/gemma-4-12b"],
                   help="Reranker specs (see module docstring).")
    p.add_argument("--k-recall", type=int, default=50, help="Candidates retrieved before rerank.")
    p.add_argument("--n-distractors", type=int, default=100)
    p.add_argument("--target-max-chars", type=int, default=4_000_000)
    p.add_argument("--distractor-max-chars", type=int, default=40_000)
    p.add_argument("--min-chars", type=int, default=3000)
    p.add_argument("--autoload", action=argparse.BooleanOptionalAction, default=True,
                   help="lms load each lmstudio model before testing and unload after.")
    p.add_argument("--gold", type=Path, default=REPO_ROOT / "output" / "eval" / "gold_passage_curated.json")
    p.add_argument("--output-json", type=Path, default=REPO_ROOT / "output" / "eval" / "rerank_bakeoff.json")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
