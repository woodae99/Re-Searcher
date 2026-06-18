"""Chunker factory."""

from typing import Any, Dict

from src.processing.router import ChunkerRouter


def create_chunker(config: Dict[str, Any]):
    """Create chunker based on the configured chunking mode."""
    chunking_config = config.get("chunking", {})
    mode = chunking_config.get("mode", "v0.6_single_grain")

    if mode == "v0.6_single_grain":
        return ChunkerRouter(config)

    raise ValueError(
        "chunking.mode must be 'v0.6_single_grain'. "
        "The legacy hierarchical router was retired for v0.6."
    )
