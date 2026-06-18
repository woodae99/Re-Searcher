"""Reranker factory."""

from typing import Any, Dict

from src.retrieval.rerank import CrossEncoderReranker, NoRerank


def create_reranker(config: Dict[str, Any]):
    """Create reranker from config."""
    rerank_config = config.get("retrieval", {}).get("rerank", {})
    if not rerank_config.get("enabled", False):
        return NoRerank(config)

    rerank_type = rerank_config.get("type", "cross_encoder")
    if rerank_type == "cross_encoder":
        return CrossEncoderReranker(config)
    if rerank_type == "none":
        return NoRerank(config)

    raise ValueError(
        f"Unsupported rerank type '{rerank_type}'. "
        "v0.6 supports 'cross_encoder' and 'none'."
    )
