"""Text chunking utilities for splitting documents into smaller segments."""

from typing import Any, Dict, List

from langchain_text_splitters import (
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
)


class TextChunker:
    """Handles text chunking with various strategies."""

    def __init__(self, config: Dict[str, Any]):
        chunking_config = config.get("chunking", {})

        self.chunk_size = chunking_config.get("chunk_size", 2048)
        self.chunk_overlap = chunking_config.get("chunk_overlap", 256)
        self.strategy = chunking_config.get("strategy", "character")

        # Initialize appropriate splitter
        if self.strategy == "character":
            self.splitter = CharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                separator="\n\n",
            )
        elif self.strategy == "recursive":
            self.splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
        else:
            # Default to recursive
            self.splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )

    def chunk_text(self, text: str) -> List[str]:
        """
        Split text into chunks.

        Args:
            text: Text to split

        Returns:
            List of text chunks
        """
        if not text or not text.strip():
            return []

        try:
            chunks = self.splitter.split_text(text)
            return [chunk.strip() for chunk in chunks if chunk.strip()]
        except Exception as e:
            print(f"⚠️  Error chunking text: {e}")
            # Fallback to simple chunking
            return self._simple_chunk(text)

    def _simple_chunk(self, text: str) -> List[str]:
        """
        Simple fallback chunking by character count.

        Args:
            text: Text to split

        Returns:
            List of text chunks
        """
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + self.chunk_size
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start += self.chunk_size - self.chunk_overlap

        return chunks

    def chunk_with_metadata(
        self, text: str, base_metadata: Dict[str, Any]
    ) -> List[tuple[str, Dict[str, Any]]]:
        """
        Chunk text and create metadata for each chunk.

        Args:
            text: Text to chunk
            base_metadata: Base metadata to include with each chunk

        Returns:
            List of (chunk_text, chunk_metadata) tuples
        """
        chunks = self.chunk_text(text)

        chunk_data = []
        for idx, chunk in enumerate(chunks):
            chunk_metadata = base_metadata.copy()
            chunk_metadata["chunk_index"] = idx
            chunk_metadata["total_chunks"] = len(chunks)
            chunk_data.append((chunk, chunk_metadata))

        return chunk_data
