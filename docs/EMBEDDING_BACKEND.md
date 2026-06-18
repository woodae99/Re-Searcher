# Embedding Backend — vLLM (production) with a managed lifecycle

The v0.6 production embedder is **Qwen3-Embedding-0.6B served by vLLM**. vLLM gives
~6× LM Studio's embedding throughput (continuous batching; see
`docs/EMBEDDER_BAKEOFF.md`), but you never have to hand-drive it — it's wrapped in a
config-driven **stand-up → process → stand-down** lifecycle.

## How it fits together

```
config.yaml (embedding.provider: vllm)
        │
        ├── bulk jobs ─ scripts/index.py / reindex.py
        │     └─ managed_embedding_backend(config):   # stand up → index → stand down
        │            VLLMServer  ── docker run … ──►  vLLM :8002  (torn down after)
        │
        └── retrieval ─ query.py / MCP server
              └─ scripts/vllm_service.py start         # persistent vLLM for queries
                     VLLMServer (keep_up) ─► vLLM :8002 (stays up)
        ▼
  create_embedder(config) → VLLMEmbedding  (OpenAI-compatible client → vLLM)
```

- `src/embedding/vllm.py` — `VLLMEmbedding`: the OpenAI-compatible client pointed at
  vLLM. Inherits batching/dimension/fail-loud from `LMStudioEmbedding`.
- `src/embedding/vllm_server.py` — `VLLMServer` (docker lifecycle) +
  `managed_embedding_backend(config)` (no-op unless `provider==vllm` and managed).
- `scripts/vllm_service.py` — `start | stop | status` for the persistent retrieval server.

## The query instruction (don't skip this)

Qwen3-Embedding is **asymmetric**: queries must carry an instruction, documents must
not. `embedding.query_instruction` is prepended in `embed_query` only (documents in
`embed_texts` are embedded raw). Without it, Qwen3 loses the retrieval-quality edge
that justified choosing it over bge-m3. For symmetric models (bge-m3) set it to `""`.

**Index and query must use the same model** (same vectors). With persistent vLLM for
retrieval (the chosen setup), both paths hit the same server — consistent by design.

## Configuration (`embedding:` block)

Key fields (full example in `config.example.yaml`):

| field | meaning |
|---|---|
| `provider: vllm` | use the vLLM backend |
| `model` | HF id, e.g. `Qwen/Qwen3-Embedding-0.6B` |
| `query_instruction` | prepended to queries only (Qwen3 needs it; bge-m3 → `""`) |
| `context_length` / `vllm.managed.max_model_len` | keep small (~1024); chunks are ~200 tokens. 32K context made a 639 MB model use 25 GB VRAM |
| `vllm.base_url` | where the server is (`http://localhost:8002/v1`) |
| `vllm.managed.*` | docker image, container name, port, gpus, `runner: pooling`, gpu mem fraction, HF cache, startup timeout, `keep_up` |

## Operating it

```bash
# Bulk index/reindex: vLLM is auto-started and torn down around the run.
python scripts/index.py
python scripts/reindex.py --obsidian-all

# Interactive retrieval: start a persistent vLLM once, then search as usual.
python scripts/vllm_service.py start      # waits until ready (first run downloads+compiles)
python scripts/vllm_service.py status
python scripts/query.py "what is the coaching process" -k 5
python scripts/vllm_service.py stop
```

First start downloads the model (~1.2 GB) and compiles CUDA graphs (a few minutes);
subsequent starts are fast. The lifecycle is idempotent — a bulk job reuses a healthy
persistent server rather than restarting it.

## Throughput / host notes

vLLM ≈ 440 ch/s on Sparky's GB10 → ~6 h for the full ~9.85M-chunk rebuild (vs ~1.5 d
on LM Studio). Throughput scales with batch size (llama.cpp's doesn't). Bambino's
5090 (more bandwidth) would be faster still. Keep `gpu_memory_utilization` modest
(0.3) for the tiny 0.6B model; raise only if co-hosting a reranker on the same GPU.

## Falling back to LM Studio

LM Studio remains supported (`provider: lmstudio`) for interactive/graphical use —
just point it at the **same** model to keep vectors consistent with a vLLM-built
index (Qwen3-0.6B Q8 vs bf16 is near-lossless but not identical).
