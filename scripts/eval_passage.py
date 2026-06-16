#!/usr/bin/env python3
"""Passage-level chunk-size eval: sharpen the v0.6 grain decision past saturation.

Where scripts/eval_chunking.py asks "which source?" (and saturates), this asks
the two questions chunk size actually governs, against real target texts plus a
large distractor pool:

  1. Passage retrieval — does a chunk overlapping a known answer span rank in the
     top k, competing with the *other* passages of the same source + distractors?
  2. completeness vs density under read-time neighbour expansion — did we recover
     the whole answer, and how much off-target text did we wade through to get it?

All heavy compute is local: BGE-M3 embeddings + a local LLM for gold-query
generation (both via LM Studio). Completeness/density are pure span arithmetic,
so there is no LLM judge — the loop spends zero hosted-model tokens. Raw vector
retrieval only (upstream of reranking), to isolate the chunking variable.

See docs/PASSAGE_EVAL.md for the design and src/passage_eval.py for the metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import chromadb  # noqa: E402
from langchain_text_splitters import (  # noqa: E402
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
)
from openai import OpenAI  # noqa: E402

from src.embedding.lmstudio import LMStudioEmbedding  # noqa: E402
from src.factories.reranker_factory import create_reranker  # noqa: E402
from src.passage_eval import (  # noqa: E402
    ChunkRef,
    PassageProbe,
    compare_passage_configs,
    evaluate_passage,
    expansion_window,
)
from src.retrieval_eval import EvalProbe, evaluate  # noqa: E402

# Rerank failures fall back to vector order rather than crashing the run.
_RERANK_FAILURES: List[str] = []

DEFAULT_SOURCE_ROOT = Path("/home/colin/Dev/Sources/Zotero/storage")

# Target texts (Zotero storage keys). Gold passages are drawn from these; they
# are embedded full-length so chapter/theme-distance retrieval is exercised.
DEFAULT_TARGETS = [
    "GE4NR393",  # Western 2012 - Coaching and Mentoring: A Critical Text (book)
    "LK8SJF7F",  # Bachkirova et al. 2016 - The SAGE Handbook of Coaching (handbook)
    "H5JHC3BL",  # Stokes et al. 2020 - "Two Sides of the Same Coin" (paper)
    "M545FD6Z",  # Salter & Gannon 2015 - Shared/Distinctive Aspects (paper)
    "DI67T6EK",  # Kamarudin et al. 2020 - Review of C&M Theories and Models (paper)
]

# Recursive size sweep for the 'mid' grain. 1200 was already shown to dip; the
# live contest is 500 vs 700 (cost no longer a constraint -> bias small).
CONFIG_VARIANTS = {
    "recursive_500_80":   {"strategy": "recursive", "chunk_size": 500,  "chunk_overlap": 80},
    "recursive_700_100":  {"strategy": "recursive", "chunk_size": 700,  "chunk_overlap": 100},
    "recursive_1000_150": {"strategy": "recursive", "chunk_size": 1000, "chunk_overlap": 150},
}

# Qualitative "what context do we get" probes — real process-research questions.
# Scored only by eyeball (printed reading windows), not by the gold metric.
MISSION_QUERIES = [
    "What does it mean to view coaching as a process rather than a goal-driven outcome?",
    "How does the coaching relationship develop and change over the course of an engagement?",
    "What are the phases or stages a coaching process moves through?",
    "How is reflective practice used as a process within coaching?",
    "What is the role of dialogue and meaning-making in the coaching process?",
]


def _ft_cache(root: Path, key: str) -> Path:
    return root / key / ".zotero-ft-cache"


def _label_for(root: Path, key: str) -> str:
    pdfs = list((root / key).glob("*.pdf"))
    return pdfs[0].stem[:60] if pdfs else key


def load_corpus(
    root: Path, targets: List[str], n_distractors: int,
    target_max_chars: int, distractor_max_chars: int, min_chars: int,
) -> Tuple[List[Tuple[str, str]], Dict[str, str]]:
    """Return ([(source_id, text)], {source_id: label}).

    Targets first (full text up to target_max_chars), then distractors drawn
    from other FT-cached sources (capped) until n_distractors is reached.
    """
    corpus: List[Tuple[str, str]] = []
    labels: Dict[str, str] = {}
    target_set = set(targets)

    for key in targets:
        cache = _ft_cache(root, key)
        if not cache.exists():
            print(f"  WARNING: target {key} has no FT cache — skipping", file=sys.stderr)
            continue
        text = cache.read_text(encoding="utf-8", errors="replace")[:target_max_chars]
        corpus.append((key, text))
        labels[key] = _label_for(root, key)

    n_added = 0
    for pdf in sorted(root.rglob("*.pdf")):
        if n_added >= n_distractors:
            break
        key = pdf.parent.name
        if key in target_set or key in labels:
            continue
        cache = pdf.parent / ".zotero-ft-cache"
        if not cache.exists():
            continue
        text = cache.read_text(encoding="utf-8", errors="replace")
        if len(text) < min_chars:
            continue
        corpus.append((key, text[:distractor_max_chars]))
        labels[key] = _label_for(root, key)
        n_added += 1

    return corpus, labels


def _make_splitter(cfg: Dict):
    """A faithful copy of TextChunker's splitter, plus add_start_index for offsets."""
    size, overlap, strategy = cfg["chunk_size"], cfg["chunk_overlap"], cfg["strategy"]
    if strategy == "character":
        return CharacterTextSplitter(chunk_size=size, chunk_overlap=overlap,
                                     separator="\n\n", add_start_index=True)
    return RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap,
                                          add_start_index=True)


def _spans_for(text: str, splitter) -> List[Tuple[str, int, int]]:
    """[(chunk_text, start, end)] — stripped to match the real pipeline, offsets
    realigned to the stripped text's position in the source."""
    out: List[Tuple[str, int, int]] = []
    for doc in splitter.create_documents([text]):
        raw, start = doc.page_content, doc.metadata["start_index"]
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        s = start + lead
        out.append((stripped, s, s + len(stripped)))
    return out


def build_collection(name: str, corpus, chunk_cfg: Dict, embedder):
    """Chunk (with offsets) + embed + store. Returns (collection, chunk_index, n)."""
    splitter = _make_splitter(chunk_cfg)
    ids, docs, metas = [], [], []
    chunk_index: Dict[str, List[ChunkRef]] = {}

    for source_id, text in corpus:
        refs: List[ChunkRef] = []
        for ordinal, (chunk, s, e) in enumerate(_spans_for(text, splitter)):
            cid = f"{source_id}#{ordinal}"
            ids.append(cid)
            docs.append(chunk)
            metas.append({"source_id": source_id, "ordinal": ordinal, "start": s, "end": e})
            refs.append(ChunkRef(source_id, ordinal, s, e, chunk))
        chunk_index[source_id] = refs

    t = time.perf_counter()
    vectors = embedder.embed_texts(docs)
    embed_secs = time.perf_counter() - t

    client = chromadb.EphemeralClient()
    coll = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
    B = 2000
    for i in range(0, len(ids), B):
        coll.add(ids=ids[i:i + B], embeddings=vectors[i:i + B],
                 metadatas=metas[i:i + B], documents=docs[i:i + B])
    return coll, chunk_index, len(ids), round(embed_secs, 1)


def make_chunk_search_fn(coll, embedder):
    def search_fn(query: str, k: int) -> List[ChunkRef]:
        qv = embedder.embed_query(query)
        res = coll.query(query_embeddings=[qv], n_results=k,
                         include=["metadatas", "documents"])
        metas = (res.get("metadatas") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        out = []
        for m, d in zip(metas, docs):
            out.append(ChunkRef(str(m.get("source_id")), int(m.get("ordinal", -1)),
                                int(m.get("start", 0)), int(m.get("end", 0)), d or ""))
        return out
    return search_fn


def build_reranker(base_cfg: Dict, model: str):
    """Production LLMReranker, configured for LM Studio with the given model.

    max_tokens is generous (2048) because the local gemma models emit
    reasoning_content before the JSON answer — a tight budget gets the reasoning
    truncated and returns empty content. Candidates/chars are bounded to keep
    per-call latency reasonable across the sweep.
    """
    cfg = json.loads(json.dumps(base_cfg))  # deep copy; don't mutate base_cfg
    cfg.setdefault("retrieval", {})["rerank"] = {
        "enabled": True, "type": "llm",
        "max_candidates": 20, "max_chars_per_candidate": 800,
        "llm": {"provider": "lmstudio", "model": model, "max_tokens": 2048, "temperature": 0.0},
    }
    return create_reranker(cfg)


def make_reranked_search_fn(coll, embedder, reranker, k_recall: int):
    """Vector-recall k_recall chunks, then rerank with the production reranker.

    Mirrors the production path: retrieve a wide recall set, rerank listwise, slice
    the top k. A rerank failure (bad JSON, timeout) falls back to vector order so a
    single bad call can't sink the run.
    """
    def search_fn(query: str, k: int) -> List[ChunkRef]:
        qv = embedder.embed_query(query)
        res = coll.query(query_embeddings=[qv], n_results=k_recall,
                         include=["metadatas", "documents", "distances"])
        metas = (res.get("metadatas") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        tuples = [(f"{m.get('source_id')}#{m.get('ordinal')}", d or "", 1.0 - float(dist), m)
                  for m, d, dist in zip(metas, docs, dists)]
        try:
            ranked = reranker.rerank(query, tuples)
        except Exception as exc:  # noqa: BLE001
            _RERANK_FAILURES.append(str(exc)[:80])
            ranked = tuples
        out = []
        for _cid, text, _score, m in ranked[:k]:
            out.append(ChunkRef(str(m.get("source_id")), int(m.get("ordinal", -1)),
                                int(m.get("start", 0)), int(m.get("end", 0)), text or ""))
        return out
    return search_fn


# --- gold curation: keep probes on substantive body prose ---------------------
_REF_PAREN = re.compile(r"\((?:19|20)\d{2}[a-z]?\)")
_REF_HINT = re.compile(r"\b(doi|https?://|retrieved from|pp\.|eds?\.|vol\.)\b", re.I)


def _looks_like_refs(passage: str) -> bool:
    """Heuristic: bibliography / reference-list / front-matter, not body prose."""
    if len(passage) < 200:
        return True
    head = passage[:140].lower()
    if any(h in head for h in ("references", "bibliography", "contents", "index")):
        return True
    # Dense citation markers are the tell for reference lists.
    return len(_REF_PAREN.findall(passage)) >= 3 or len(_REF_HINT.findall(passage)) >= 2


def _snap_start(text: str, start: int) -> int:
    """Advance to the next sentence start (capital after .!?), else next word."""
    m = re.search(r"[.!?]\s+([A-Z])", text[start:start + 300])
    if m:
        return start + m.start(1)
    sp = text.find(" ", start)
    return sp + 1 if 0 <= sp - start < 60 else start


def _sample_passage(text: str, frac: float, span_chars: int, max_tries: int = 5):
    """Return (aligned_start, stripped_passage) at/after frac, skipping refs/junk."""
    n = len(text)
    for t in range(max_tries):
        raw = int(n * frac) + t * int(n * 0.03)
        if raw >= n - 200:
            break
        s = _snap_start(text, raw)
        window = text[s:s + span_chars]
        lead = len(window) - len(window.lstrip())
        passage = window.strip()
        if len(passage) >= 200 and not _looks_like_refs(passage):
            return s + lead, passage
    return None


def generate_gold(corpus, targets, depths, model, endpoint, span_chars, use_llm=True):
    """Multiple passage probes per target (sampled at fractional depths).

    Passages are snapped to sentence boundaries and screened against a
    reference/front-matter heuristic, so gold queries describe substantive body
    prose rather than bibliography lines or mid-word fragments.
    """
    text_by_id = dict(corpus)
    client = OpenAI(base_url=endpoint, timeout=120)
    gold: List[Dict] = []
    for key in targets:
        text = text_by_id.get(key)
        if not text:
            continue
        for d in depths:
            sampled = _sample_passage(text, d, span_chars)
            if sampled is None:
                print(f"  [{key}@{d:.2f}] skipped (refs/front-matter/too short)", file=sys.stderr)
                continue
            start, passage = sampled
            query, how = "", "extractive"
            if use_llm:
                prompt = (
                    "Read this passage and write ONE specific research search query "
                    "(a question or noun phrase, <=20 words) that a coaching researcher "
                    "would type to find exactly this discussion. Use distinctive "
                    "terminology from the passage. Output ONLY the query.\n\nPASSAGE:\n"
                    + passage
                )
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=[{"role": "user", "content": prompt}],
                        temperature=0.2, max_tokens=1000,
                    )
                    content = (resp.choices[0].message.content or "").strip().strip('"')
                    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
                    if lines:
                        query, how = lines[-1].strip('"'), "llm"
                except Exception as exc:  # noqa: BLE001
                    print(f"  gold-gen error {key}@{d}: {str(exc)[:100]}", file=sys.stderr)
            if not query:
                frag = next((s for s in re.split(r"(?<=[.!?])\s+", passage)
                             if len(s.split()) >= 6), passage)
                query, how = " ".join(frag.split()[:18]), "extractive"
            gold.append({
                "probe_id": f"{key}@{d:.2f}", "query": query, "source_id": key,
                "gold_start": start, "gold_end": start + len(passage),
                "query_source": how, "passage_preview": passage[:160].replace("\n", " "),
            })
            print(f"  [{key}@{d:.2f}] ({how}) {query}", flush=True)
    return gold


def run_mission_probes(queries, variants_built, labels, expand_m=1, preview=320):
    """Print, per config, the top chunk + expanded window for each mission query."""
    print("\n" + "=" * 70 + "\nQUALITATIVE: mission-query reading windows\n" + "=" * 70)
    for q in queries:
        print(f"\nQ: {q}")
        for name, (search_fn, chunk_index) in variants_built.items():
            hits = search_fn(q, 1)
            if not hits:
                print(f"  [{name}] (no result)")
                continue
            top = hits[0]
            window = expansion_window(chunk_index.get(top.source_id, []), top.ordinal, expand_m)
            win_text = " ".join(c.text for c in window)
            label = labels.get(top.source_id, top.source_id)
            print(f"  [{name}] -> {label} (chunk #{top.ordinal}, window ±{expand_m})")
            print(f"      {win_text[:preview].strip()}…")


def main() -> int:
    args = parse_args()
    os.environ.setdefault("OPENAI_API_KEY", "lm-studio")
    base_cfg = yaml.safe_load(args.config.read_text()) or {}
    embedder = LMStudioEmbedding(base_cfg)
    endpoint = base_cfg.get("embedding", {}).get("endpoint", "http://localhost:1234/v1")

    targets = args.targets.split(",") if args.targets else DEFAULT_TARGETS
    depths = [float(x) for x in args.depths.split(",")]

    corpus, labels = load_corpus(args.source_root, targets, args.n_distractors,
                                 args.target_max_chars, args.distractor_max_chars,
                                 args.min_chars)
    n_targets = sum(1 for k, _ in corpus if k in set(targets))
    print(f"Corpus: {len(corpus)} sources ({n_targets} targets + "
          f"{len(corpus) - n_targets} distractors) from {args.source_root}")
    for k, _ in corpus[:n_targets]:
        print(f"  target {k}: {labels.get(k)}")

    if args.gold.exists():
        gold_raw = json.loads(args.gold.read_text())
        print(f"Loaded {len(gold_raw)} passage probes from {args.gold}")
    else:
        print(f"Generating passage gold with {args.llm_model}...")
        gold_raw = generate_gold(corpus, targets, depths, args.llm_model, endpoint,
                                 args.span_chars)
        args.gold.parent.mkdir(parents=True, exist_ok=True)
        args.gold.write_text(json.dumps(gold_raw, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(gold_raw)} probes -> {args.gold}")

    probes = [PassageProbe(query=g["query"], source_id=g["source_id"],
                           gold_start=g["gold_start"], gold_end=g["gold_end"],
                           probe_id=g["probe_id"], query_source=g.get("query_source", ""))
              for g in gold_raw]
    src_probes = [EvalProbe(query=g["query"], expected_source_ids=frozenset([g["source_id"]]),
                            probe_id=g["probe_id"]) for g in gold_raw]

    print(f"query_source mix: {dict(Counter(g.get('query_source', '?') for g in gold_raw))}")

    variants = (CONFIG_VARIANTS if args.variants == ["all"]
                else {k: CONFIG_VARIANTS[k] for k in args.variants})
    reranker = build_reranker(base_cfg, args.rerank_model) if args.rerank else None
    if reranker:
        print(f"Rerank ON: model={args.rerank_model} k_recall={args.k_recall}")

    raw_results: Dict[str, Dict] = {}
    rr_results: Dict[str, Dict] = {}
    built = {}

    def _line(tag, rep):
        return (f"  [{tag}] p_hit@5={rep['passage_hit_at'].get('hit@5')} "
                f"hit@1={rep['passage_hit_at'].get('hit@1')} hit@3={rep['passage_hit_at'].get('hit@3')} "
                f"mrr={rep['passage_mrr']} strict@5={rep['strict_hit_at'].get('hit@5')} "
                f"dens@1={rep['density'].get(1)}")

    for name, chunk_cfg in variants.items():
        print(f"\n== config {name}: {chunk_cfg} ==", flush=True)
        coll, chunk_index, n_chunks, embed_secs = build_collection(name, corpus, chunk_cfg, embedder)
        raw_search = make_chunk_search_fn(coll, embedder)
        report = evaluate_passage(raw_search, probes, chunk_index,
                                  k_values=(1, 3, 5, 10), expansions=(0, 1, 2))
        # Source-level too, for continuity with the saturated eval.
        src_report = evaluate(lambda q, k, _s=raw_search: [{"source_id": c.source_id}
                                                           for c in _s(q, k)],
                              src_probes, k_values=(1, 5))
        report["meta"] = {"chunks": n_chunks, "embed_secs": embed_secs,
                          "source_hit@5": src_report["hit_at"].get("hit@5")}
        raw_results[name] = report
        built[name] = (raw_search, chunk_index)
        print(f"  chunks={n_chunks} embed={embed_secs}s source_hit@5={report['meta']['source_hit@5']}")
        print(_line("raw", report))
        print(f"       completeness {report['completeness']}  density {report['density']}  "
              f"read_chars {report['read_chars']}")

        if reranker:
            rr_search = make_reranked_search_fn(coll, embedder, reranker, args.k_recall)
            rr_report = evaluate_passage(rr_search, probes, chunk_index,
                                         k_values=(1, 3, 5, 10), expansions=(0, 1, 2))
            rr_report["meta"] = {"chunks": n_chunks}
            rr_results[name] = rr_report
            built[name] = (rr_search, chunk_index)  # mission uses the production path
            print(_line("rerank", rr_report))

    def _print_table(results, tag):
        tbl = compare_passage_configs(results, headline_k=args.headline_k, headline_m=1)
        print(f"\n== {tag}: ranking (passage_hit@{args.headline_k}, then density@1) ==")
        for r in tbl:
            rep = results[r["config"]]
            print(f"  {r['config']:22} p_hit@{args.headline_k}={r.get('passage_hit@%d' % args.headline_k)} "
                  f"hit@1={rep['passage_hit_at'].get('hit@1')} hit@3={rep['passage_hit_at'].get('hit@3')} "
                  f"strict@5={r.get('strict_hit@%d' % args.headline_k)} mrr={r['passage_mrr']} "
                  f"compl@1={r.get('completeness@1')} dens@1={r.get('density@1')} "
                  f"read@1={r.get('read_chars@1')} chunks={r.get('chunks')}")
        return tbl

    raw_table = _print_table(raw_results, "RAW (vector only)")
    rr_table = _print_table(rr_results, "RERANK (production path)") if reranker else None
    if _RERANK_FAILURES:
        print(f"\n(rerank fell back to vector order {len(_RERANK_FAILURES)}× of "
              f"{len(probes) * len(variants)} calls; first: {_RERANK_FAILURES[0]})")

    if not args.no_mission:
        run_mission_probes(MISSION_QUERIES, built, labels)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(
        {"corpus_size": len(corpus), "n_targets": n_targets, "probes": len(probes),
         "query_source_mix": dict(Counter(g.get("query_source", "?") for g in gold_raw)),
         "rerank": {"enabled": bool(reranker),
                    "model": args.rerank_model if reranker else None,
                    "k_recall": args.k_recall, "failures": len(_RERANK_FAILURES)},
         "labels": labels,
         "raw": {"table": raw_table, "results": raw_results},
         "rerank_results": ({"table": rr_table, "results": rr_results} if reranker else None)},
        indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output_json}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config.example.yaml")
    p.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    p.add_argument("--targets", default="", help="Comma-separated Zotero keys (default: 5 coaching texts).")
    p.add_argument("--depths", default="0.2,0.35,0.5,0.65,0.78",
                   help="Fractional passage depths per target (body prose; avoids end-matter).")
    p.add_argument("--span-chars", type=int, default=600, help="Gold passage length.")
    p.add_argument("--n-distractors", type=int, default=100, help="Distractor sources to embed alongside targets.")
    p.add_argument("--target-max-chars", type=int, default=4_000_000, help="Per-target text cap (full book by default).")
    p.add_argument("--distractor-max-chars", type=int, default=40_000, help="Per-distractor text cap.")
    p.add_argument("--min-chars", type=int, default=3000, help="Skip tiny distractor caches.")
    p.add_argument("--variants", nargs="+", default=["all"])
    p.add_argument("--headline-k", type=int, default=5)
    p.add_argument("--no-mission", action="store_true", help="Skip qualitative reading-window dump.")
    p.add_argument("--rerank", action="store_true", help="Also evaluate the production LLM rerank path.")
    p.add_argument("--rerank-model", default="google/gemma-4-12b",
                   help="Rerank LLM (granite-4-micro is prod default but not downloaded locally).")
    p.add_argument("--k-recall", type=int, default=50, help="Candidates retrieved before rerank.")
    p.add_argument("--llm-model", default="google/gemma-4-12b")
    p.add_argument("--gold", type=Path, default=REPO_ROOT / "output" / "eval" / "gold_passage.json")
    p.add_argument("--output-json", type=Path, default=REPO_ROOT / "output" / "eval" / "passage_eval.json")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
