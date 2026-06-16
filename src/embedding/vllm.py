"""vLLM embedding provider.

vLLM serves an OpenAI-compatible ``/v1/embeddings`` endpoint, so the embedding
calls are identical to LM Studio's — this provider just resolves its endpoint and
model from the ``embedding.vllm`` config sub-block and inherits everything else
(batching, dimension caching, fail-loud errors, and the asymmetric
``query_instruction``) from :class:`LMStudioEmbedding`.

Why vLLM: measured ~6x the embedding throughput of LM Studio/llama.cpp on the same
hardware (continuous batching). See docs/EMBEDDER_BAKEOFF.md. Lifecycle (stand-up /
stand-down of the container) is handled separately by src/embedding/vllm_server.py.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from .lmstudio import LMStudioEmbedding


class VLLMEmbedding(LMStudioEmbedding):
    """OpenAI-compatible embedding client pointed at a vLLM server."""

    def __init__(self, config: Dict[str, Any]):
        vllm_cfg = (config.get("embedding", {}) or {}).get("vllm", {}) or {}
        # Re-point the parent's endpoint/model resolution at the vllm sub-block while
        # preserving everything else (query_instruction, batch_size, max_concurrent,
        # timeout, api_key) from the top-level embedding config.
        cfg = copy.deepcopy(config)
        emb = cfg.setdefault("embedding", {})
        if vllm_cfg.get("base_url"):
            emb["endpoint"] = vllm_cfg["base_url"]
        if vllm_cfg.get("model"):
            emb["model"] = vllm_cfg["model"]
        # Drop the lmstudio sub-block so it can't override the vLLM endpoint/model
        # (the parent prefers embedding.lmstudio.* over embedding.*).
        emb.pop("lmstudio", None)
        emb.setdefault("api_key", "EMPTY")  # vLLM ignores the key but the client needs one
        super().__init__(cfg)
