"""Hierarchical chunker for huge documents."""

from typing import Any, Dict, List, Tuple

from src.processing.chunker import TextChunker


class HierarchicalChunker:
    """Chunk text into coarse, mid, and fine levels with lineage metadata."""

    def __init__(self, config: Dict[str, Any]):
        chunking_config = config.get("chunking", {})
        defaults = chunking_config.get("defaults", chunking_config)
        huge_docs = chunking_config.get("huge_docs", {})
        levels = huge_docs.get("levels", {})

        self.levels_config = {
            "coarse": {**defaults, **levels.get("coarse", {})},
            "mid": {**defaults, **levels.get("mid", {})},
            "fine": {**defaults, **levels.get("fine", {})},
        }

    def chunk_with_metadata(
        self, text: str, base_metadata: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        if not text or not text.strip():
            return []

        coarse_texts = self._chunk_level(text, "coarse")
        coarse_chunks: List[Tuple[str, Dict[str, Any]]] = []
        mid_chunks: List[Tuple[str, Dict[str, Any]]] = []
        fine_chunks: List[Tuple[str, Dict[str, Any]]] = []

        mid_counter = 0
        fine_counter = 0

        for coarse_idx, coarse_text in enumerate(coarse_texts):
            coarse_metadata = base_metadata.copy()
            coarse_metadata.update(
                {
                    "chunk_level": "coarse",
                    "chunk_index": coarse_idx,
                }
            )
            coarse_chunks.append((coarse_text, coarse_metadata))

            mid_texts = self._chunk_level(coarse_text, "mid")
            for mid_text in mid_texts:
                mid_metadata = base_metadata.copy()
                mid_metadata.update(
                    {
                        "chunk_level": "mid",
                        "chunk_index": mid_counter,
                        "parent_level": "coarse",
                        "parent_ordinal": coarse_idx,
                    }
                )
                mid_chunks.append((mid_text, mid_metadata))
                parent_mid_index = mid_counter
                mid_counter += 1

                fine_texts = self._chunk_level(mid_text, "fine")
                for fine_text in fine_texts:
                    fine_metadata = base_metadata.copy()
                    fine_metadata.update(
                        {
                            "chunk_level": "fine",
                            "chunk_index": fine_counter,
                            "parent_level": "mid",
                            "parent_ordinal": parent_mid_index,
                        }
                    )
                    fine_chunks.append((fine_text, fine_metadata))
                    fine_counter += 1

        self._apply_total_counts(coarse_chunks)
        self._apply_total_counts(mid_chunks)
        self._apply_total_counts(fine_chunks)

        return coarse_chunks + mid_chunks + fine_chunks

    def _chunk_level(self, text: str, level: str) -> List[str]:
        config = {"chunking": self.levels_config[level]}
        chunker = TextChunker(config)
        return chunker.chunk_text(text)

    def _apply_total_counts(self, chunks: List[Tuple[str, Dict[str, Any]]]):
        total = len(chunks)
        for _, metadata in chunks:
            metadata["total_chunks"] = total
