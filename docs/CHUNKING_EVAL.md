# Chunking Evaluation — Measuring Stick + First Results

**Date**: 2026-06-15
**Branch**: `v0.6-rebuild`
**Host**: Sparky
**Status**: Eval loop + first results. Informs the v0.6 chunking decision (grain / size / overlap).

Same philosophy as the extraction work: decide chunking on measured retrieval,
not assumption. This note delivers the **retrieval eval loop** and the first
sweep over single-grain chunk configs.

## 1. The measuring stick

`src/retrieval_eval.py` (unit-tested) computes known-item retrieval metrics
(hit@k, MRR, first-rank) given a `search_fn` and a set of probes
(query → the source(s) that should come back). `scripts/eval_chunking.py` runs it
end to end:

1. Build a **gold set**: gemma-4-12b paraphrases a passage (~30% into each source)
   into a research query; the source is the expected answer. Cached to
   `output/eval/gold_chunking.json` (reproducible; curatable by hand). A
   deterministic extractive fallback keeps the set complete when the LLM returns
   nothing.
2. For each chunk config: chunk the corpus with the **real** chunker
   (`create_chunker`), embed with the **real** BGE-M3 (LM Studio), store in an
   ephemeral Chroma collection (cosine), run the gold queries, score.

Raw vector retrieval only — **upstream of reranking**, to isolate the chunking
variable. The probe is known-item (does a chunk from the expected *source* come
back, and how high), so it measures whether chunking preserves retrievability.

## 2. First sweep

**Corpus: 40 Zotero sources (FT cache, ≤50k chars each). 20 gold probes (17 LLM,
3 extractive).** Single working grain, router off (per the v0.6 plan).

| config | hit@1 | hit@3 | hit@5 | mrr | chunks | embed |
|---|---:|---:|---:|---:|---:|---:|
| `char_700_100` (baseline) | 0.75 | 0.90 | 0.95 | 0.838 | 661 | 18 s |
| `recursive_500_80` | 0.85 | **1.00** | 1.00 | **0.917** | 5132 | 43 s |
| `recursive_700_100` | 0.85 | **1.00** | 1.00 | **0.917** | 3801 | 34 s |
| `recursive_1000_150` | 0.85 | 0.95 | 1.00 | 0.913 | 2738 | 28 s |
| `recursive_1200_200` | 0.85 | 0.95 | 0.95 | 0.900 | 2311 | 26 s |

## 3. Findings

1. **Switch the strategy from `character` to `recursive` — decisive.** The
   `character` splitter only breaks on blank lines, so it cannot split paragraphs
   longer than the target: it emitted **661 oversized chunks** for the same corpus
   where `recursive` at the same size made 3,801 properly-bounded ones, and it was
   worst on every metric (hit@1 0.75 vs 0.85, MRR 0.838 vs ~0.917). This is the
   single most actionable result. (The current `TextChunker` default is
   `character`.)

2. **Retrieval is flat across recursive sizes 500–1000.** hit@1 is identical
   (0.85) and hit@3/5 differ by at most one probe. 1200 starts to dip
   (hit@5 0.95). So **source-level recall barely depends on chunk size** in the
   500–1000 band.

3. **Chunk count scales steeply with size** (5132 → 3801 → 2738 → 2311 for
   500/700/1000/1200). Since the rebuild cost is embedding ~millions of chunks,
   size is really a *cost* lever once retrieval is flat: `recursive_700` is 26%
   fewer chunks than `500` for identical retrieval; `1000` is another 28% fewer
   for a one-probe hit@3 cost.

## 4. Recommendation

- **Strategy: `recursive`** (not `character`). Unambiguous.
- **Grain: single `mid`** — confirmed sufficient here; nothing in this eval needs
  a second grain. (Add `coarse` only if a *survey-recall* eval later shows a gap;
  this eval is point-retrieval, not survey.)
- **Size: `recursive` 700 / overlap 100** as the v0.6 `mid` default — it sits on
  the Pareto front (top retrieval at 26% fewer chunks than 500). Use **1000 / 150**
  instead if minimizing the 9.85M-chunk rebuild cost outweighs a marginal recall
  edge. Avoid ≥1200 (retrieval dips). This validates the SPEC's existing `mid`
  size — with the critical correction that the *strategy* must be recursive.

## 5. Honest limitations (what this eval cannot yet decide)

- **It is saturated.** hit@5 = 1.0 and hit@1 = 0.85 across all recursive sizes:
  with 40 topically-distinct academic sources and queries carrying distinctive
  terminology, finding the right *source* is easy, so the metric can't separate
  fine size/overlap differences. The "recursive ≫ character" finding is robust;
  the precise optimum size is not yet pinned.
- **Source-level, not passage-level.** It scores whether the right *source* is
  retrieved, not whether the right *passage* ranks first. Chunk size matters most
  for passage precision and for how much irrelevant text rides along into reading
  / LLM context — smaller chunks help there, which nudges toward 700 over 1000.
  That effect is invisible to a source-level metric.
- **Auto-gold is approximate.** LLM queries can be answerable by more than the
  intended source in an overlapping-topic corpus. Valid for *comparing* configs
  on a fixed gold set; not an absolute recall number. The gold set is cached for
  Colin to curate.
- **Raw retrieval, no rerank.** Deliberate (isolates chunking); the production
  rerank stage is a separate eval.

## 6. Next steps to sharpen the size decision

1. **Harder eval**: enlarge the distractor pool (hundreds of sources) and/or add
   **passage-level gold** (mark the expected chunk, score hit@1 on passages) so
   chunk size actually discriminates. `scripts/eval_chunking.py` already scales;
   passage-level gold is the main addition.
2. Curate a small hand-written gold set of real thesis questions (highest-signal,
   domain-true) alongside the auto-gold.
3. Add a rerank stage to the eval to measure the full retrieval pipeline.
4. Land `chunking.strategy: recursive` + `mid` 700/100 as config defaults.

## Reproduce

```bash
./.venv/bin/python -m pytest tests/unit/test_retrieval_eval.py -q
# Needs LM Studio with text-embedding-bge-m3 + an LLM loaded (lms load ...):
./.venv/bin/python scripts/eval_chunking.py --n-sources 40 --n-probes 20
# -> output/eval/gold_chunking.json, output/eval/chunking_eval.json
```
