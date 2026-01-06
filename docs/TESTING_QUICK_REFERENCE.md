# Testing Quick Reference

## Run Tests

```bash
# Unit tests only (fast - 2 min)
pytest tests/unit/ -v

# Integration tests (15 min, requires Zotero/Obsidian)
pytest tests/integration/ -v

# Quick pipeline test (5-20 min)
pytest tests/pipeline/test_pipeline_fast.py -v

# Full pipeline (30+ min, with PDF extraction)
pytest tests/pipeline/test_pipeline_full.py -v

# Regression tests (5 min, quick validation)
pytest tests/pipeline/test_regression.py -v

# Specific test
pytest tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_returns_list -v

# With coverage
pytest tests/unit/ --cov=src --cov-report=html
```

---

## Configure Tests

### 1. Update Zotero Path

Edit: `tests/fixtures/configs/config.integration.yaml` and `config.pipeline.yaml`

```yaml
zotero:
  data_directory: "~/Zotero" # Your path here
```

### 2. Update Obsidian Path

```yaml
obsidian:
  vault_path: "~/Documents/Obsidian/MyVault" # Your path here
```

### 3. Verify Setup

```bash
pytest tests/unit/test_chunking.py -v  # Should work immediately
pytest tests/integration/ -v --collect-only  # Shows what will run
```

---

## Test Markers

```bash
pytest -m unit                          # Only unit tests
pytest -m integration                   # Only integration tests
pytest -m pipeline                      # Only pipeline tests
pytest -m "not slow"                    # Skip slow tests
pytest -m requires_chromadb             # Tests needing ChromaDB
pytest -m requires_lmstudio             # Tests needing LM Studio
```

---

## Common pytest Options

```bash
-v              # Verbose output
-s              # Show print() output
-x              # Stop on first failure
-k "pattern"    # Run tests matching pattern
-l              # Show local variables on failure
--tb=short      # Shorter traceback format
--tb=long       # Longer traceback format
--tb=no         # No traceback
--lf            # Run last failed tests
--ff            # Run failed tests first
-n 4            # Parallel execution (4 workers)
```

---

## Writing Tests

### Minimal Unit Test

```python
import pytest
from src.mymodule import MyClass

@pytest.mark.unit
class TestMyClass:
    def test_basic(self, unit_config):
        obj = MyClass(unit_config)
        result = obj.do_something()
        assert result is not None
```

### With Sample Data

```python
def test_with_data(self, sample_text, text_chunker):
    chunks = text_chunker.chunk_text(sample_text)
    assert len(chunks) > 0
```

### With Real Zotero Data

```python
@pytest.mark.integration
def test_zotero(self, zotero_source):
    docs = []
    for doc in zotero_source.fetch_documents():
        docs.append(doc)
        if len(docs) >= 5:
            break
    assert len(docs) > 0
```

---

## Available Fixtures

### Configs

- `unit_config` - Minimal config
- `integration_config` - Real Zotero/Obsidian
- `pipeline_config` - Full setup
- `test_config` - Auto-selects based on marker

### Data

- `sample_text` - Standard text
- `sample_texts` - Multiple texts
- `unicode_text` - Unicode content
- `very_long_text` - 250KB+ text
- `empty_text` - Empty string

### Components

- `text_chunker` - TextChunker instance
- `embedding_provider` - LM Studio embedder
- `zotero_source` - ZoteroSource (if configured)
- `obsidian_source` - ObsidianSource (if configured)

### Database

- `chromadb_client` - ChromaDB HTTP client
- `chromadb_collection` - Test collection (auto-cleanup)
- `test_collection_name` - Unique collection name

---

## Test Structure

```
tests/
├── unit/                    # 👈 Start here (fast)
│   ├── test_chunking.py
│   ├── test_embedding.py
│   ├── test_extraction.py
│   ├── test_storage.py
│   └── test_sources.py
│
├── integration/             # Real data tests
│   ├── test_source_integration.py
│   ├── test_processing_chain.py
│   └── test_data_flow.py
│
├── pipeline/                # End-to-end tests
│   ├── test_pipeline_fast.py
│   ├── test_pipeline_full.py
│   ├── test_regression.py
│   └── test_query_semantics.py
│
├── fixtures/
│   ├── sample_files/        # Sample PDFs, DOCX, HTML
│   └── configs/
│       ├── config.unit.yaml
│       ├── config.integration.yaml
│       └── config.pipeline.yaml
│
├── conftest.py             # Shared fixtures
└── fixtures.py             # Existing fixtures
```

---

## Typical Workflow

### During Development

```bash
# 1. Run unit tests (2 min)
pytest tests/unit/ -v

# 2. Fix broken test
vim src/my_component.py

# 3. Run that component's tests
pytest tests/unit/test_my_component.py -v

# 4. Repeat until passing
```

### Before Committing

```bash
# 1. Run unit tests
pytest tests/unit/ -v

# 2. Run regression tests
pytest tests/pipeline/test_regression.py -v

# 3. Total: ~7 minutes
```

### Before Release

```bash
# 1. Run everything
pytest tests/ -v

# Option A: Quick validation (30 min)
pytest tests/unit/ tests/integration/ tests/pipeline/test_pipeline_fast.py

# Option B: Complete validation (1+ hour)
pytest tests/pipeline/test_pipeline_full.py
```

---

## Debugging Tests

### See print output

```bash
pytest tests/unit/test_chunking.py -v -s
```

### Show variables on failure

```bash
pytest tests/unit/ -v -l
```

### Stop on first failure

```bash
pytest tests/unit/ -v -x
```

### Run specific test in debugger

```bash
pytest tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_returns_list -v --pdb
```

### Generate HTML report

```bash
pytest tests/ --html=report.html --self-contained-html
```

---

## Troubleshooting

### "Test skipped: ... not available"

- Unit tests: Check LM Studio running
- Integration tests: Check config.integration.yaml has correct paths
- Pipeline tests: Check config.pipeline.yaml has correct paths

### ChromaDB connection error

```bash
# Verify server running
curl http://localhost:8000/api/v1

# Check config has right endpoint
http://localhost:8000
```

### LM Studio connection error

```bash
# Verify server running
curl http://localhost:1234/v1/models

# Check config has right endpoint and API key
http://localhost:1234/v1
```

### Tests hang or timeout

```bash
# Run with timeout
pytest tests/ --timeout=300 -v
```

---

## Coverage Report

```bash
# Generate coverage
pytest tests/ --cov=src --cov-report=html

# View report
open htmlcov/index.html

# Set minimum coverage threshold
pytest tests/ --cov=src --cov-fail-under=75
```

---

## Key Files

- 📖 Full strategy: `TESTING_STRATEGY.md`
- 📚 How-to guide: `TESTING_GUIDE.md`
- 📋 This summary: `TESTING_QUICK_REFERENCE.md`
- ⚙️ Pytest config: `pytest.ini`
- 🔧 Shared fixtures: `tests/conftest.py`
- 📁 Configs: `tests/fixtures/configs/`

---

## Performance Tips

- Use `-n 4` for parallel execution (~4x faster)
- Use `-m "not slow"` to skip long tests
- Run only affected tests with `-k pattern`
- Run fast pipeline instead of full pipeline (20 min vs 1+ hour)

---

## Test Organization Recap

| Layer             | Tests | Duration  | Real Data       | Use For        |
| ----------------- | ----- | --------- | --------------- | -------------- |
| **Unit**          | 50+   | ~2 min    | Synthetic       | Development    |
| **Integration**   | 15+   | ~15 min   | Real Z/O        | Validation     |
| **Fast Pipeline** | 1+    | ~5-20 min | Real Z/O        | Quick checks   |
| **Full Pipeline** | 1+    | ~30+ min  | Real Z/O + PDFs | Before release |

---

## Remember

1. ✅ Unit tests for components
2. ✅ Integration tests for real sources
3. ✅ Pipeline tests for workflows
4. ✅ Real data validation
5. ✅ Fast feedback loop
6. ✅ Clear error messages
7. ✅ Easy to debug

Start with unit tests, move up pyramid as needed!
