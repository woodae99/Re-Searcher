"""Embedding provider factory."""

from typing import Any, Dict

from src.embedding.lmstudio import LMStudioEmbedding
from src.embedding.openai import OpenAIEmbedding
from src.embedding.vllm import VLLMEmbedding


SUPPORTED_PROVIDERS = {"lmstudio", "openai", "vllm"}


def create_embedder(config: Dict[str, Any]):
    """Create embedding provider from config."""
    embedding_config = config.get("embedding", {})
    provider = embedding_config.get("provider", "lmstudio")

    if provider == "lmstudio":
        return LMStudioEmbedding(config)
    if provider == "vllm":
        return VLLMEmbedding(config)
    if provider == "openai":
        return OpenAIEmbedding(config)

    raise ValueError(
        f"Unsupported embedding provider '{provider}'. "
        f"Supported providers: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
    )
