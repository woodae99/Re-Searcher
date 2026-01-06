"""Test fixtures for resumable indexing tests."""

from pathlib import Path
from typing import List

from src.sources.base import Document


def create_test_documents(count: int = 10) -> List[Document]:
    """
    Create sample test documents.

    Args:
        count: Number of test documents to create

    Returns:
        List of Document objects
    """
    documents = []

    for i in range(count):
        metadata = {
            "id": f"test_doc_{i:03d}",
            "title": f"Test Document {i}",
            "source": "test",
            "type": "test_document",
            "doc_index": i,
            "timestamp": "2025-01-05T00:00:00Z",
        }
        
        content = f"""Test Document {i}

This is a sample test document used for unit testing the indexing pipeline.
It contains multiple paragraphs of text to simulate real documents.

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor 
incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis 
nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.

Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore 
eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt 
in culpa qui officia deserunt mollit anim id est laborum.

This document covers topics including testing, indexing, and data processing.
It is designed to be chunked and embedded for search functionality testing.

Document number: {i}
Test iteration: 1
Quality: High
Category: Test
"""
        
        doc = Document(
            doc_id=f"test_doc_{i:03d}",
            content=content,
            metadata=metadata,
        )
        documents.append(doc)

    return documents


def get_test_fixtures_dir() -> Path:
    """Get path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


def get_test_config_path() -> Path:
    """Get path to test config file."""
    test_dir = Path(__file__).parent.parent
    return test_dir / "config.test.yaml"
