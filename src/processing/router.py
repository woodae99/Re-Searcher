"""Chunking router with adaptive logic."""

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.processing.chunker import TextChunker
from src.processing.chunkers.atomic import AtomicChunker
from src.processing.chunkers.hierarchical import HierarchicalChunker
from src.processing.chunkers.markdown import MarkdownChunker


class ChunkerRouter:
    """Route documents to appropriate chunkers based on metadata and content."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        chunking_config = config.get("chunking", {})
        self.defaults = chunking_config.get("defaults", chunking_config)
        self.huge_docs_config = chunking_config.get("huge_docs", {})
        self.markdown_enabled = chunking_config.get("markdown", {}).get("enabled", True)

        self.atomic_chunker = AtomicChunker()
        self.markdown_chunker = MarkdownChunker(config)
        self.hierarchical_chunker = HierarchicalChunker(config)
        self.default_chunker = TextChunker({"chunking": self.defaults})

    def chunk_with_metadata(
        self, text: str, metadata: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        if not text or not text.strip():
            return []

        source_type = metadata.get("source_type")
        doc_id = metadata.get("doc_id", "unknown")

        # Debug setup (avoid UnboundLocalError)
        debug = self.config.get("chunking", {}).get("debug_router", False)
        token_est = None
        if debug:
            token_est = len(text) // 4

        if source_type == "zotero_annotation":
            selected = "AtomicChunker"
            result = self.atomic_chunker.chunk_with_metadata(text, metadata)
        elif self.markdown_enabled and self._is_markdown(metadata, text):
            selected = "MarkdownChunker"
            result = self.markdown_chunker.chunk_with_metadata(text, metadata)
        elif self._is_huge_document(text):
            selected = "HierarchicalChunker"
            result = self.hierarchical_chunker.chunk_with_metadata(text, metadata)
        else:
            selected = "TextChunker"
            result = self.default_chunker.chunk_with_metadata(text, metadata)

        if debug:
            print(f"  [ROUTER] {doc_id}: {selected} (tokens~{token_est}, source={source_type})")

        return result

    def _is_markdown(self, metadata: Dict[str, Any], text: str) -> bool:
        if metadata.get("source_type") == "obsidian":
            return True

        file_path = metadata.get("file_path") or metadata.get("file_name")
        if file_path:
            if Path(str(file_path)).suffix.lower() == ".md":
                return True

        return bool(re.search(r"^#{1,6}\s+", text, re.MULTILINE))

    def _is_huge_document(self, text: str) -> bool:
        if not self.huge_docs_config.get("enabled", False):
            return False

        huge_doc_tokens = self.huge_docs_config.get("huge_doc_tokens", 25000)
        estimated_tokens = max(1, len(text) // 4)
        return estimated_tokens >= huge_doc_tokens
