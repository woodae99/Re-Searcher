"""Chunker factory."""

from typing import Any, Dict

from src.processing.chunker import TextChunker
from src.processing.router import ChunkerRouter


def create_chunker(config: Dict[str, Any]):
    """Create chunker based on router settings."""
    chunking_config = config.get("chunking", {})
    if chunking_config.get("router_enabled", False):
        return ChunkerRouter(config)
    return TextChunker(config)
