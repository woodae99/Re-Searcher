"""
Unit tests for text chunking functionality.

Module: src/processing/chunker.py
Tests the TextChunker class and chunking strategies.
"""

import pytest
from src.processing.chunker import TextChunker


@pytest.mark.unit
class TestTextChunkerBasic:
    """Basic chunking functionality tests."""

    def test_chunk_text_returns_list(self, text_chunker, sample_text):
        """Chunking should return a list of strings."""
        chunks = text_chunker.chunk_text(sample_text)
        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert all(isinstance(chunk, str) for chunk in chunks)

    def test_chunk_text_preserves_content(self, text_chunker, sample_text):
        """All content should be preserved when chunked."""
        chunks = text_chunker.chunk_text(sample_text)
        rejoined = "".join(chunks)
        
        # Should preserve all words (accounting for overlap)
        original_words = set(sample_text.split())
        rejoined_words = set(rejoined.split())
        assert original_words.issubset(rejoined_words)

    def test_chunk_text_respects_max_size(self, text_chunker, sample_text):
        """All chunks should respect the maximum chunk size."""
        chunks = text_chunker.chunk_text(sample_text)
        max_size = text_chunker.chunk_size
        
        for chunk in chunks:
            assert len(chunk) <= max_size * 1.1  # Allow 10% buffer for word boundaries

    def test_chunk_text_with_small_text(self, text_chunker):
        """Small text shorter than chunk size should work."""
        small_text = "This is a small test."
        chunks = text_chunker.chunk_text(small_text)
        
        assert len(chunks) >= 1
        assert small_text in "".join(chunks)

    def test_chunk_text_with_empty_text(self, text_chunker):
        """Empty text should be handled gracefully."""
        chunks = text_chunker.chunk_text("")
        assert isinstance(chunks, list)
        assert len(chunks) == 0

    def test_chunk_text_with_whitespace_only(self, text_chunker):
        """Whitespace-only text should be handled gracefully."""
        chunks = text_chunker.chunk_text("   \n\n   \t\t  ")
        assert isinstance(chunks, list)
        assert len(chunks) == 0


@pytest.mark.unit
class TestTextChunkerOverlap:
    """Test chunking with overlap functionality."""

    def test_chunks_have_overlap(self, text_chunker, sample_text):
        """With overlap configured, consecutive chunks should have common text."""
        if text_chunker.chunk_overlap == 0:
            pytest.skip("Test requires chunk_overlap > 0")
        
        chunks = text_chunker.chunk_text(sample_text)
        
        # Check that consecutive chunks have overlapping content
        for i in range(len(chunks) - 1):
            chunk1_words = set(chunks[i].split())
            chunk2_words = set(chunks[i + 1].split())
            overlap = chunk1_words & chunk2_words
            
            # Should have some overlap due to overlap configuration
            assert len(overlap) > 0, f"Chunks {i} and {i+1} have no overlap"

    def test_overlap_configuration(self, unit_config):
        """Chunker should initialize with correct overlap configuration."""
        chunker = TextChunker(unit_config)
        expected_overlap = unit_config.get("chunking", {}).get("chunk_overlap", 256)
        assert chunker.chunk_overlap == expected_overlap


@pytest.mark.unit
class TestTextChunkerStrategies:
    """Test different chunking strategies."""

    def test_character_strategy(self, unit_config, sample_text):
        """Character-based chunking should work."""
        config = unit_config.copy()
        config["chunking"]["strategy"] = "character"
        
        chunker = TextChunker(config)
        chunks = chunker.chunk_text(sample_text)
        
        assert len(chunks) > 0
        assert all(isinstance(chunk, str) for chunk in chunks)

    def test_recursive_strategy(self, unit_config, sample_text):
        """Recursive chunking should work."""
        config = unit_config.copy()
        config["chunking"]["strategy"] = "recursive"
        
        chunker = TextChunker(config)
        chunks = chunker.chunk_text(sample_text)
        
        assert len(chunks) > 0
        assert all(isinstance(chunk, str) for chunk in chunks)

    def test_invalid_strategy_defaults(self, unit_config, sample_text):
        """Invalid strategy should default to recursive."""
        config = unit_config.copy()
        config["chunking"]["strategy"] = "invalid_strategy"
        
        chunker = TextChunker(config)
        chunks = chunker.chunk_text(sample_text)
        
        # Should still work with default strategy
        assert len(chunks) > 0


@pytest.mark.unit
class TestTextChunkerEdgeCases:
    """Test edge cases and special characters."""

    def test_unicode_text(self, text_chunker, unicode_text):
        """Should handle unicode characters properly."""
        chunks = text_chunker.chunk_text(unicode_text)
        
        assert len(chunks) > 0
        rejoined = "".join(chunks)
        
        # Check unicode preservation
        assert "你好世界" in rejoined or "مرحبا" in rejoined

    def test_very_long_text(self, text_chunker, very_long_text):
        """Should handle very long text without errors."""
        chunks = text_chunker.chunk_text(very_long_text)
        
        assert len(chunks) > 1
        assert sum(len(chunk) for chunk in chunks) > len(very_long_text) * 0.9

    def test_text_with_special_formatting(self, text_chunker):
        """Should handle text with special formatting."""
        formatted_text = """
        ## Heading
        
        - Bullet 1
        - Bullet 2
        
        1. Numbered 1
        2. Numbered 2
        
        **Bold** and *italic* and `code`
        
        ```python
        def example():
            return "code block"
        ```
        """ * 10
        
        chunks = text_chunker.chunk_text(formatted_text)
        assert len(chunks) > 0

    def test_text_with_urls(self, text_chunker):
        """Should handle text containing URLs."""
        text_with_urls = """
        Check out this article: https://example.com/very/long/url?param=value&other=123
        
        And this email: test@example.com
        
        Some content here.
        """ * 20
        
        chunks = text_chunker.chunk_text(text_with_urls)
        assert len(chunks) > 0
        
        # URL should be preserved in some chunk
        joined = "".join(chunks)
        assert "example.com" in joined


@pytest.mark.unit
class TestTextChunkerConfiguration:
    """Test chunker configuration and initialization."""

    def test_default_configuration(self, unit_config):
        """Chunker should initialize with sensible defaults."""
        chunker = TextChunker(unit_config)
        
        assert chunker.chunk_size > 0
        assert chunker.chunk_overlap >= 0
        assert chunker.chunk_overlap < chunker.chunk_size
        assert chunker.strategy in ["character", "recursive"]

    def test_custom_chunk_size(self, unit_config):
        """Should respect custom chunk size."""
        config = unit_config.copy()
        config["chunking"]["chunk_size"] = 256
        
        chunker = TextChunker(config)
        assert chunker.chunk_size == 256

    def test_custom_chunk_overlap(self, unit_config):
        """Should respect custom chunk overlap."""
        config = unit_config.copy()
        config["chunking"]["chunk_overlap"] = 32
        
        chunker = TextChunker(config)
        assert chunker.chunk_overlap == 32

    def test_missing_config_uses_defaults(self):
        """Should use defaults when config keys are missing."""
        minimal_config = {}
        
        chunker = TextChunker(minimal_config)
        assert chunker.chunk_size > 0
        assert chunker.chunk_overlap >= 0
