# Reranker Bake-off + BGE rerank-service notes

> **Production runtime settled (2026-06-16): vLLM native `/rerank` cross-encoder**
> (`CrossEncoderReranker`, `reranker_factory` `type: cross_encoder`, default
> `BAAI/bge-reranker-v2-m3`), managed by the same vLLM lifecycle — no separate
> TEI/Infinity service needed. This bake-off is now for *model A/B* (e.g. bge-reranker
> vs Qwen3-Reranker-0.6B), not for choosing the serving stack. See `docs/EMBEDDING_BACKEND.md`.

**Script**: `scripts/eval_rerankers.py` · **Core**: `src/passage_eval.py` ·
**Shares plumbing with**: `scripts/eval_passage.py`

The chunk grain is settled (recursive 700/100 — `docs/PASSAGE_EVAL.md`). The
*reranker* is a separate, swappable choice with its own speed/accuracy trade-off,
and it's the lever most likely to change as models come and go. This harness lets
you re-decide it later without re-deriving anything: it holds the grain and the
gold fixed, builds the collection once, and sweeps rerankers.

## What it answers

- Dense vs MoE, small vs large: e.g. `gemma-4-12b` (12B dense) vs
  `qwen3.6-35b-a3b` (35B MoE, 3B active) — accuracy *and* seconds/query.
- "Is the cheap reranker good enough?" — does a bigger model earn its latency?
- "How would a real cross-encoder (BGE) reranker compare?" — via the HTTP seam.

## Usage

```bash
# Gold is generated once by the passage eval; the bake-off reuses it.
.venv/bin/python scripts/eval_passage.py --rerank      # -> output/eval/gold_passage_curated.json

# Compare LM Studio models (auto-load/unload each; bge-m3 stays resident):
.venv/bin/python scripts/eval_rerankers.py \
  --rerankers none \
              lmstudio:google/gemma-4-e4b \
              lmstudio:google/gemma-4-12b \
              lmstudio:qwen/qwen3.6-35b-a3b

# Point at a cross-encoder service instead (see below):
.venv/bin/python scripts/eval_rerankers.py \
  --rerankers none "http://localhost:8080/rerank#BAAI/bge-reranker-v2-m3"
```

Output: a table of `hit@1 / hit@3 / hit@5 / MRR / strict@5 / s_per_query / fallbacks`
per reranker, plus `output/eval/rerank_bakeoff.json`. Metrics are the passage-level
ones from `docs/PASSAGE_EVAL.md`; only ranking changes between rerankers (density /
completeness are grain properties and are held fixed).

### Reranker specs

| spec | backend |
|---|---|
| `none` | raw vector order (baseline) |
| `http://host:port/rerank[#model]` | HTTP cross-encoder service (TEI / Infinity / vLLM) |

## What we already know (2026-06-16, on the 700/100 grain, 25 probes)

- **`gemma-4-e4b` (7.5B)**: ~11 s/query, and it recovered the chosen grain well
  (rr hit@1 0.80, hit@5 0.92, MRR 0.843, strict@5 0.88). This was an evaluation
  stand-in only; the v0.6 production reranker is the vLLM cross-encoder.
- **`gemma-4-12b` (dense)**: ~44 s/query for the same 30-candidate payload —
  reliable JSON but ~4× slower, so not worth it as a per-query reranker unless it
  proves materially more accurate.
- **Retired path**: the gemma/LM Studio JSON-score reranker was removed before
  production because the cross-encoder is purpose-built and does not rely on LLM
  JSON formatting.
- **Untested but interesting**: `qwen3.6-35b-a3b` (MoE) — the bake-off's reason for
  existing. The a3b's small active-parameter count *should* make it faster than a
  dense model of similar quality, but see VRAM.

## Memory / host notes

The two hosts have opposite constraints:

- **Sparky (128 GB unified memory)** — the eval/rebuild host. Large models *fit*
  (the 35b-a3b, even the 122b alongside bge-m3), so capacity is not the limit;
  unified-memory bandwidth is — big models run slower than on a discrete GPU, and
  `s_per_query` will show it. Good for "can the big model do it at all / how
  accurate" questions.
- **Bambino (RTX 5090, 32 GB VRAM)** — faster but capacity-bound. There `bge-m3`
  (~0.6 GB) + the reranker must fit in 32 GB: `gemma-4-e4b` (~8), `gemma-4-12b`
  (~13), `gemma-4-26b-a4b` (~28, tight) fit; `gemma-4-31b` (~34) and
  `qwen3.6-35b-a3b` (~38) would CPU-offload. Good for "is the *deployable* small
  model fast and good enough" questions.

Historical LM Studio bake-offs used `--autoload`; current v0.6 production tests
should target the HTTP `/rerank` cross-encoder service.

## BGE rerank service (the cross-encoder option to consider)

The natural partner to `bge-m3` embeddings is a **`bge-reranker`** cross-encoder
(`BAAI/bge-reranker-v2-m3`): it scores (query, passage) pairs directly rather than
asking an LLM to emit JSON, so it's typically far faster and more consistent than
an LLM reranker — and purpose-built for the job.

**The catch**: LM Studio has **no rerank endpoint** (it serves chat + embeddings
only). So a cross-encoder reranker must run as a *separate* service exposing a
`/rerank` HTTP API. Options, roughly in order of least friction:

- **HF Text Embeddings Inference (TEI)** — `text-embeddings-inference` serves
  cross-encoder/sequence-classification models with a `/rerank` route. Pairs
  cleanly with `bge-reranker-v2-m3`. Likely the lowest-effort path.
- **Infinity** (`michaelfeil/infinity`) — serves embeddings *and* reranking with a
  Jina-style `/rerank` API; could host bge-m3 + bge-reranker together.
- **vLLM** — can serve `score`/`rerank` for classification models; heavier, best if
  already in the stack for generation.

The bake-off's `CrossEncoderReranker` already speaks the common shape
(`POST {query, texts:[...]}` → `{results:[{index, score}]}`), so once a service is
up: `--rerankers "http://host:port/rerank#BAAI/bge-reranker-v2-m3"`. Adjust the
client if the chosen server's request/response differs.

**Promotion path**: if a cross-encoder wins the bake-off, promote
`CrossEncoderReranker` from this script into `src/retrieval/rerank.py` and wire
`reranker_factory.create_reranker` for `type: cross_encoder` (the config already
advertises that option) so production and the eval share one implementation —
mirroring the existing CLI/MCP parity convention.

## Open questions for the next run

1. Does `qwen3.6-35b-a3b` (MoE) beat `gemma-4-12b` on accuracy, and is its
   speed competitive despite offload?
2. Answered for v0.6: use `bge-reranker-v2-m3` via the vLLM `/rerank`
   cross-encoder service; the LLM JSON reranker path is retired.
3. Re-confirm the grain decision under the *best* reranker — 700 led under e4b; a
   stronger/cross-encoder reranker shouldn't change the ordering, but verify.
