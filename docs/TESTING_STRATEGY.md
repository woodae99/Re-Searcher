# Re-Searcher Testing Strategy

## Overview

This document outlines a comprehensive testing regime for Re-Searcher that covers:

- **Unit Tests**: Individual component functions and methods
- **Integration Tests**: Component interactions
- **Pipeline Tests**: End-to-end workflows with real data
- **Real-World Conditions**: Actual Zotero items, attachments, and Obsidian vault

---

## Testing Hierarchy

```
┌─────────────────────────────────────────────────┐
│         End-to-End Pipeline Tests               │
│  (Full workflow with real data sources)          │
├─────────────────────────────────────────────────┤
│      Integration Tests (Component chains)       │
│  (e.g., chunking + embedding, store + query)    │
├─────────────────────────────────────────────────┤
│          Unit Tests (Individual functions)      │
│  (Text extraction, chunking, embedding, etc.)   │
└─────────────────────────────────────────────────┘
```

---

## 1. Unit Tests (Foundation Layer)

### 1.1 Text Extraction (`tests/unit/test_extraction.py`)

**Module**: `src/extract_text.py`

```python
def test_extract_text_pdf():
    """Extract text from a real PDF"""

def test_extract_text_docx():
    """Extract text from a real DOCX"""

def test_extract_text_html():
    """Extract text from HTML files"""

def test_extract_text_markdown():
    """Extract text from markdown files"""

def test_extract_text_with_bad_encoding():
    """Handle PDF with encoding issues gracefully"""

def test_extract_text_empty_file():
    """Handle empty files"""

def test_extract_text_unsupported_format():
    """Gracefully reject unsupported file types"""
```

**Test Data**: Keep small sample files in `tests/fixtures/sample_files/`

- `sample.pdf` - Simple PDF
- `sample.docx` - Simple Word doc
- `sample.html` - Simple HTML
- `broken.pdf` - Corrupted/problematic PDF

---

### 1.2 Text Chunking (`tests/unit/test_chunking.py`)

**Module**: `src/processing/chunker.py`

```python
def test_chunk_text_basic():
    """Basic chunking produces expected number of chunks"""

def test_chunk_text_preserves_content():
    """All content is preserved (no text loss)"""

def test_chunk_overlap():
    """Overlapping chunks have expected overlaps"""

def test_chunk_size_constraints():
    """Chunks respect max size"""

def test_chunk_empty_text():
    """Handle empty or None text"""

def test_chunk_very_short_text():
    """Text shorter than chunk size"""

def test_chunk_strategies():
    """Both 'character' and 'recursive' strategies work"""

def test_chunk_unicode_text():
    """Proper handling of unicode/special characters"""
```

**Test Data**: Various lengths and formats of text

- Very short text
- Long document text
- Unicode text
- Empty/whitespace only

---

### 1.3 Embedding Generation (`tests/unit/test_embedding.py`)

**Module**: `src/embedding/lmstudio.py`

```python
def test_embed_single_text():
    """Single text produces valid embedding"""

def test_embed_batch_texts():
    """Batch embedding produces correct number of embeddings"""

def test_embed_dimension():
    """Embeddings have correct dimension (1024 for BGE-M3)"""

def test_embed_consistency():
    """Same text produces same embedding across calls"""

def test_embed_empty_text():
    """Empty text handling"""

def test_embed_very_long_text():
    """Very long text handling"""

def test_embed_special_characters():
    """Unicode and special character handling"""
```

**Requirements**: LM Studio running with text-embedding-bge-m3

---

### 1.4 Vector Storage (`tests/unit/test_storage.py`)

**Module**: `src/storage/chroma.py`

```python
def test_create_collection():
    """Create a new ChromaDB collection"""

def test_add_documents():
    """Add documents to collection"""

def test_add_documents_with_metadata():
    """Metadata properly stored"""

def test_query_documents():
    """Query returns relevant results"""

def test_query_with_filters():
    """Metadata filtering works"""

def test_delete_collection():
    """Clean up test collections"""

def test_collection_persistence():
    """Documents persist across reconnections"""
```

**Test Collections**: Use timestamped collection names like `test_storage_20260106_120000`

---

### 1.5 Data Source Base Classes (`tests/unit/test_sources.py`)

**Modules**: `src/sources/base.py`, `src/sources/zotero.py`, `src/sources/obsidian.py`

```python
def test_zotero_source_init():
    """ZoteroSource initializes with config"""

def test_zotero_is_enabled():
    """is_enabled() checks config"""

def test_zotero_validate_config():
    """validate_config() checks directory/database existence"""

def test_obsidian_source_init():
    """ObsidianSource initializes with config"""

def test_obsidian_is_enabled():
    """is_enabled() checks config"""

def test_obsidian_validate_config():
    """validate_config() checks vault path existence"""
```

---

## 2. Integration Tests

### 2.1 Source Integration (`tests/integration/test_source_integration.py`)

**Purpose**: Test real data source interactions

```python
def test_zotero_source_real_data():
    """
    Fetch actual items from real Zotero library
    - Requires: Real Zotero configured in config.yaml
    - Validates: Items with proper metadata returned
    - Checks: Authors, titles, dates, tags preserved
    """

def test_zotero_pdf_extraction():
    """
    Extract text from real PDF attachments
    - Requires: Zotero items with PDF attachments
    - Validates: Text extracted, searchable content generated
    - Checks: No data corruption, proper encoding
    """

def test_zotero_item_notes():
    """
    Process Zotero item notes
    - Validates: Notes properly formatted as documents
    - Checks: HTML to text conversion works
    """

def test_obsidian_vault_reading():
    """
    Read actual Obsidian vault
    - Requires: Real Obsidian vault configured
    - Validates: All markdown files discovered
    - Checks: Frontmatter parsing, content extraction
    """

def test_obsidian_wikilinks():
    """
    Extract and process wikilinks
    - Validates: Wikilinks detected in content
    - Checks: Link relationships preserved
    """
```

**Configuration**: Use `config.integration.yaml` with real Zotero/Obsidian paths

---

### 2.2 Processing Pipeline (`tests/integration/test_processing_chain.py`)

**Purpose**: Test component chains

```python
def test_extraction_to_chunking():
    """
    Real PDF → Extract text → Chunk
    - Uses real sample PDF
    - Validates: All text extracted, properly chunked
    """

def test_chunking_to_embedding():
    """
    Text chunks → Generate embeddings
    - Uses real LM Studio
    - Validates: All chunks get embeddings, consistent dimensions
    """

def test_embedding_to_storage():
    """
    Embeddings → Store in ChromaDB
    - Uses real ChromaDB instance
    - Validates: Documents searchable after storage
    """
```

---

## 3. Pipeline Tests (Orchestration Layer)

### 3.1 Fast Pipeline Test (`tests/pipeline/test_pipeline_fast.py`)

**Purpose**: Quick validation without heavy processing

```python
def test_zotero_obsidian_pipeline():
    """
    Full pipeline with real data (no PDF extraction)
    - Fetches: Real Zotero items (notes only)
    - Fetches: Real Obsidian notes
    - Chunks: All documents
    - Embeds: All chunks with real LM Studio
    - Stores: In test ChromaDB collection
    - Queries: Validates semantic search works

    Duration: ~5-15 minutes depending on library size
    """
```

**Configuration**: `config.pipeline-fast.yaml`

```yaml
zotero:
  enabled: true
  extract_attachments: false
  include_notes: true
  include_annotations: false

obsidian:
  enabled: true
  include_folders: ["Research", "Notes"] # Subset for speed

storage:
  collection_name: "test_pipeline_fast_TIMESTAMP"
```

---

### 3.2 Full Pipeline Test with Attachments (`tests/pipeline/test_pipeline_full.py`)

**Purpose**: Complete real-world test with PDF extraction

```python
def test_zotero_pdf_obsidian_pipeline():
    """
    Full pipeline with PDF attachment extraction
    - Fetches: Real Zotero items with PDFs
    - Extracts: Text from PDF files
    - Fetches: Real Obsidian notes
    - Chunks: All documents
    - Embeds: All chunks with real LM Studio
    - Stores: In test ChromaDB collection
    - Queries: Complex semantic queries

    Duration: 30+ minutes depending on PDF count
    Reports: Extraction stats, processing times, query results
    """
```

**Configuration**: `config.pipeline-full.yaml`

```yaml
zotero:
  enabled: true
  extract_attachments: true
  include_notes: true
  max_extraction_threads: 4

obsidian:
  enabled: true
```

---

### 3.3 Regression Test Suite (`tests/pipeline/test_regression.py`)

**Purpose**: Ensure updates don't break existing functionality

```python
def test_pipeline_produces_searchable_results():
    """
    Core functionality: Can find documents
    - Query: "machine learning"
    - Expected: Results returned with similarity > 0.3
    """

def test_pipeline_preserves_metadata():
    """
    Metadata integrity throughout pipeline
    - Check: Authors, dates, sources intact in results
    """

def test_pipeline_handles_unicode():
    """
    Special characters and unicode preserved
    - Query: Non-ASCII text
    - Expected: Results with correct encoding
    """
```

---

## 4. Test Organization Structure

```
tests/
├── conftest.py                          # Pytest fixtures (shared config, temp collections)
├── fixtures.py                          # Existing fixtures
├── fixtures/
│   ├── sample_files/
│   │   ├── sample.pdf
│   │   ├── sample.docx
│   │   ├── sample.html
│   │   └── broken.pdf
│   └── configs/
│       ├── config.unit.yaml             # Minimal config for unit tests
│       ├── config.integration.yaml      # Real Zotero/Obsidian, test ChromaDB
│       └── config.pipeline.yaml         # Full pipeline config
│
├── unit/
│   ├── __init__.py
│   ├── test_extraction.py              # PDF/DOCX/HTML text extraction
│   ├── test_chunking.py                # Text chunking logic
│   ├── test_embedding.py               # Embedding generation
│   ├── test_storage.py                 # ChromaDB operations
│   └── test_sources.py                 # Source initialization and validation
│
├── integration/
│   ├── __init__.py
│   ├── test_source_integration.py      # Real Zotero/Obsidian data
│   ├── test_processing_chain.py        # Component chains
│   └── test_data_flow.py               # Data integrity across pipeline
│
└── pipeline/
    ├── __init__.py
    ├── test_pipeline_fast.py           # Notes-only pipeline
    ├── test_pipeline_full.py           # With PDF extraction
    ├── test_regression.py              # Regression tests
    └── test_query_semantics.py         # Search quality tests
```

---

## 5. Running Tests

### Unit Tests Only (Fast - ~2 minutes)

```bash
pytest tests/unit/ -v
```

### Integration Tests (Medium - ~15 minutes)

```bash
pytest tests/integration/ -v
```

### Quick Pipeline Test (15-20 minutes)

```bash
pytest tests/pipeline/test_pipeline_fast.py -v
```

### Full Pipeline Test (1+ hour)

```bash
pytest tests/pipeline/test_pipeline_full.py -v -s
```

### Regression Test Suite (5 minutes)

```bash
pytest tests/pipeline/test_regression.py -v
```

### All Tests

```bash
pytest tests/ -v --tb=short
```

---

## 6. Test Configuration Management

### `conftest.py` - Shared Pytest Configuration

```python
"""Pytest configuration and shared fixtures."""

import pytest
from pathlib import Path
import yaml
import tempfile
from datetime import datetime

@pytest.fixture(scope="session")
def temp_test_dir():
    """Create temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)

@pytest.fixture
def unit_config():
    """Minimal config for unit tests."""
    with open("tests/fixtures/configs/config.unit.yaml") as f:
        return yaml.safe_load(f)

@pytest.fixture
def integration_config():
    """Config with real data sources."""
    with open("tests/fixtures/configs/config.integration.yaml") as f:
        return yaml.safe_load(f)

@pytest.fixture
def pipeline_config():
    """Full pipeline config."""
    with open("tests/fixtures/configs/config.pipeline.yaml") as f:
        return yaml.safe_load(f)

@pytest.fixture
def test_collection_name():
    """Generate unique collection name for each test."""
    return f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

@pytest.fixture
def chromadb_client(integration_config):
    """Provide ChromaDB client for tests."""
    from src.storage.chroma import ChromaVectorStore
    return ChromaVectorStore(integration_config).client
```

---

## 7. Test Coverage Goals

| Layer                  | Target Coverage | Priority |
| ---------------------- | --------------- | -------- |
| Unit - Text Extraction | 85%             | Critical |
| Unit - Chunking        | 90%             | Critical |
| Unit - Embedding       | 75%             | High     |
| Unit - Storage         | 80%             | Critical |
| Integration - Sources  | 70%             | High     |
| Integration - Chains   | 60%             | Medium   |
| Pipeline - Fast        | 100%            | Critical |
| Pipeline - Full        | 100%            | High     |

---

## 8. CI/CD Integration (GitHub Actions)

### Workflow: `tests-unit.yml`

- Triggers: Every push
- Duration: 2 minutes
- Fail: Block merge on failure

### Workflow: `tests-integration.yml`

- Triggers: Daily or on-demand
- Duration: 15 minutes
- Status: Report but don't block (requires actual Zotero/Obsidian)

### Workflow: `tests-pipeline.yml`

- Triggers: Manual or nightly
- Duration: 1+ hour
- Status: Report results

---

## 9. Data Management for Tests

### Real Data Sources

- **Zotero**: Use a test collection in your library (tag: "test-only")
- **Obsidian**: Use a test folder (e.g., "Testing/")
- **ChromaDB**: Always use timestamped test collections, clean up after tests

### Sample Files

Keep in `tests/fixtures/sample_files/`:

- Small, representative samples
- Different file formats
- Edge cases (corrupted, encoded, etc.)
- Version controlled (Git LFS if large)

### Cleanup Strategy

```python
@pytest.fixture(autouse=True)
def cleanup_test_collections(chromadb_client):
    """Remove test collections after each test."""
    yield
    collections = chromadb_client.list_collections()
    for collection in collections:
        if collection.name.startswith("test_"):
            chromadb_client.delete_collection(name=collection.name)
```

---

## 10. Continuous Improvement

### Metrics to Track

- Test execution time trends
- Code coverage percentage
- Failure rates and common failure modes
- Real-world data validation metrics

### Review Process

- Monthly: Analyze test failures and patterns
- Quarterly: Update test data and scenarios
- With each refactor: Run full test suite before and after

---

## Summary

This testing regime provides:

✅ **Unit Tests** - Fast feedback on individual components  
✅ **Integration Tests** - Verify component interactions  
✅ **Pipeline Tests** - Real-world end-to-end validation  
✅ **Regression Tests** - Prevent feature degradation  
✅ **Real Data** - Zotero PDFs, Obsidian vaults, actual embeddings  
✅ **CI/CD Ready** - Layers of test automation  
✅ **Maintainability** - Clear organization and fixtures

The testing pyramid ensures quick feedback during development (unit tests) while maintaining comprehensive real-world validation (pipeline tests).
