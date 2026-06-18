# Passage-Level Chunking Evaluation — Results

**Date**: 2026-06-16
**Branch**: v0.6-rebuild (worktree)
**Host**: Sparky
**Status**: Sharpens the v0.6 chunk-*size* decision past the saturation limit of
the source-level eval (`docs/CHUNKING_EVAL.md`). Strategy (`recursive`) and grain
(single) are already settled; this is only about size.

## Why this eval exists

The source-level eval answers "which source?" and **saturates** (hit@5 = 1.0
across 500–1000), so it cannot separate chunk sizes. Chunk size actually governs
two things that are *invisible* to a source-level metric, and both are what the
thesis cares about — passage precision and not drowning a query in detail:

1. **Passage retrieval** — given a query whose answer is a known *span* of a known
   source, does a chunk overlapping that span rank in the top k, competing with
   the *other* passages of the same source plus a large distractor pool?
2. **Completeness vs density under read-time neighbour expansion** — around the
   best retrieved chunk we widen the reading window by *m* neighbours each side
   (modelling `get_chunk_context`) and measure:
   - **completeness(m)** = gold span covered / gold span length ("did we recover
     the whole answer?")
   - **density(m)** = gold span covered / total chars in the window ("answer per
     char read" — the don't-overwhelm lever).

All metrics are pure span arithmetic — deterministic, no LLM judge. The only LLM
use is local Gemma generating the gold queries. **Zero hosted-model tokens.**

## The measuring stick

- `src/passage_eval.py` (unit-tested, `tests/unit/test_passage_eval.py`): the
  metrics core — `passage_hit@k`, `passage_mrr`, strict (≥50%-of-span) variants,
  and the completeness/density/read-chars curves. Collection- and
  embedder-agnostic.
- `scripts/eval_passage.py`: orchestration. Loads target texts full-length +
  a distractor pool; generates multi-depth passage gold via local Gemma (the
  answer is the *span*, not just the source); chunks each config with a faithful
  copy of the production splitter (`add_start_index=True` → exact char offsets);
  embeds with real BGE-M3; scores; and dumps qualitative reading windows for a
  set of real process-research "mission" queries.

## Run

**Corpus: 105 sources** — 5 targets embedded full-length (Western 2012 *Coaching
and Mentoring: A Critical Text*; Bachkirova et al. 2016 *SAGE Handbook of
Coaching*; 3 papers — Stokes 2020, Salter & Gannon 2015, Kamarudin 2020) + 100
coaching/mentoring-adjacent distractors (≤40k chars each). **30 passage probes**
(6 depths × 5 targets; 23 LLM-generated, 7 extractive fallback). Raw vector
retrieval, no rerank. 33 min wall-clock (all local).

| config | chunks | embed | p_hit@1 | p_hit@3 | p_hit@5 | p_hit@10 | MRR | strict@5 | compl@±1 | density@±1 | read@±1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| recursive_500_80   | 22,612 | 203s | 0.500 | 0.667 | 0.800 | 0.833 | 0.607 | 0.533 | 0.933 | **0.527** | 1,105 |
| recursive_700_100  | 17,004 | 161s | 0.567 | 0.767 | 0.800 | 0.800 | 0.669 | 0.533 | 0.953 | **0.398** | 1,492 |
| recursive_1000_150 | 12,264 | 132s | 0.600 | 0.767 | 0.800 | 0.800 | 0.675 | 0.600 | 0.957 | **0.334** | 1,873 |

Full completeness/density curves across expansion m ∈ {0,1,2}:

| config | compl@0 | compl@1 | compl@2 | dens@0 | dens@1 | dens@2 |
|---|---:|---:|---:|---:|---:|---:|
| 500_80   | 0.563 | 0.933 | 0.983 | 0.780 | 0.527 | 0.338 |
| 700_100  | 0.679 | 0.953 | 0.998 | 0.689 | 0.398 | 0.254 |
| 1000_150 | 0.750 | 0.957 | 0.998 | 0.639 | 0.334 | 0.199 |

## Findings

1. **The eval is no longer saturated.** Bigger pool + passage gold drop
   `passage_hit@5` to 0.80 and even `source_hit@5` to 0.83–0.87 (was 1.0). It now
   discriminates — which is the whole point.

2. **`passage_hit@5` is a flat tie (0.80) but the sharper signals separate, and
   they favour *bigger*.** `hit@1` 0.50 → 0.567 → 0.60 and `hit@3` 0.667 → 0.767
   → 0.767 both rise with size; MRR 0.607 → 0.669 → 0.675; strict containment
   favours 1000. **500 fragments the answer** so the single best chunk ranks first
   less often. This is the predicted fragmentation floor showing up at 500.

3. **Completeness converges and drops out of the decision.** At ±1 expansion all
   three grains reach ~0.93–0.96 completeness; at ±2, ~0.98–1.0. With read-time
   neighbour expansion available, "did we recover the whole answer?" is *not* a
   differentiator — so the asymmetry argument (index small, expand at read time)
   holds: small chunks lose nothing on completeness once expanded.

4. **Density is where the grains genuinely differ, and it favours *smaller*.** At
   the ±1 operating point (equal completeness), density is 0.527 / 0.398 / 0.334
   and read-cost 1,105 / 1,492 / 1,873 chars. 500 delivers the same answer in
   ~40% less reading than 1000 — directly the "don't overwhelm the query" lever.

5. **The trade is therefore explicit:** ranking precision (favours bigger:
   hit@1/hit@3/MRR/strict) vs context economy (favours smaller: density,
   read-cost), with completeness and hit@5 tied. **700/100 is the balance point**
   — it matches 1000 on hit@3/MRR/completeness, nearly matches it on hit@1, while
   being ~20% denser and ~380 chars leaner per window, and it sits safely above
   the fragmentation floor that bites 500.

6. **Qualitative reading windows** (mission queries) return on-target,
   paragraph-coherent context at ±1 across all grains (e.g. Stelter "third-
   generation coaching" and the SAGE Handbook for *dialogue and meaning-making*;
   Shoukry & Fatien for *reflective practice as process*). Confirms ±1 expansion
   yields readable argument-level context even at 500.

## Recommendation (resolved)

**Land `recursive` 700/100 as the v0.6 `mid` grain.** The gating rerank eval (see
"Curated gold + rerank" below) resolves the 500-vs-700 question in favour of 700:
on the production rerank path 700 leads on every retrieval-quality metric — hit@1
0.80, hit@5 0.92, MRR 0.843, strict@5 0.88 — while 500's only remaining edge is a
modest density / read-cost saving (~360 chars per ±1 window). 1000/150 is
dominated by 700 (equal hit@1, lower hit@5, far lower density). This is now a
measured decision on passage + rerank evidence, not the saturated-eval default.

## Update — curated gold + rerank (2026-06-16)

Two follow-ups landed. **(1) Gold curation**: the passage sampler now snaps to
sentence boundaries and screens out reference-list / front-matter passages
(`_looks_like_refs`), and end-matter depths were dropped (defaults 0.2–0.78). This
removed the ~6 junk probes that depressed the first run; the curated set is 25
probes (21 LLM, 4 extractive). **(2) Rerank**: `scripts/eval_passage.py --rerank`
now scores the vLLM cross-encoder rerank path (`src/retrieval/rerank.py`
`CrossEncoderReranker`). Earlier small-LLM/JSON rerank experiments were retired
before the v0.6 production cutover.

Curated results (25 probes, 105 sources):

| config | source@5 | raw hit@1 | raw hit@5 | raw MRR | **rr hit@1** | **rr hit@5** | **rr MRR** | **rr strict@5** | density@1 | read@1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 500/80   | 0.92 | 0.64 | 0.88 | 0.725 | 0.72 | 0.88 | 0.798 | 0.72 | **0.529** | 1142 |
| 700/100  | 0.96 | 0.64 | 0.80 | 0.705 | **0.80** | **0.92** | **0.843** | **0.88** | 0.403 | 1505 |
| 1000/150 | 0.96 | 0.60 | 0.84 | 0.668 | 0.80 | 0.88 | 0.840 | 0.84 | 0.295 | 2106 |

1. **Curation sharpened the instrument.** Raw hit@5 rose to 0.80–0.88 and the
   junk-probe distortion is gone; the v1 "500 fragments → low hit@1" gap (0.50)
   largely washes out with clean gold (raw hit@1 ties at 0.64).
2. **Rerank lifts every grain** — hit@1 to 0.72–0.80, hit@3 to 0.88 across the
   board (2 of 75 calls fell back to vector order; negligible).
3. **It lifts 700 more than 500 — this resolves the gate.** After rerank, 700
   leads on hit@1 (0.80 vs 0.72), hit@5 (0.92 vs 0.88), MRR (0.843 vs 0.798), and
   most robustly **strict@5 (0.88 vs 0.72)**: 500's thin chunks retrieve the right
   passage but rarely contain ≥half the answer span, whereas 700 does. 500 keeps
   only its density edge (0.529 vs 0.403).
4. **Completeness still converges** (~0.97–0.99 at ±1) and **density is unchanged
   by rerank** (a grain property), as expected.

Caveat: 25 probes, so single-probe metric gaps (hit@5 0.92 vs 0.88 = one probe)
are within noise; the robust multi-probe signals favouring 700 are strict@5 (a
4-probe gap) and MRR. A stronger reranker (12B / granite) would likely raise all
grains, but the *ordering* (700 ≥ 500 on quality) matches the raw signal, so the
call is stable. Artifacts: `output/eval/gold_passage_curated.json`,
`output/eval/passage_eval_curated.json` (the v1 raw-only run is preserved).

## Honest limitations

- **Gold quality.** 7 of 30 probes are extractive fallbacks; depth-0.90 sampling
  often lands in reference lists / bibliographies (4 of 6 passage misses are these
  fragments). They depress and slightly distort the headline rates. **Next step:
  curate gold** — drop reference-list probes, keep substantive LLM probes, and
  sample depths that avoid end-matter.
- **No rerank.** Deliberate (isolates chunking); see the gating step above.
- **Chapter/theme tagging unavailable.** Flat FT-cache text has no surviving
  heading markup, so the expansion curve is the proxy for "how much context";
  structural metadata would need a structure-aware extractor.
- **Span gold ≈ paraphrase recall**, not absolute answer-quality; valid for
  *comparing* grains on a fixed gold set.

## Reproduce

```bash
# Needs LM Studio with text-embedding-bge-m3 loaded (lms load …) + an LLM for gold:
.venv/bin/python -m pytest tests/unit/test_passage_eval.py -q
.venv/bin/python scripts/eval_passage.py            # defaults: 5 targets, 100 distractors, 500/700/1000
# -> output/eval/gold_passage.json, output/eval/passage_eval.json
```
