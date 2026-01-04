"""Embedding providers for text vectorization."""

from .base import EmbeddingProvider
from .lmstudio import LMStudioEmbedding

__all__ = ["EmbeddingProvider", "LMStudioEmbedding"]
