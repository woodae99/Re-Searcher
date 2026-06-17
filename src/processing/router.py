"""Chunking router with adaptive logic."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.processing.chunker import TextChunker
from src.processing.chunkers.atomic import AtomicChunker
from src.processing.chunkers.hierarchical import HierarchicalChunker
from src.processing.chunkers.markdown import MarkdownChunker

logger = logging.getLogger(__name__)


class ChunkerRouter:
    """Route documents to appropriate chunkers based on metadata and content."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        chunking_config = config.get("chunking", {})
        self.mode = chunking_config.get("mode", "v0.6_single_grain")
        self.defaults = chunking_config.get("defaults", chunking_config)
        self.huge_docs_config = chunking_config.get("huge_docs", {})
        self.markdown_enabled = chunking_config.get("markdown", {}).get("enabled", True)

        # Get expected chunk size for oversize detection
        self.expected_chunk_size = self.defaults.get("chunk_size", 2048)
        self.oversize_threshold = 1.2  # Log warning if chunk is 20% over expected

        self.atomic_chunker = AtomicChunker()
        self.markdown_chunker = MarkdownChunker(config)
        self.hierarchical_chunker = (
            HierarchicalChunker(config) if self.mode == "legacy_router" else None
        )
        self.default_chunker = TextChunker({"chunking": self.defaults})

    def chunk_with_metadata(
        self, text: str, metadata: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        if not text or not text.strip():
            return []

        source_type = metadata.get("source_type")
        doc_id = metadata.get("doc_id", "unknown")
        source_id = metadata.get("source_id", doc_id)

        # Debug setup (avoid UnboundLocalError)
        debug = self.config.get("chunking", {}).get("debug_router", False)
        token_est = len(text) // 4

        if source_type == "zotero_annotation":
            selected = "AtomicChunker"
            result = self.atomic_chunker.chunk_with_metadata(text, metadata)
        elif self.markdown_enabled and self._is_markdown(metadata, text):
            selected = "MarkdownChunker"
            result = self.markdown_chunker.chunk_with_metadata(text, metadata)
        elif self.mode == "legacy_router" and self._is_huge_document(text):
            selected = "HierarchicalChunker"
            result = self.hierarchical_chunker.chunk_with_metadata(text, metadata)
        else:
            selected = "TextChunker"
            result = self.default_chunker.chunk_with_metadata(text, metadata)

        if debug:
            print(f"  [ROUTER] {doc_id}: {selected} (tokens~{token_est}, source={source_type})")

        # Root cause logging: detect and log oversize chunks
        self._log_oversize_chunks(result, selected, source_id, source_type)

        return result

    def _log_oversize_chunks(
        self,
        chunks: List[Tuple[str, Dict[str, Any]]],
        chunker_name: str,
        source_id: str,
        source_type: str,
    ) -> None:
        """Log detailed info when chunks exceed expected size by >20%."""
        threshold_tokens = int(self.expected_chunk_size * self.oversize_threshold / 4)

        for chunk_text, chunk_metadata in chunks:
            estimated_tokens = len(chunk_text) // 4

            if estimated_tokens > threshold_tokens:
                # This chunk is significantly larger than expected
                chunk_level = chunk_metadata.get("chunk_level", "unknown")
                text_preview = chunk_text[:120].replace("\n", " ")

                # Try to determine why split failed
                reason = self._diagnose_oversize_reason(chunk_text)

                logger.warning(
                    f"Oversize chunk created: "
                    f"source_id={source_id}, "
                    f"source_type={source_type}, "
                    f"selected_chunker={chunker_name}, "
                    f"chunk_level={chunk_level}, "
                    f"estimated_tokens={estimated_tokens}, "
                    f"expected_max={threshold_tokens}, "
                    f"text_preview={text_preview!r}, "
                    f"reason={reason}"
                )

    def _diagnose_oversize_reason(self, text: str) -> str:
        """Try to diagnose why a chunk ended up oversized."""
        # Check for lack of paragraph breaks
        if "\n\n" not in text:
            return "no paragraph breaks found"

        # Check for lack of sentence boundaries
        if not re.search(r"[.?!]\s", text):
            return "no sentence boundaries found"

        # Check if it's mostly code
        code_indicators = ["def ", "class ", "function ", "import ", "const ", "var ", "{", "}"]
        code_count = sum(1 for ind in code_indicators if ind in text)
        if code_count >= 3:
            return "appears to be code block"

        # Check for very long lines (common in extracted PDFs)
        lines = text.split("\n")
        long_lines = [l for l in lines if len(l) > 500]
        if long_lines:
            return f"contains {len(long_lines)} very long lines (PDF extraction artifact?)"

        return "unknown - may need investigation"

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
