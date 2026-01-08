#!/usr/bin/env python3
"""Post-run verification script for Re-Searcher indexing.

This script verifies the integrity of an indexed ChromaDB collection:
- Chunk level distribution (fine/mid/coarse)
- Parent-child relationships
- Obsidian metadata coverage
- Legacy ID detection
"""

import argparse
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import chromadb
from chromadb.config import Settings


class VerificationResult:
    """Holds verification results."""

    def __init__(self, name: str):
        self.name = name
        self.passed = True
        self.messages: List[str] = []
        self.warnings: List[str] = []
        self.stats: Dict[str, Any] = {}

    def fail(self, message: str):
        self.passed = False
        self.messages.append(f"FAIL: {message}")

    def warn(self, message: str):
        self.warnings.append(f"WARN: {message}")

    def info(self, message: str):
        self.messages.append(f"INFO: {message}")

    def add_stat(self, key: str, value: Any):
        self.stats[key] = value


def connect_to_chroma(config: Dict[str, Any]) -> Tuple[Any, Any]:
    """Connect to ChromaDB and get collection."""
    storage = config.get("storage", {})
    endpoint = storage.get("endpoint", "http://localhost:8000")
    collection_name = storage.get("collection_name", "research_library")

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

    collection = client.get_collection(name=collection_name)
    return client, collection


def verify_chunk_level_distribution(collection) -> VerificationResult:
    """Check distribution of chunk levels."""
    result = VerificationResult("Chunk Level Distribution")

    # Get all metadatas
    total_count = collection.count()
    result.add_stat("total_chunks", total_count)

    if total_count == 0:
        result.fail("Collection is empty")
        return result

    # Sample chunks to check distribution (limit for large collections)
    sample_size = min(10000, total_count)

    # Get sample of chunks
    sample = collection.get(
        limit=sample_size,
        include=["metadatas"],
    )

    # Count chunk levels
    level_counts = Counter()
    for metadata in sample["metadatas"]:
        level = metadata.get("chunk_level", "unknown")
        level_counts[level] += 1

    result.add_stat("level_distribution", dict(level_counts))

    # Report distribution
    result.info(f"Total chunks: {total_count}")
    result.info(f"Sample size: {sample_size}")
    for level, count in sorted(level_counts.items()):
        pct = (count / sample_size) * 100
        result.info(f"  {level}: {count} ({pct:.1f}%)")

    # Check for expected levels
    if "fine" not in level_counts and "mid" not in level_counts:
        result.warn("No 'fine' or 'mid' chunk levels found - router may not be enabled")

    if level_counts.get("unknown", 0) > sample_size * 0.5:
        result.warn("More than 50% of chunks have unknown level")

    return result


def verify_parent_child_relationships(collection, sample_size: int = 100) -> VerificationResult:
    """Verify parent-child relationships are valid."""
    result = VerificationResult("Parent-Child Relationships")

    total_count = collection.count()
    if total_count == 0:
        result.fail("Collection is empty")
        return result

    # Get fine chunks with parent_id
    fine_chunks = collection.get(
        where={"chunk_level": "fine"},
        limit=sample_size * 2,
        include=["metadatas"],
    )

    if not fine_chunks["ids"]:
        result.info("No fine chunks found - skipping parent verification")
        return result

    # Sample fine chunks that have parent_id
    chunks_with_parent = [
        (chunk_id, metadata)
        for chunk_id, metadata in zip(fine_chunks["ids"], fine_chunks["metadatas"])
        if metadata.get("parent_id")
    ]

    if not chunks_with_parent:
        result.warn("No fine chunks have parent_id set")
        return result

    # Sample up to sample_size
    sample = random.sample(chunks_with_parent, min(sample_size, len(chunks_with_parent)))

    # Verify each sampled parent relationship
    parent_ids = [metadata["parent_id"] for _, metadata in sample]

    # Fetch parents
    try:
        parents = collection.get(
            ids=parent_ids,
            include=["metadatas"],
        )
    except Exception as e:
        result.fail(f"Error fetching parents: {e}")
        return result

    # Build parent lookup
    parent_lookup = {
        parent_id: metadata
        for parent_id, metadata in zip(parents["ids"], parents["metadatas"])
    }

    # Verify relationships
    missing_parents = 0
    wrong_level = 0
    source_mismatch = 0

    for chunk_id, child_metadata in sample:
        parent_id = child_metadata["parent_id"]
        child_source = child_metadata.get("source_id")

        if parent_id not in parent_lookup:
            missing_parents += 1
            continue

        parent_metadata = parent_lookup[parent_id]
        parent_level = parent_metadata.get("chunk_level")
        parent_source = parent_metadata.get("source_id")

        # Check parent level is 'mid'
        if parent_level != "mid":
            wrong_level += 1

        # Check source_id matches
        if child_source and parent_source and child_source != parent_source:
            source_mismatch += 1

    result.add_stat("sampled", len(sample))
    result.add_stat("missing_parents", missing_parents)
    result.add_stat("wrong_level", wrong_level)
    result.add_stat("source_mismatch", source_mismatch)

    result.info(f"Sampled {len(sample)} fine chunks with parent_id")

    if missing_parents > 0:
        result.fail(f"{missing_parents} chunks have parent_id pointing to non-existent parent")
    else:
        result.info("All sampled parents exist")

    if wrong_level > 0:
        result.warn(f"{wrong_level} parents are not 'mid' level")

    if source_mismatch > 0:
        result.fail(f"{source_mismatch} child/parent pairs have different source_id")
    else:
        result.info("All sampled child/parent pairs have matching source_id")

    return result


def verify_obsidian_metadata(collection) -> VerificationResult:
    """Check Obsidian-specific metadata coverage."""
    result = VerificationResult("Obsidian Metadata Coverage")

    # Get Obsidian chunks
    try:
        obsidian_chunks = collection.get(
            where={"source_type": "obsidian"},
            limit=5000,
            include=["metadatas"],
        )
    except Exception:
        # source_type might not exist
        result.info("Could not filter by source_type='obsidian'")
        return result

    if not obsidian_chunks["ids"]:
        result.info("No Obsidian chunks found")
        return result

    total = len(obsidian_chunks["ids"])
    result.add_stat("obsidian_chunks", total)
    result.info(f"Found {total} Obsidian chunks")

    # Check metadata coverage
    has_heading_path = sum(1 for m in obsidian_chunks["metadatas"] if m.get("heading_path"))
    has_contains_code = sum(1 for m in obsidian_chunks["metadatas"] if "contains_code" in m)
    has_tags = sum(1 for m in obsidian_chunks["metadatas"] if m.get("tags"))
    has_zotero_key = sum(1 for m in obsidian_chunks["metadatas"] if m.get("zotero_key"))
    has_aliases = sum(1 for m in obsidian_chunks["metadatas"] if m.get("aliases"))
    has_wikilinks = sum(1 for m in obsidian_chunks["metadatas"] if m.get("wikilinks"))
    has_links_out = sum(1 for m in obsidian_chunks["metadatas"] if m.get("links_out"))

    # Count notes with code blocks (where contains_code is True)
    with_code = sum(1 for m in obsidian_chunks["metadatas"] if m.get("contains_code") is True)

    result.add_stat("has_heading_path", has_heading_path)
    result.add_stat("has_contains_code", has_contains_code)
    result.add_stat("has_tags", has_tags)
    result.add_stat("has_zotero_key", has_zotero_key)
    result.add_stat("has_aliases", has_aliases)
    result.add_stat("has_wikilinks", has_wikilinks)
    result.add_stat("with_code_blocks", with_code)

    result.info(f"  heading_path:  {has_heading_path}/{total} ({has_heading_path/total*100:.1f}%)")
    result.info(f"  contains_code: {has_contains_code}/{total} ({has_contains_code/total*100:.1f}%) - {with_code} have code blocks")
    result.info(f"  tags:          {has_tags}/{total} ({has_tags/total*100:.1f}%)")
    result.info(f"  aliases:       {has_aliases}/{total} ({has_aliases/total*100:.1f}%)")
    result.info(f"  wikilinks:     {has_wikilinks}/{total} ({has_wikilinks/total*100:.1f}%)")
    result.info(f"  links_out:     {has_links_out}/{total} ({has_links_out/total*100:.1f}%)")
    result.info(f"  zotero_key:    {has_zotero_key}/{total} ({has_zotero_key/total*100:.1f}%)")

    return result


def verify_no_legacy_ids(collection, sample_size: int = 1000) -> VerificationResult:
    """Check that no IDs use legacy format."""
    result = VerificationResult("Legacy ID Check")

    total_count = collection.count()
    if total_count == 0:
        result.fail("Collection is empty")
        return result

    # Sample IDs
    sample = collection.get(
        limit=sample_size,
        include=[],  # Just need IDs
    )

    legacy_pattern = "-chunk-"
    legacy_ids = [id for id in sample["ids"] if legacy_pattern in id]

    result.add_stat("sampled", len(sample["ids"]))
    result.add_stat("legacy_ids_found", len(legacy_ids))

    if legacy_ids:
        result.fail(f"Found {len(legacy_ids)} IDs with legacy '-chunk-' pattern")
        result.info(f"Examples: {legacy_ids[:5]}")
    else:
        result.info(f"No legacy IDs found in sample of {len(sample['ids'])}")

    return result


def verify_collection_health(collection) -> VerificationResult:
    """Basic health check on collection."""
    result = VerificationResult("Collection Health")

    try:
        count = collection.count()
        result.add_stat("document_count", count)
        result.info(f"Document count: {count}")

        if count == 0:
            result.fail("Collection is empty")
        else:
            result.info("Collection is accessible and contains data")

    except Exception as e:
        result.fail(f"Error accessing collection: {e}")

    return result


def run_verification(config_path: Path, verbose: bool = False) -> bool:
    """
    Run all verification checks.

    Args:
        config_path: Path to configuration file
        verbose: If True, print detailed output

    Returns:
        True if all checks pass
    """
    # Load config
    with open(config_path) as f:
        config = yaml.safe_load(f)

    print("=" * 65)
    print("Re-Searcher Post-Run Verification")
    print("=" * 65)
    print(f"Config: {config_path.resolve()}")
    print(f"Collection: {config.get('storage', {}).get('collection_name', 'unknown')}")
    print("=" * 65)
    print()

    # Connect to ChromaDB
    try:
        client, collection = connect_to_chroma(config)
    except Exception as e:
        print(f"[FAIL] Could not connect to ChromaDB: {e}")
        return False

    # Run all verification checks
    results: List[VerificationResult] = []

    print("Running verification checks...\n")

    # 1. Collection health
    results.append(verify_collection_health(collection))

    # 2. Chunk level distribution
    results.append(verify_chunk_level_distribution(collection))

    # 3. Parent-child relationships
    results.append(verify_parent_child_relationships(collection))

    # 4. Obsidian metadata
    results.append(verify_obsidian_metadata(collection))

    # 5. Legacy IDs
    results.append(verify_no_legacy_ids(collection))

    # Print results
    print("\n" + "=" * 65)
    print("VERIFICATION RESULTS")
    print("=" * 65)

    all_passed = True
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        status_symbol = "[OK]" if result.passed else "[FAIL]"
        print(f"\n{status_symbol} {result.name}")

        if verbose or not result.passed:
            for msg in result.messages:
                print(f"    {msg}")
            for warn in result.warnings:
                print(f"    {warn}")

        if not result.passed:
            all_passed = False

    print("\n" + "=" * 65)
    if all_passed:
        print("OVERALL: PASS - All verification checks passed")
    else:
        print("OVERALL: FAIL - Some verification checks failed")
    print("=" * 65)

    return all_passed


def main():
    parser = argparse.ArgumentParser(
        description="Verify Re-Searcher indexing results"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output for all checks",
    )

    args = parser.parse_args()

    if not args.config.exists():
        print(f"Error: Configuration file not found: {args.config}")
        sys.exit(1)

    success = run_verification(args.config, verbose=args.verbose)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
