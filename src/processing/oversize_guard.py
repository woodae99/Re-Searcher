"""Oversize guard to prevent embedder truncation.

This module provides a safety net that catches any chunks that exceed
the configured token limit, regardless of which chunker produced them.
It should run AFTER all routing/chunking and BEFORE embedding.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .token_utils import create_token_estimator, heuristic_token_estimate

logger = logging.getLogger(__name__)


@dataclass
class OversizeGuardStats:
    """Statistics from oversize guard processing."""

    passed: int = 0
    split: int = 0
    truncated: int = 0
    skipped: int = 0
    total_input: int = 0
    total_output: int = 0

    def summary(self) -> str:
        """Return a summary string of the stats."""
        return (
            f"Oversize Guard: {self.passed} passed | "
            f"{self.split} split | {self.truncated} truncated | {self.skipped} skipped"
        )


class OversizeGuard:
    """
    Guard that catches and handles chunks exceeding token limits.

    This is the last line of defence before embedding. Chunkers should
    aim to never emit pathological sizes, but this guard ensures no
    oversized chunks reach the embedder.
    """

    def __init__(
        self,
        max_tokens: int,
        policy: str = "split",
        token_estimator: str = "heuristic",
        reporter: Optional[Any] = None,
    ):
        """
        Initialize the oversize guard.

        Args:
            max_tokens: Maximum tokens allowed per chunk
            policy: How to handle oversize chunks - "split", "truncate", or "skip"
            token_estimator: Method for estimating tokens - "heuristic" or "model_tokenizer"
        """
        self.max_tokens = max_tokens
        self.policy = policy
        self.estimate_tokens = create_token_estimator(token_estimator)
        self.stats = OversizeGuardStats()
        self.reporter = reporter

        # Validate policy
        if policy not in ("split", "truncate", "skip"):
            raise ValueError(f"Unknown oversize_policy: {policy}")

    def process(
        self, chunks: List[Tuple[str, Dict[str, Any]]]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Process chunks, handling any that exceed max_tokens.

        Args:
            chunks: List of (text, metadata) tuples from chunker

        Returns:
            List of (text, metadata) tuples with oversize chunks handled
        """
        self.stats.total_input = len(chunks)
        result: List[Tuple[str, Dict[str, Any]]] = []

        for text, metadata in chunks:
            tokens = self.estimate_tokens(text)

            if tokens <= self.max_tokens:
                self.stats.passed += 1
                result.append((text, metadata))
            else:
                handled = self._handle_oversize(text, metadata, tokens)
                result.extend(handled)

        self.stats.total_output = len(result)
        return result

    def set_reporter(self, reporter: Any) -> None:
        self.reporter = reporter

    def _handle_oversize(
        self, text: str, metadata: Dict[str, Any], tokens: int
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Handle an oversize chunk according to policy.

        Args:
            text: The oversize chunk text
            metadata: The chunk metadata
            tokens: Estimated token count

        Returns:
            List of (text, metadata) tuples (may be empty, one, or many)
        """
        source_id = metadata.get("source_id", "unknown")
        chunk_level = metadata.get("chunk_level", "unknown")

        if self.policy == "split":
            self.stats.split += 1
            self._report_oversize("split", metadata, text, tokens)
            logger.warning(
                f"Splitting oversize chunk: source_id={source_id}, "
                f"level={chunk_level}, tokens={tokens} > {self.max_tokens}"
            )
            return self._recursive_split(text, metadata)

        elif self.policy == "truncate":
            self.stats.truncated += 1
            self._report_oversize("truncate", metadata, text, tokens)
            logger.warning(
                f"Truncating oversize chunk: source_id={source_id}, "
                f"level={chunk_level}, tokens={tokens} -> {self.max_tokens}"
            )
            truncated_text = self._truncate_to_tokens(text, self.max_tokens)
            new_metadata = {**metadata, "truncated": True, "original_tokens": tokens}
            return [(truncated_text, new_metadata)]

        else:  # skip
            self.stats.skipped += 1
            self._report_oversize("skip", metadata, text, tokens)
            logger.warning(
                f"Skipping oversize chunk: source_id={source_id}, "
                f"level={chunk_level}, tokens={tokens}"
            )
            return []

    def _report_oversize(
        self,
        action: str,
        metadata: Dict[str, Any],
        text: str,
        tokens: int,
    ) -> None:
        if self.reporter is None:
            return
        self.reporter.record(
            stage="oversize_guard",
            severity="warn",
            remediation="embedder_limit",
            message=f"Oversize chunk {action}",
            metadata=metadata,
            text_length=len(text),
            token_estimate=tokens,
            extra={
                "policy": self.policy,
                "action": action,
                "max_tokens": self.max_tokens,
                "chunk_level": metadata.get("chunk_level", ""),
            },
        )

    def _recursive_split(
        self, text: str, metadata: Dict[str, Any], depth: int = 0
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Recursively split text until all pieces are under max_tokens.

        Split strategy (in order):
        1. Paragraph boundaries (double newline)
        2. Sentence boundaries (. ? !)
        3. Fixed character window (last resort)

        Args:
            text: Text to split
            metadata: Original metadata
            depth: Recursion depth (for safety)

        Returns:
            List of (text, metadata) tuples
        """
        # Safety: prevent infinite recursion
        if depth > 10:
            logger.error(f"Recursive split exceeded depth limit, truncating")
            if self.reporter is not None:
                self.reporter.record(
                    stage="oversize_guard",
                    severity="error",
                    remediation="chunking",
                    message="Recursive oversize split exceeded depth limit",
                    metadata=metadata,
                    text_length=len(text),
                    token_estimate=self.estimate_tokens(text),
                    extra={"max_tokens": self.max_tokens},
                )
            return [(self._truncate_to_tokens(text, self.max_tokens), metadata)]

        tokens = self.estimate_tokens(text)
        if tokens <= self.max_tokens:
            return [(text, metadata)]

        # Try paragraph split first
        parts = self._split_on_paragraphs(text)
        if len(parts) > 1:
            return self._process_parts(parts, metadata, depth)

        # Try sentence split
        parts = self._split_on_sentences(text)
        if len(parts) > 1:
            return self._process_parts(parts, metadata, depth)

        # Last resort: fixed window split
        parts = self._split_fixed_window(text)
        return self._process_parts(parts, metadata, depth)

    def _process_parts(
        self, parts: List[str], metadata: Dict[str, Any], depth: int
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Process split parts, recursively splitting if needed."""
        result: List[Tuple[str, Dict[str, Any]]] = []
        original_chunk_id = metadata.get("chunk_id", "unknown")

        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            parent_variant = metadata.get("chunk_id_variant")
            chunk_variant = str(i) if parent_variant is None else f"{parent_variant}.{i}"

            # Create new metadata for split chunk
            new_metadata = {
                **metadata,
                "oversize_split": True,
                "split_from_chunk_id": original_chunk_id,
                "split_part": i,
                "split_total": len(parts),
                "chunk_id_variant": chunk_variant,
            }

            # Recursively process if still too large
            sub_results = self._recursive_split(part, new_metadata, depth + 1)
            result.extend(sub_results)

        return result

    def _split_on_paragraphs(self, text: str) -> List[str]:
        """Split text on paragraph boundaries (double newline)."""
        parts = re.split(r"\n\s*\n", text)
        return [p.strip() for p in parts if p.strip()]

    def _split_on_sentences(self, text: str) -> List[str]:
        """Split text on sentence boundaries."""
        # Split on . ? ! followed by space or end
        parts = re.split(r"(?<=[.?!])\s+", text)
        return [p.strip() for p in parts if p.strip()]

    def _split_fixed_window(self, text: str) -> List[str]:
        """Split text into fixed-size character windows."""
        # Use max_tokens * 3 chars as window size (conservative)
        window_size = self.max_tokens * 3
        overlap = window_size // 10  # 10% overlap

        parts = []
        start = 0
        while start < len(text):
            end = start + window_size
            part = text[start:end].strip()
            if part:
                parts.append(part)
            start += window_size - overlap

        return parts

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to approximately max_tokens."""
        # Use chars * 4 as rough token-to-char conversion
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0] + "..."

    def get_stats(self) -> OversizeGuardStats:
        """Get processing statistics."""
        return self.stats

    def reset_stats(self):
        """Reset statistics for a new run."""
        self.stats = OversizeGuardStats()


def create_oversize_guard(config: Dict[str, Any]) -> OversizeGuard:
    """
    Create an OversizeGuard from configuration.

    Args:
        config: Full configuration dictionary

    Returns:
        Configured OversizeGuard instance
    """
    chunking_config = config.get("chunking", {})
    embedding_config = config.get("embedding", {})

    # Get max tokens - use configured value or derive from context length
    max_tokens = chunking_config.get("max_tokens_per_chunk")
    if max_tokens is None:
        context_length = embedding_config.get("context_length", 8192)
        # Use 85% of context length as safe maximum
        max_tokens = int(context_length * 0.85)
        logger.info(
            f"No max_tokens_per_chunk set, using {max_tokens} "
            f"(85% of context_length={context_length})"
        )

    policy = chunking_config.get("oversize_policy", "split")
    token_estimator = chunking_config.get("token_estimator", "heuristic")

    return OversizeGuard(
        max_tokens=max_tokens,
        policy=policy,
        token_estimator=token_estimator,
    )
