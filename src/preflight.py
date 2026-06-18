"""Preflight validation for Re-Searcher pipeline.

This module ensures configuration is valid before running indexing operations,
preventing wasted overnight runs with misconfigured settings.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class PreflightError(Exception):
    """Raised when preflight validation fails."""

    pass


class PreflightWarning:
    """Represents a non-fatal warning during preflight."""

    def __init__(self, message: str):
        self.message = message


def get_git_commit() -> Optional[str]:
    """Get the current git commit hash, if available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def validate_config(
    config: Dict[str, Any],
    config_path: Path,
    allow_legacy_chunking: bool = False,
    allow_default_config: bool = False,
) -> Tuple[bool, List[str], List[PreflightWarning]]:
    """
    Validate configuration for safe indexing.

    Args:
        config: Parsed configuration dictionary
        config_path: Path to the config file (for display)
        allow_legacy_chunking: Deprecated; retained for CLI compatibility only.
        allow_default_config: If True, allow missing chunking block

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    errors: List[str] = []
    warnings: List[PreflightWarning] = []

    # Check for chunking block
    chunking = config.get("chunking")
    if chunking is None:
        if not allow_default_config:
            errors.append(
                "Config is missing 'chunking:' block. "
                "This is unsafe for production indexing.\n"
                "  Add a chunking configuration or use --allow-default-config to proceed."
            )
        else:
            warnings.append(
                PreflightWarning("No 'chunking:' block found - using defaults")
            )
        chunking = {}

    # Check chunking mode/router
    chunking_mode = chunking.get("mode", "v0.6_single_grain")
    router_enabled = chunking.get("router_enabled", False)
    if chunking_mode != "v0.6_single_grain":
        errors.append(
            "chunking.mode must be 'v0.6_single_grain'. "
            "The legacy hierarchical router was retired for v0.6."
        )
    if chunking_mode == "v0.6_single_grain" and not router_enabled:
        warnings.append(
            PreflightWarning(
                "chunking.router_enabled is false; v0.6 still uses the router for "
                "annotation, markdown, and text routing."
            )
        )

    # Check id_strategy
    id_strategy = chunking.get("id_strategy", "legacy")
    if id_strategy == "legacy":
        warnings.append(
            PreflightWarning(
                f"id_strategy is '{id_strategy}' - consider 'stable_hash' for deterministic IDs"
            )
        )

    # Check max_tokens_per_chunk
    max_tokens = chunking.get("max_tokens_per_chunk")
    embedding = config.get("embedding", {})
    context_length = embedding.get("context_length")

    if max_tokens is None and context_length is None:
        warnings.append(
            PreflightWarning(
                "Neither chunking.max_tokens_per_chunk nor embedding.context_length is set. "
                "This may cause embedder truncation."
            )
        )

    # Check storage configuration
    storage = config.get("storage", {})
    if not storage.get("collection_name"):
        warnings.append(PreflightWarning("No collection_name specified in storage config"))

    # Check embedding configuration
    if not embedding.get("provider"):
        warnings.append(PreflightWarning("No embedding provider specified"))

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


def print_config_header(
    config: Dict[str, Any],
    config_path: Path,
    git_commit: Optional[str] = None,
) -> None:
    """
    Print a formatted configuration header showing effective settings.

    Args:
        config: Parsed configuration dictionary
        config_path: Path to the config file
        git_commit: Optional git commit hash
    """
    # Get resolved absolute path
    resolved_path = config_path.resolve()

    # Extract config sections with defaults
    chunking = config.get("chunking", {})
    extraction = config.get("extraction", {})
    embedding = config.get("embedding", {})
    storage = config.get("storage", {})
    zotero = config.get("zotero", {})

    # Determine worker count
    workers = extraction.get("workers", "auto")
    if workers == "auto":
        workers_display = f"auto ({os.cpu_count()} CPUs)"
    else:
        workers_display = str(workers)

    # Build header
    width = 65
    border = "=" * width

    lines = [
        "",
        border,
        "Re-Searcher Configuration",
        border,
        f"Config path: {resolved_path}",
    ]

    if git_commit:
        lines.append(f"Git commit:  {git_commit}")

    lines.append("")
    lines.append("Chunking:")

    chunking_mode = chunking.get("mode", "v0.6_single_grain")
    lines.append(f"  mode:                 {chunking_mode}")

    router_enabled = chunking.get("router_enabled", False)
    router_marker = " <- enabled" if router_enabled else " <- disabled"
    lines.append(f"  router_enabled:       {router_enabled}{router_marker}")

    lines.append(f"  id_strategy:          {chunking.get('id_strategy', 'legacy')}")
    lines.append(
        f"  chunk_size:           {chunking.get('chunk_size', 'default')} / "
        f"overlap: {chunking.get('chunk_overlap', 'default')}"
    )
    lines.append(f"  max_tokens_per_chunk: {chunking.get('max_tokens_per_chunk', 'not set')}")
    lines.append(f"  oversize_policy:      {chunking.get('oversize_policy', 'not set')}")
    lines.append(f"  token_estimator:      {chunking.get('token_estimator', 'heuristic')}")

    lines.append("")
    lines.append("Extraction:")
    lines.append(f"  parallel: {extraction.get('parallel', False)}")
    lines.append(f"  workers:  {workers_display}")

    lines.append("")
    lines.append("Embedding:")
    lines.append(f"  provider:       {embedding.get('provider', 'not set')}")
    lines.append(f"  model:          {embedding.get('model', 'not set')}")
    lines.append(f"  context_length: {embedding.get('context_length', 'not set')}")

    lines.append("")
    lines.append("Storage:")
    lines.append(f"  collection: {storage.get('collection_name', 'not set')}")
    endpoint = storage.get("endpoint", "not set")
    lines.append(f"  endpoint:   {endpoint}")

    lines.append("")
    lines.append("Sources:")
    lines.append(f"  Zotero:   {'enabled' if zotero.get('enabled') else 'disabled'}")
    obsidian = config.get("obsidian", {})
    lines.append(f"  Obsidian: {'enabled' if obsidian.get('enabled') else 'disabled'}")

    lines.append(border)
    lines.append("")

    # Print all lines
    for line in lines:
        print(line)


def run_preflight(
    config: Dict[str, Any],
    config_path: Path,
    allow_legacy_chunking: bool = False,
    allow_default_config: bool = False,
    quiet: bool = False,
) -> bool:
    """
    Run full preflight validation.

    Args:
        config: Parsed configuration dictionary
        config_path: Path to the config file
        allow_legacy_chunking: Deprecated; retained for CLI compatibility only.
        allow_default_config: If True, allow missing chunking block
        quiet: If True, suppress header output (still show errors/warnings)

    Returns:
        True if validation passed, False otherwise
    """
    git_commit = get_git_commit()

    # Print config header unless quiet
    if not quiet:
        print_config_header(config, config_path, git_commit)

    # Validate configuration
    is_valid, errors, warnings = validate_config(
        config,
        config_path,
        allow_legacy_chunking=allow_legacy_chunking,
        allow_default_config=allow_default_config,
    )

    # Print warnings
    for warning in warnings:
        print(f"[WARNING] {warning.message}")

    if warnings:
        print()

    # Print errors and abort if invalid
    if not is_valid:
        print("=" * 65)
        print("PREFLIGHT VALIDATION FAILED")
        print("=" * 65)
        for error in errors:
            print(f"\n[ERROR] {error}")
        print()
        print("Aborting to prevent misconfigured indexing run.")
        print("Fix the errors above or use appropriate --allow-* flags.")
        print("=" * 65)
        return False

    if not quiet:
        print("[OK] Preflight validation passed\n")

    return True


def check_services(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Check that required services are available.

    Args:
        config: Parsed configuration dictionary

    Returns:
        Tuple of (all_ok, list of error messages)
    """
    errors = []

    # Check ChromaDB
    storage = config.get("storage", {})
    if storage.get("provider") == "chroma":
        endpoint = storage.get("endpoint", "http://localhost:8000")
        try:
            import chromadb
            from chromadb.config import Settings

            # Parse endpoint
            host = endpoint.replace("http://", "").replace("https://", "")
            port = 8000
            if ":" in host:
                host, port_str = host.split(":", 1)
                port = int(port_str.split("/")[0])

            client = chromadb.HttpClient(
                host=host,
                port=port,
                settings=Settings(anonymized_telemetry=False),
            )
            client.heartbeat()
        except Exception as e:
            errors.append(f"ChromaDB not available at {endpoint}: {e}")

    # Check embedding service
    embedding = config.get("embedding", {})
    if embedding.get("provider") == "lmstudio":
        endpoint = embedding.get("endpoint", "http://localhost:1234/v1")
        api_key = embedding.get("api_key")
        try:
            import httpx

            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            response = httpx.get(f"{endpoint}/models", headers=headers, timeout=5)
            if response.status_code != 200:
                errors.append(f"LM Studio not responding at {endpoint}")
        except Exception as e:
            errors.append(f"LM Studio not available at {endpoint}: {e}")

    return len(errors) == 0, errors
