"""Atomic chunker for single chunk documents."""

from typing import Any, Dict, List, Tuple


class AtomicChunker:
    """Return a single chunk for the full text."""

    def chunk_with_metadata(
        self, text: str, base_metadata: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        if not text or not text.strip():
            return []

        chunk_metadata = base_metadata.copy()
        chunk_metadata.update(
            {
                "chunk_index": 0,
                "total_chunks": 1,
                "chunk_level": "atomic",
            }
        )
        return [(text.strip(), chunk_metadata)]
