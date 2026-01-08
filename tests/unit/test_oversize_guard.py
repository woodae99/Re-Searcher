"""Unit tests for the oversize guard module.

Module: src/processing/oversize_guard.py
Tests the OversizeGuard class that prevents oversized chunks from reaching the embedder.
"""

import pytest

from src.processing.oversize_guard import OversizeGuard, create_oversize_guard


@pytest.mark.unit
class TestOversizeGuardBasic:
    """Basic oversize guard functionality."""

    def test_passes_small_chunk(self):
        """Chunks under the token limit should pass through unchanged."""
        guard = OversizeGuard(max_tokens=100, policy="split")
        chunks = [("This is a small chunk.", {"source_id": "doc-1"})]

        result = guard.process(chunks)

        assert len(result) == 1
        assert result[0][0] == "This is a small chunk."
        assert guard.stats.passed == 1
        assert guard.stats.split == 0

    def test_splits_large_chunk(self):
        """Chunks over the token limit should be split with 'split' policy."""
        guard = OversizeGuard(max_tokens=10, policy="split")
        # Create text that will definitely exceed 10 tokens (40+ chars)
        large_text = "This is paragraph one.\n\nThis is paragraph two.\n\nThis is paragraph three."
        chunks = [(large_text, {"source_id": "doc-1", "chunk_level": "mid"})]

        result = guard.process(chunks)

        # Should be split into multiple smaller chunks
        assert len(result) > 1
        assert guard.stats.split == 1
        assert guard.stats.passed == 0
        # All result chunks should have oversize_split metadata
        for text, metadata in result:
            assert metadata.get("oversize_split") is True

    def test_truncate_policy(self):
        """'truncate' policy should truncate oversized chunks."""
        guard = OversizeGuard(max_tokens=10, policy="truncate")
        large_text = "This is a very long text that exceeds the token limit significantly."
        chunks = [(large_text, {"source_id": "doc-1"})]

        result = guard.process(chunks)

        assert len(result) == 1
        assert guard.stats.truncated == 1
        assert guard.stats.passed == 0
        # Result should be truncated
        assert len(result[0][0]) < len(large_text)
        # Should have truncated metadata
        assert result[0][1].get("truncated") is True
        assert result[0][1].get("original_tokens") is not None

    def test_skip_policy(self):
        """'skip' policy should drop oversized chunks."""
        guard = OversizeGuard(max_tokens=10, policy="skip")
        large_text = "This is a very long text that exceeds the token limit significantly."
        chunks = [(large_text, {"source_id": "doc-1"})]

        result = guard.process(chunks)

        assert len(result) == 0
        assert guard.stats.skipped == 1
        assert guard.stats.passed == 0


@pytest.mark.unit
class TestOversizeGuardSplitting:
    """Test split behavior in detail."""

    def test_split_on_paragraphs(self):
        """Should prefer splitting on paragraph boundaries."""
        guard = OversizeGuard(max_tokens=10, policy="split")  # Very low limit to force split
        # Text with clear paragraph boundaries, each paragraph long enough to be its own chunk
        text = (
            "First paragraph has content that is longer than our token limit.\n\n"
            "Second paragraph also has content that exceeds the token limit.\n\n"
            "Third paragraph with more content here."
        )
        chunks = [(text, {"source_id": "doc-1"})]

        result = guard.process(chunks)

        # Should have split at paragraph boundaries (or further if needed)
        assert len(result) >= 2

    def test_split_on_sentences(self):
        """Should fall back to sentence splitting if no paragraphs."""
        guard = OversizeGuard(max_tokens=15, policy="split")
        # Text with sentences but no paragraph breaks
        text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence."
        chunks = [(text, {"source_id": "doc-1"})]

        result = guard.process(chunks)

        # Should have split at sentence boundaries
        assert len(result) >= 2

    def test_split_preserves_metadata(self):
        """Split chunks should preserve original metadata."""
        guard = OversizeGuard(max_tokens=10, policy="split")
        original_metadata = {
            "source_id": "doc-1",
            "source_type": "pdf",
            "chunk_level": "mid",
            "custom_field": "preserved",
        }
        large_text = "Part one content.\n\nPart two content.\n\nPart three content."
        chunks = [(large_text, original_metadata)]

        result = guard.process(chunks)

        for text, metadata in result:
            assert metadata.get("source_id") == "doc-1"
            assert metadata.get("source_type") == "pdf"
            assert metadata.get("custom_field") == "preserved"


@pytest.mark.unit
class TestOversizeGuardStats:
    """Test statistics tracking."""

    def test_stats_tracking(self):
        """Should track processing statistics correctly."""
        guard = OversizeGuard(max_tokens=50, policy="split")
        chunks = [
            ("Small chunk one.", {"source_id": "doc-1"}),
            ("Small chunk two.", {"source_id": "doc-1"}),
            ("This is a much larger chunk that will need to be split into multiple pieces." * 3, {"source_id": "doc-1"}),
        ]

        result = guard.process(chunks)

        assert guard.stats.total_input == 3
        assert guard.stats.passed == 2
        assert guard.stats.split == 1
        assert guard.stats.total_output >= 3  # At least 2 passed + split results

    def test_stats_reset(self):
        """reset_stats should clear all counters."""
        guard = OversizeGuard(max_tokens=100, policy="split")
        guard.process([("test", {})])
        guard.reset_stats()

        assert guard.stats.passed == 0
        assert guard.stats.split == 0
        assert guard.stats.truncated == 0
        assert guard.stats.skipped == 0
        assert guard.stats.total_input == 0
        assert guard.stats.total_output == 0

    def test_stats_summary(self):
        """stats.summary() should return a formatted string."""
        guard = OversizeGuard(max_tokens=100, policy="split")
        guard.process([("test", {})])

        summary = guard.stats.summary()

        assert "passed" in summary.lower()
        assert "split" in summary.lower()


@pytest.mark.unit
class TestOversizeGuardConfiguration:
    """Test configuration and factory function."""

    def test_invalid_policy_raises(self):
        """Invalid policy should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown oversize_policy"):
            OversizeGuard(max_tokens=100, policy="invalid_policy")

    def test_create_from_config(self):
        """create_oversize_guard should create guard from config dict."""
        config = {
            "chunking": {
                "max_tokens_per_chunk": 7000,
                "oversize_policy": "truncate",
                "token_estimator": "heuristic",
            },
            "embedding": {
                "context_length": 8192,
            },
        }

        guard = create_oversize_guard(config)

        assert guard.max_tokens == 7000
        assert guard.policy == "truncate"

    def test_create_from_config_derives_max_tokens(self):
        """Should derive max_tokens from context_length if not specified."""
        config = {
            "chunking": {
                "oversize_policy": "split",
            },
            "embedding": {
                "context_length": 8192,
            },
        }

        guard = create_oversize_guard(config)

        # Should be 85% of context_length
        expected = int(8192 * 0.85)
        assert guard.max_tokens == expected

    def test_create_from_minimal_config(self):
        """Should work with minimal config using defaults."""
        config = {}

        guard = create_oversize_guard(config)

        # Should use default 8192 context and derive max_tokens
        assert guard.max_tokens > 0
        assert guard.policy == "split"  # default
