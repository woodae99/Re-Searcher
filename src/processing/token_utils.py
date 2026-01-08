"""Token estimation utilities for chunk size management."""

from typing import Callable


def heuristic_token_estimate(text: str) -> int:
    """
    Estimate token count using character-based heuristic.

    Uses chars/4 as a conservative estimate. This tends to slightly
    overestimate token count, which is safer for avoiding truncation.

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count
    """
    if not text:
        return 0
    return len(text) // 4


def create_token_estimator(method: str = "heuristic") -> Callable[[str], int]:
    """
    Create a token estimation function based on the specified method.

    Args:
        method: Estimation method - "heuristic" or "model_tokenizer"

    Returns:
        Function that takes text and returns estimated token count
    """
    if method == "heuristic":
        return heuristic_token_estimate
    elif method == "model_tokenizer":
        # Future: could integrate with LM Studio's tokenizer endpoint
        # For now, fall back to heuristic
        return heuristic_token_estimate
    else:
        raise ValueError(f"Unknown token estimator method: {method}")


def calculate_safe_max_tokens(context_length: int, safety_margin: float = 0.85) -> int:
    """
    Calculate a safe maximum token limit given a context length.

    Args:
        context_length: The model's context window size
        safety_margin: Fraction of context to use (default 0.85 = 85%)

    Returns:
        Safe maximum token count
    """
    return int(context_length * safety_margin)
