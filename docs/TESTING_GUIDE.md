# Re-Searcher Testing Guide

This document explains how to use the comprehensive testing infrastructure for Re-Searcher.

## Quick Start

### Run All Unit Tests (Fast - ~2 minutes)

```bash
pytest tests/unit/ -v
```

### Run Integration Tests (Real Data - ~15 minutes)

Requires: Zotero configured, Obsidian vault available

```bash
pytest tests/integration/ -v
```

### Run Quick Pipeline Test (Simulated - ~5 minutes)

```bash
pytest tests/pipeline/ -v -k "test_pipeline_fast"
```

### Run Full Pipeline Test (Complete - 30+ minutes)

```bash
pytest tests/pipeline/test_pipeline_full.py -v -s
```

### Run Specific Test

```bash
pytest tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_returns_list -v
```

---

## Test Organization

```
tests/
├── conftest.py                          # Shared fixtures and configuration
├── fixtures.py                          # Existing test fixtures
├── fixtures/
│   ├── sample_files/                   # Sample test documents
│   │   ├── sample.pdf
│   │   ├── sample.docx
│   │   └── sample.html
│   └── configs/
│       ├── config.unit.yaml             # Unit test config (no real sources)
│       ├── config.integration.yaml      # Integration test config (real Zotero/Obsidian)
│       └── config.pipeline.yaml         # Pipeline test config (full setup)
│
├── unit/                                # Fast, isolated component tests
│   ├── test_chunking.py                # Text chunking
│   ├── test_embedding.py               # Embedding generation
│   ├── test_extraction.py              # PDF/document text extraction
│   ├── test_storage.py                 # ChromaDB operations
│   └── test_sources.py                 # Source initialization/validation
│
├── integration/                         # Real data component interactions
│   ├── test_source_integration.py      # Real Zotero/Obsidian data
│   ├── test_processing_chain.py        # Component chains
│   └── test_data_flow.py               # Data integrity tests
│
└── pipeline/                            # End-to-end workflows
    ├── test_pipeline_fast.py           # Notes-only pipeline
    ├── test_pipeline_full.py           # With PDF extraction
    ├── test_regression.py              # Regression tests
    └── test_query_semantics.py         # Search quality
```

---

## Test Configuration

### Unit Tests (`config.unit.yaml`)

- ✓ Minimal setup
- ✓ No real Zotero/Obsidian
- ✓ Uses sample/synthetic data
- ✓ Fast execution (~2 min)
- ✗ Doesn't test real data sources

### Integration Tests (`config.integration.yaml`)

- ✓ Real Zotero library
- ✓ Real Obsidian vault
- ✓ Tests component interactions
- ✓ Medium execution (~15 min)
- ⚠️ Requires Zotero/Obsidian configured

### Pipeline Tests (`config.pipeline.yaml`)

- ✓ Full real-world setup
- ✓ All components enabled
- ✓ Tests complete workflows
- ✓ Validates search quality
- ✗ Slow execution (30+ min)

---

## Setting Up Configurations

### 1. Update Integration Config

Edit `tests/fixtures/configs/config.integration.yaml`:

```yaml
zotero:
  enabled: true
  data_directory: "~/Zotero" # ← Update path

obsidian:
  enabled: true
  vault_path: "~/Documents/Obsidian/MyVault" # ← Update path
```

### 2. Update Pipeline Config

Edit `tests/fixtures/configs/config.pipeline.yaml` (same paths as integration):

```yaml
zotero:
  enabled: true
  data_directory: "~/Zotero" # ← Same as above
  extract_attachments: true # Enable PDF processing
```

### 3. Verify Setup

```bash
# Check unit tests work (no setup needed)
pytest tests/unit/test_chunking.py -v

# Check integration config
pytest tests/integration/ -v --collect-only

# Check pipeline config
pytest tests/pipeline/ -v --collect-only
```

---

## Available Pytest Fixtures

### Configuration Fixtures

```python
def test_something(unit_config):
    """Minimal config for unit tests."""

def test_something(integration_config):
    """Config with real Zotero/Obsidian."""

def test_something(pipeline_config):
    """Full pipeline configuration."""
```

### Data Fixtures

```python
def test_something(sample_text):
    """Standard sample text."""

def test_something(sample_texts):
    """Multiple text samples."""

def test_something(unicode_text):
    """Text with unicode characters."""

def test_something(very_long_text):
    """Long text (>250KB)."""
```

### Component Fixtures

```python
def test_something(text_chunker):
    """Initialized TextChunker."""

def test_something(embedding_provider):
    """Initialized LM Studio embedder."""

def test_something(zotero_source):
    """Initialized ZoteroSource (if configured)."""

def test_something(obsidian_source):
    """Initialized ObsidianSource (if configured)."""
```

### Database Fixtures

```python
def test_something(chromadb_client):
    """ChromaDB HTTP client."""

def test_something(chromadb_collection):
    """Temporary ChromaDB collection (auto-cleanup)."""

def test_something(test_collection_name):
    """Unique collection name for this test."""
```

---

## Writing New Tests

### Example Unit Test

```python
"""
Unit tests for new feature.

Module: src/mymodule.py
Tests the MyClass class.
"""

import pytest
from src.mymodule import MyClass


@pytest.mark.unit
class TestMyClass:
    """Group related tests in classes."""

    def test_basic_functionality(self, unit_config):
        """Test basic functionality."""
        obj = MyClass(unit_config)
        result = obj.do_something()
        assert result is not None

    def test_with_real_data(self, sample_text):
        """Test with provided sample data."""
        obj = MyClass(unit_config)
        result = obj.process(sample_text)
        assert len(result) > 0
```

### Example Integration Test

```python
"""
Integration tests for source interaction.

Tests real Zotero data retrieval and processing.
"""

import pytest


@pytest.mark.integration
@pytest.mark.requires_zotero
class TestZoteroIntegration:
    """Test Zotero source with real library."""

    def test_fetch_real_items(self, zotero_source):
        """Fetch items from real Zotero library."""
        items = []
        for doc in zotero_source.fetch_documents():
            items.append(doc)
            if len(items) >= 5:
                break

        assert len(items) > 0
        assert all(hasattr(doc, 'doc_id') for doc in items)
```

### Example Pipeline Test

```python
"""
Pipeline tests for end-to-end workflow.

Tests complete workflow from source to search.
"""

import pytest


@pytest.mark.pipeline
@pytest.mark.slow
class TestEndToEndPipeline:
    """Test complete pipeline."""

    def test_full_workflow(self, pipeline_config):
        """
        Complete workflow: Fetch → Chunk → Embed → Store → Query

        Duration: ~20 minutes
        """
        # Initialize components
        from src.sources.zotero import ZoteroSource
        from src.processing.chunker import TextChunker
        from src.embedding.lmstudio import LMStudioEmbedding
        from src.storage.chroma import ChromaVectorStore

        zotero = ZoteroSource(pipeline_config)
        chunker = TextChunker(pipeline_config)
        embedder = LMStudioEmbedding(pipeline_config)
        store = ChromaVectorStore(pipeline_config)

        # ... rest of pipeline ...
```

---

## Test Markers

Use markers to control test execution:

### Run only unit tests

```bash
pytest -m unit
```

### Run integration tests

```bash
pytest -m integration
```

### Run pipeline tests

```bash
pytest -m pipeline
```

### Skip slow tests

```bash
pytest -m "not slow"
```

### Run tests requiring ChromaDB

```bash
pytest -m requires_chromadb
```

---

## Debugging Tests

### Show print output

```bash
pytest tests/unit/test_chunking.py -v -s
```

### Stop on first failure

```bash
pytest tests/unit/ -v -x
```

### Show local variables on failure

```bash
pytest tests/unit/ -v -l
```

### Run with detailed traceback

```bash
pytest tests/unit/ -v --tb=long
```

### Generate HTML report

```bash
pytest tests/unit/ --html=report.html --self-contained-html
```

---

## Test Coverage

Check code coverage:

```bash
# All tests
pytest --cov=src tests/

# Unit tests only
pytest --cov=src tests/unit/

# With HTML report
pytest --cov=src --cov-report=html tests/
# Open: htmlcov/index.html
```

Coverage targets:

- Text extraction: 85%
- Chunking: 90%
- Embedding: 75%
- Storage: 80%
- Overall: 75%

---

## CI/CD Integration

### Run tests locally before committing

```bash
# Quick check
pytest tests/unit/ -v

# Before PR
pytest tests/unit/ tests/integration/ -v
```

### GitHub Actions (proposed)

**Unit tests** - Every push (~2 min)

```bash
pytest tests/unit/ -v
```

**Integration tests** - Daily or on-demand (~15 min)

```bash
pytest tests/integration/ -v
```

**Pipeline tests** - Nightly or on-demand (30+ min)

```bash
pytest tests/pipeline/ -v -s
```

---

## Troubleshooting

### Tests skip with "not available" message

- **Unit tests**: Check LM Studio is running if embedding tests run
- **Integration tests**: Check Zotero/Obsidian configured in config.integration.yaml
- **Pipeline tests**: Check both Zotero and Obsidian configured

### ChromaDB connection errors

- Verify ChromaDB server running: `curl http://localhost:8000/api/v1`
- Check endpoint in config: `http://localhost:8000`

### LM Studio connection errors

- Verify LM Studio running: `curl http://localhost:1234/v1/models`
- Check endpoint in config: `http://localhost:1234/v1`
- Check API key is correct

### Tests fail on Unicode handling

- Ensure Python files are UTF-8 encoded
- Check terminal/IDE supports UTF-8

### Memory issues with long tests

- Close other applications
- Use smaller subsets: `-k "not slow"`
- Run in isolation: one test at a time

---

## ChromaDB Collection Management

### Collection Strategy

**Unit Tests** (reuse static collection)

```bash
pytest tests/unit/ -v
# Uses: "test_unit" collection (persistent across runs)
# Cleanup: Not needed - same test data each time
```

**Integration Tests** (auto-cleanup per session)

```bash
pytest tests/integration/ -v
# Uses: Unique timestamped collection (auto-created, auto-deleted)
# Cleanup: Automatic via pytest fixture
# Why: Real data source, want clean slate per run
```

**Pipeline Tests** (auto-cleanup per test)

```bash
pytest tests/pipeline/test_pipeline_fast.py -v
# Uses: Unique timestamped collection (auto-created, auto-deleted)
# Cleanup: Automatic after test completes
# Why: Full system test, want isolation
```

**Manual/Development Tests** (keep for inspection)

```python
# test_full_pipeline_with_sources.py generates:
# Collection: "test_pipeline_20260106_080225"
# Keep for: Manual inspection of results
# Cleanup: Manual or delete when no longer needed
```

### List Test Collections

```bash
# Using curl
curl http://localhost:8000/api/v1/collections | jq '.[].name'

# Using Python
python -c "
from src.storage.chroma import ChromaVectorStore
import yaml
with open('config.test.yaml') as f:
    config = yaml.safe_load(f)
store = ChromaVectorStore(config)
for c in store.client.list_collections():
    print(f'{c.name}: {c.count()} documents')
"
```

### Delete Old Collections

```bash
# Delete specific collection
curl -X DELETE http://localhost:8000/api/v1/collections/test_unit_old

# Or with Python
from src.storage.chroma import ChromaVectorStore
store = ChromaVectorStore(config)
store.client.delete_collection(name="test_unit_old")
```

### Cleanup Strategy

| Context           | Collection Lifecycle | Cleanup           |
| ----------------- | -------------------- | ----------------- |
| Unit tests        | Static/reused        | Never (same data) |
| Integration tests | Unique per session   | Auto (fixture)    |
| Pipeline tests    | Unique per test      | Auto (fixture)    |
| Manual tests      | Named for inspection | Manual when done  |
| Production        | Long-lived           | Never             |

---

## Best Practices

1. **Organize tests** by layer (unit, integration, pipeline)
2. **Use fixtures** for shared setup and data
3. **Mark tests** appropriately (unit, integration, slow, etc.)
4. **Test real conditions** in integration/pipeline tests
5. **Keep unit tests fast** (~2 minutes for all)
6. **Name tests clearly** - test name should describe what it tests
7. **One assertion per test** where possible
8. **Use parametrize** for testing multiple inputs
9. **Use unique collection names** for tests (timestamps avoid conflicts)
10. **Let fixtures handle cleanup** (automatic, no manual work)

---

## Example Test Run

```bash
$ pytest tests/unit/ -v

tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_returns_list PASSED
tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_preserves_content PASSED
tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_respects_max_size PASSED
...
tests/unit/test_chunking.py::TestTextChunkerConfiguration::test_missing_config_uses_defaults PASSED

========================= 24 passed in 1.23s =========================
```

---

## Support

- 📖 Full strategy: See `TESTING_STRATEGY.md`
- 🔧 Pytest docs: https://docs.pytest.org
- 🐛 Debug tests: Use `pytest -vv --tb=long -s`
- 📊 Coverage: `pytest --cov=src --cov-report=html`
