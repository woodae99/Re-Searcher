#!/usr/bin/env python3
"""End-to-end chunking eval: sweep chunk configs through real BGE-M3 retrieval.

The measuring stick for the v0.6 chunking decision (grain / size / overlap). For
each chunking config it: chunks a small Zotero corpus with the *real* chunker,
embeds with the *real* BGE-M3 (LM Studio), stores in an ephemeral Chroma
collection, runs a gold set of known-item queries, and scores hit@k / MRR via
src/retrieval_eval.py. Raw vector retrieval only — upstream of reranking, to
isolate the chunking variable.

The gold set is generated once (LLM paraphrases a passage into a question, the
source becomes the expected answer) and cached to JSON so reruns are stable and
Colin can curate it. Auto-gold is approximate in absolute terms but valid for
*comparing* configs on a fixed corpus + gold set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import chromadb  # noqa: E402
from openai import OpenAI  # noqa: E402

from src.factories.chunker_factory import create_chunker  # noqa: E402
from src.embedding.lmstudio import LMStudioEmbedding  # noqa: E402
from src.retrieval_eval import EvalProbe, compare_configs, evaluate  # noqa: E402

DEFAULT_SOURCE_ROOT = Path("/home/colin/Dev/Sources/Zotero/storage")

# Single-grain configs to compare (v0.6 retires multi-level; router off).
# One character-strategy baseline (it can't split over-long paragraphs, so it
# under-chunks) plus a recursive size sweep for the 'mid' grain question.
CONFIG_VARIANTS = {
    "char_700_100":       {"strategy": "character", "chunk_size": 700,  "chunk_overlap": 100},
    "recursive_500_80":   {"strategy": "recursive", "chunk_size": 500,  "chunk_overlap": 80},
    "recursive_700_100":  {"strategy": "recursive", "chunk_size": 700,  "chunk_overlap": 100},
    "recursive_1000_150": {"strategy": "recursive", "chunk_size": 1000, "chunk_overlap": 150},
    "recursive_1200_200": {"strategy": "recursive", "chunk_size": 1200, "chunk_overlap": 200},
}


def load_corpus(root: Path, n_sources: int, max_chars: int, min_chars: int) -> List[Tuple[str, str]]:
    """Return [(source_id, text)] from Zotero FT caches (source_id = storage key)."""
    out: List[Tuple[str, str]] = []
    for pdf in sorted(root.rglob("*.pdf")):
        cache = pdf.parent / ".zotero-ft-cache"
        if not cache.exists():
            continue
        text = cache.read_text(encoding="utf-8", errors="replace")
        if len(text) < min_chars:
            continue
        out.append((pdf.parent.name, text[:max_chars]))
        if len(out) >= n_sources:
            break
    return out


def _extractive_query(passage: str) -> str:
    """Deterministic fallback: a distinctive sentence-ish fragment (>=6 words)."""
    import re
    for sentence in re.split(r"(?<=[.!?])\s+", passage):
        words = sentence.split()
        if len(words) >= 6:
            return " ".join(words[:18]).strip()
    return " ".join(passage.split()[:18]).strip()


def generate_gold(corpus: List[Tuple[str, str]], n_probes: int, model: str,
                  endpoint: str, use_llm: bool = True) -> List[Dict]:
    """Turn a passage from each source into a query (LLM paraphrase, else extractive).

    LLM paraphrase makes the query *semantic* (different wording from the chunk),
    which discriminates chunking quality; the extractive fallback keeps the gold
    set complete and reproducible when the LLM returns nothing.
    """
    client = OpenAI(base_url=endpoint, timeout=120)
    gold: List[Dict] = []
    for source_id, text in corpus[:n_probes]:
        start = max(0, int(len(text) * 0.3))  # ~30% in, past title/abstract furniture
        passage = text[start:start + 600].strip()
        if len(passage) < 200:
            continue
        query, how = "", "extractive"
        if use_llm:
            prompt = (
                "Read this passage and write ONE specific search query (a question or noun "
                "phrase, <=20 words) a researcher would type to find it. Use distinctive "
                "terminology. Output ONLY the query, nothing else.\n\nPASSAGE:\n" + passage
            )
            try:
                resp = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    temperature=0.2, max_tokens=1000,  # room for gemma reasoning + answer
                )
                content = (resp.choices[0].message.content or "").strip().strip('"')
                lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
                if lines:
                    query, how = lines[-1], "llm"
            except Exception as exc:  # noqa: BLE001
                print(f"  gold-gen LLM error for {source_id}: {str(exc)[:120]}", file=sys.stderr)
        if not query:
            query, how = _extractive_query(passage), "extractive"
        gold.append({"probe_id": source_id, "query": query, "expected_source_ids": [source_id],
                     "passage_preview": passage[:160], "query_source": how})
        print(f"  [{source_id}] ({how}) {query}", flush=True)
    return gold


def build_collection(name: str, corpus, chunk_cfg: Dict, base_cfg: Dict, embedder) -> Tuple[object, int, float]:
    """Chunk + embed + store the corpus into an ephemeral collection."""
    cfg = dict(base_cfg)
    cfg["chunking"] = {**chunk_cfg, "router_enabled": False}
    chunker = create_chunker(cfg)

    ids, docs, metas = [], [], []
    for source_id, text in corpus:
        for j, chunk in enumerate(chunker.chunk_text(text)):
            ids.append(f"{source_id}-{j}")
            docs.append(chunk)
            metas.append({"source_id": source_id})

    t = time.perf_counter()
    vectors = embedder.embed_texts(docs)
    embed_secs = time.perf_counter() - t

    client = chromadb.EphemeralClient()
    coll = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
    B = 2000
    for i in range(0, len(ids), B):
        coll.add(ids=ids[i:i + B], embeddings=vectors[i:i + B], metadatas=metas[i:i + B])
    return coll, len(ids), embed_secs


def make_search_fn(coll, embedder):
    def search_fn(query: str, k: int) -> List[Dict]:
        qv = embedder.embed_query(query)
        res = coll.query(query_embeddings=[qv], n_results=k, include=["metadatas"])
        metas = (res.get("metadatas") or [[]])[0]
        return [{"source_id": m.get("source_id")} for m in metas]
    return search_fn


def main() -> int:
    args = parse_args()
    # LM Studio ignores the key, but the OpenAI client requires one to be set.
    os.environ.setdefault("OPENAI_API_KEY", "lm-studio")
    base_cfg = yaml.safe_load(args.config.read_text()) or {}
    embedder = LMStudioEmbedding(base_cfg)
    endpoint = base_cfg.get("embedding", {}).get("endpoint", "http://localhost:1234/v1")

    corpus = load_corpus(args.source_root, args.n_sources, args.max_chars, args.min_chars)
    print(f"Corpus: {len(corpus)} sources from {args.source_root}")

    if args.gold.exists():
        gold_raw = json.loads(args.gold.read_text())
        print(f"Loaded {len(gold_raw)} gold probes from {args.gold}")
    else:
        print(f"Generating gold set with {args.llm_model}...")
        gold_raw = generate_gold(corpus, args.n_probes, args.llm_model, endpoint)
        args.gold.parent.mkdir(parents=True, exist_ok=True)
        args.gold.write_text(json.dumps(gold_raw, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(gold_raw)} gold probes -> {args.gold}")

    probes = [EvalProbe(query=g["query"], expected_source_ids=frozenset(g["expected_source_ids"]),
                        probe_id=g["probe_id"]) for g in gold_raw]

    variants = CONFIG_VARIANTS if args.variants == ["all"] else {k: CONFIG_VARIANTS[k] for k in args.variants}
    results: Dict[str, Dict] = {}
    for name, chunk_cfg in variants.items():
        print(f"\n== config {name}: {chunk_cfg} ==", flush=True)
        coll, n_chunks, embed_secs = build_collection(name, corpus, chunk_cfg, base_cfg, embedder)
        report = evaluate(make_search_fn(coll, embedder), probes, k_values=(1, 3, 5, 10))
        report["meta"] = {"chunks": n_chunks, "embed_secs": round(embed_secs, 1)}
        results[name] = report
        print(f"  chunks={n_chunks} embed={embed_secs:.1f}s "
              f"hit@5={report['hit_at']['hit@5']} mrr={report['mrr']} "
              f"found={report['found_rate']} mean_rank={report['mean_first_rank']}")

    table = compare_configs(results, headline_k=args.headline_k)
    print("\n== ranking (by hit@%d) ==" % args.headline_k)
    for row in table:
        print(f"  {row['config']:22} hit@{args.headline_k}={row.get('hit@%d' % args.headline_k)} "
              f"mrr={row['mrr']} found={row['found_rate']} mean_rank={row['mean_first_rank']} "
              f"chunks={row.get('chunks')}")

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({"corpus_size": len(corpus), "probes": len(probes),
                                            "table": table, "results": results}, indent=2) + "\n",
                                encoding="utf-8")
    print(f"\nWrote {args.output_json}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config.example.yaml")
    p.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    p.add_argument("--n-sources", type=int, default=40, help="Corpus size (retrieval pool).")
    p.add_argument("--n-probes", type=int, default=20, help="Gold queries to generate.")
    p.add_argument("--max-chars", type=int, default=50000, help="Cap per-source text (bound chunk count).")
    p.add_argument("--min-chars", type=int, default=3000, help="Skip tiny caches.")
    p.add_argument("--variants", nargs="+", default=["all"], help="Config variants to run.")
    p.add_argument("--headline-k", type=int, default=5)
    p.add_argument("--llm-model", default="google/gemma-4-12b")
    p.add_argument("--gold", type=Path, default=REPO_ROOT / "output" / "eval" / "gold_chunking.json")
    p.add_argument("--output-json", type=Path, default=REPO_ROOT / "output" / "eval" / "chunking_eval.json")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
