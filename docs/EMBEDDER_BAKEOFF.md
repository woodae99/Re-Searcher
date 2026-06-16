# Embedder Bake-off — Results

**Date**: 2026-06-16 · **Host**: Sparky · **Script**: `scripts/eval_embedders.py`
· **Core**: `src/passage_eval.py`

The embedder is the one v0.6 decision baked into the rebuild — every stored vector
depends on it, so changing it means a full re-embed. It therefore deserves the same
measured treatment the chunk grain got, *before* the production rebuild. This is
that eval: grain fixed at 700/100, gold fixed (the curated 25-probe set), RAW
vector retrieval (no rerank, to isolate the embedder).

## Why now / leaderboard context

bge-m3 shipped Jan 2024. The current MTEB-multilingual leader is the **Qwen3-Embedding**
family (8B at 70.58), open-weight, with a matched Qwen3-Reranker. Gemini-Embedding
leads English but is a Google API (not local); NV-Embed / Harrier are huge / English.
So the test that matters for a *local* rebuild is bge-m3 vs Qwen3-Embedding, with
small local baselines for sanity.

**Fairness**: each family gets its intended prompt prefixes (`scripts/eval_embedders.py`
`PREFIXES`) — Qwen3 a query instruction, nomic `search_document:`/`search_query:`,
bge none — so no model is handicapped. Prefix strings are best-effort from model
cards and are themselves a tuning lever.

## Results (25 probes, 700/100 grain, raw retrieval)

| embedder | dim | MRR | hit@1 | hit@3 | hit@5 | strict@5 | embed (ch/s) | store @9.85M | rebuild* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **qwen3-embedding-0.6b** | 1024 | **0.763** | **0.72** | **0.80** | **0.84** | **0.80** | 26 | 40.3 GB | ~4.4 d |
| qwen3-embedding-4b | 2560 | 0.758 | 0.72 | 0.76 | 0.80 | 0.80 | 13 | 100.9 GB | ~8.5 d |
| bge-m3 (current) | 1024 | 0.707 | 0.64 | 0.72 | 0.80 | 0.76 | 105 | 40.3 GB | ~26 h |
| nomic-embed-text-v1.5 | 768 | 0.687 | 0.64 | 0.72 | 0.76 | 0.72 | 240 | 30.3 GB | ~11 h |

\* single-stream projection for 9.85M chunks at the measured throughput on Sparky
(unified memory). A discrete GPU (Bambino) and/or higher embedding concurrency
would cut this; treat it as a relative cost, not an absolute.
(EmbeddingGemma-300m was skipped — it's resident on Bambino, not the eval host, so
`lms load` couldn't find it here.)

## Findings

1. **Qwen3-Embedding-0.6B beats bge-m3 on every retrieval metric at identical cost
   per stored vector.** MRR 0.763 vs 0.707, hit@1 0.72 vs 0.64, hit@3 0.80 vs 0.72,
   strict@5 0.80 vs 0.76 — and it's the *same* 1024-dim, so the same ~40 GB store.
   This is a genuine drop-in quality upgrade, not a cost/quality trade on the
   storage axis.
2. **Bigger is not better here: Qwen3-4B is dominated.** Despite 2560-dim and 2.5×
   the storage (101 GB) and ~2× the embed time, it does *not* beat the 0.6B (it's
   marginally lower on MRR/hit@5, ties hit@1/strict). No reason to pay for it.
3. **The only cost of Qwen3-0.6B is embedding throughput** — ~26 ch/s vs bge-m3's
   ~105 (it's a causal-LM with last-token pooling, heavier per token than bge's
   BERT). That turns a ~1-day rebuild into ~4–5 days single-stream. Given the
   project stance (hard to build, easy to use; runtime quality is what matters),
   a one-time multi-day rebuild for materially better retrieval is the right trade
   — and throughput is partly recoverable with embedding concurrency / a GPU host.
4. **bge-m3 > nomic-v1.5** — the incumbent was a sound choice; nomic is cheaper and
   faster but worse. Sanity check passes.

## Recommendation

**Adopt `Qwen3-Embedding-0.6B` as the v0.6 embedder** (pending go-ahead, since it
commits the rebuild). Better retrieval than bge-m3 at the same storage footprint;
the 4B variant is not worth its cost.

**Implementation note — not just a config swap.** Production `LMStudioEmbedding`
embeds queries and documents identically. Qwen3-Embedding's advantage depends on an
**asymmetric query instruction** ("Instruct: …\nQuery:" on queries, nothing on
documents) — this eval applied it via `PrefixEmbedder`. To realise the measured
gain, the production embed path must apply the same query-only instruction (docs
raw). Without it, Qwen3 will underperform its numbers here.

## Caveats / follow-ups

- **25 probes** — the qwen3 > bge advantage is consistent across MRR, hit@1, hit@3,
  strict (a coherent pattern, not one probe), and MRR (aggregate) is the most
  robust signal. Still, widen the probe set before the final rebuild commit.
- **Raw retrieval only.** The embedder sets the recall ceiling, so a better
  embedder helps regardless of rerank — but re-confirm the gap end-to-end with the
  rerank stage (`scripts/eval_rerankers.py` style) on the top-2 embedders.
- **Matched reranker**: if Qwen3-Embedding is adopted, the natural reranker partner
  is **Qwen3-Reranker** — fold it into the reranker bake-off (`docs/RERANKER_BAKEOFF.md`).
- **Throughput / rebuild host (measured 2026-06-16).** qwen3-0.6b, same chunks,
  same code path. The dominant lever is **embedder context length**, not batch size
  or parallelism:
  - **Context (the big lever, ~2.2×):** Sparky unified runs at **27 ch/s** at the
    loaded 32768 context but **59 ch/s at context 1024** (same model + host). The
    32K window both halves throughput (llama.cpp per-forward-pass cost scales with
    allocated context) *and* causes the 25.2 GB VRAM for a 639 MB model. Chunks are
    ~200 tokens, so 1024 is ample — no quality cost.
  - **Host (~2×):** Bambino 5090 ≈ 57 ch/s vs Sparky 27 ch/s, both at 32K context.
  - **Batch size: flat** (32→256 ≈57 ch/s on the 5090) — llama.cpp does not
    batch-accelerate this causal-LM embedder.
  - **Parallel: minor (~+15%):** conc 4 → 68 ch/s vs 59 at conc 1; the `PARALLEL`
    column stays "-" for embedding instances (it's a generation setting).
  - **LM Studio ceiling:** ~60–80 ch/s regardless of host/batch (Bambino at ctx
    1024 measured 67→79 ch/s, conc 1→4) — the context lever helped the 5090 only
    ~18% (vs Sparky's 2.2×), so both hosts converge here. ~1.5-day rebuild.
  - **vLLM is the real lever (measured, decisive).** vLLM v0.20 (Docker,
    `--runner pooling`) on Sparky's GB10 sustains **~440 ch/s** (4k-chunk steady
    state; ~6× LM Studio) → **~6–7 h rebuild** for qwen3-0.6b. It scales with batch
    size (llama.cpp doesn't); client concurrency adds little because vLLM already
    batches internally. (A 600-chunk sample mis-read ~2500 ch/s — overhead noise;
    440 is the trustworthy figure.) **This should be the rebuild's embedding
    server** — and the same stack can host a BGE cross-encoder reranker.
  - **Setup**: `docker run --gpus all -p 8002:8000 -v ~/.cache/huggingface:/root/.cache/huggingface
    vllm/vllm-openai:v0.20.0 Qwen/Qwen3-Embedding-0.6B --runner pooling
    --max-model-len 1024 --gpu-memory-utilization 0.3` (arm64 image, runs on the
    GB10). LM Link routes normal requests fine — only an oversized (125-input) batch
    payload failed; moot now Bambino serves on the LAN (192.168.0.129:1234).

## Reproduce

```bash
# Gold from the passage eval; then:
.venv/bin/python scripts/eval_embedders.py --embedders \
  text-embedding-bge-m3 \
  text-embedding-qwen3-embedding-0.6b \
  text-embedding-qwen3-embedding-4b \
  text-embedding-nomic-embed-text-v1.5
# -> output/eval/embedder_bakeoff.json
```
