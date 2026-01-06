# Testing Strategy Summary for Re-Searcher

## Proposed Comprehensive Testing Regime

I've designed a **three-layer testing pyramid** that will help you maintain code quality while testing under real-world conditions with actual Zotero items, PDFs, and Obsidian vault data.

---

## 🔺 The Testing Pyramid

```
        ┌─────────────────────┐
        │  Pipeline Tests     │  ← End-to-end workflows
        │  (30+ minutes)      │    Full real data processing
        ├─────────────────────┤
        │ Integration Tests   │  ← Component chains
        │ (15 minutes)        │    Real Zotero & Obsidian data
        ├─────────────────────┤
        │   Unit Tests        │  ← Individual components
        │  (~2 minutes)       │    Fast, isolated, no external deps
        └─────────────────────┘
```

---

## Layer 1: Unit Tests (Foundation)

**Purpose**: Fast feedback on individual components  
**Duration**: ~2 minutes  
**Scope**: Isolated function/class testing

### What Gets Tested

- ✅ Text extraction (PDFs, DOCX, HTML)
- ✅ Text chunking (size, overlap, strategies)
- ✅ Embedding generation (single & batch)
- ✅ Vector storage operations (CRUD)
- ✅ Source initialization & config validation

### Example Tests

```bash
pytest tests/unit/test_chunking.py -v
pytest tests/unit/test_embedding.py -v
pytest tests/unit/test_extraction.py -v
```

**Configuration**: `config.unit.yaml` - No real Zotero/Obsidian needed

---

## Layer 2: Integration Tests (Validation)

**Purpose**: Test component interactions with real data  
**Duration**: ~15 minutes  
**Scope**: Real Zotero library, real Obsidian vault

### What Gets Tested

- ✅ Actual Zotero item retrieval
- ✅ PDF attachment extraction from real PDFs
- ✅ Zotero notes processing
- ✅ Actual Obsidian markdown files
- ✅ Component chains (chunking → embedding, embedding → storage)
- ✅ Data integrity throughout pipeline

### Example Tests

```bash
pytest tests/integration/test_source_integration.py -v
pytest tests/integration/test_processing_chain.py -v
```

**Configuration**: `config.integration.yaml`

- Uses your real Zotero library (configurable subset)
- Uses your real Obsidian vault (configurable folders)
- Test ChromaDB collection (auto-cleanup)

---

## Layer 3: Pipeline Tests (Complete Validation)

**Purpose**: End-to-end workflow validation with real conditions  
**Duration**: 30+ minutes (full) or ~5-20 min (fast variants)

### Three Variants

#### 3a. Fast Pipeline (Notes-only)

- **Duration**: 5-20 minutes (depends on library size)
- **What**: Real Zotero notes + Real Obsidian notes (no PDF extraction)
- **Use**: Daily/weekly regression testing

```bash
pytest tests/pipeline/test_pipeline_fast.py -v
```

#### 3b. Full Pipeline (With PDFs)

- **Duration**: 30+ minutes
- **What**: Real Zotero with PDF extraction + Real Obsidian
- **Use**: Before major releases, after significant changes

```bash
pytest tests/pipeline/test_pipeline_full.py -v
```

#### 3c. Regression Suite

- **Duration**: 5 minutes
- **What**: Quick validation of core functionality
- **Use**: Every commit/push

```bash
pytest tests/pipeline/test_regression.py -v
```

---

## Test Organization Structure

```
tests/
├── conftest.py                          # Shared fixtures (configs, data, components)
├── fixtures.py                          # Existing fixtures
├── fixtures/
│   ├── sample_files/
│   │   ├── sample.pdf, sample.docx, sample.html
│   └── configs/
│       ├── config.unit.yaml             # Unit test setup
│       ├── config.integration.yaml      # Integration setup
│       └── config.pipeline.yaml         # Full pipeline setup
│
├── unit/                                # Fast component tests
│   ├── test_chunking.py                # 24 chunking tests
│   ├── test_embedding.py               # Embedding tests
│   ├── test_extraction.py              # Text extraction tests
│   ├── test_storage.py                 # ChromaDB tests
│   └── test_sources.py                 # Source initialization tests
│
├── integration/                         # Real data tests
│   ├── test_source_integration.py      # Real Zotero/Obsidian
│   ├── test_processing_chain.py        # Component chains
│   └── test_data_flow.py               # Data integrity
│
└── pipeline/                            # End-to-end tests
    ├── test_pipeline_fast.py           # Notes only
    ├── test_pipeline_full.py           # With PDFs
    ├── test_regression.py              # Quick validation
    └── test_query_semantics.py         # Search quality
```

---

## Running Tests

### Quick Development Cycle

```bash
# Run unit tests (instant feedback)
pytest tests/unit/ -v                   # ~2 min

# Before committing
pytest tests/unit/ tests/pipeline/test_regression.py -v  # ~7 min
```

### Full Validation (Before Release)

```bash
# Unit + Integration + Fast Pipeline
pytest tests/unit/ tests/integration/ tests/pipeline/test_pipeline_fast.py -v  # ~30 min

# Complete validation
pytest tests/pipeline/test_pipeline_full.py -v  # ~1+ hour
```

### Specific Test

```bash
pytest tests/unit/test_chunking.py::TestTextChunkerBasic::test_chunk_text_returns_list -v
```

---

## Key Features

### ✅ Real Data Testing

- ✓ Actual Zotero items with real metadata
- ✓ Real PDF attachments (extracted and processed)
- ✓ Actual Obsidian vault with real markdown
- ✓ Real embeddings from LM Studio
- ✓ Real semantic search results

### ✅ Fault Isolation

- Unit tests fail → Check specific component
- Integration tests fail → Check component interaction
- Pipeline tests fail → Check end-to-end workflow

### ✅ Fast Feedback Loop

- Unit tests: ~2 min (for active development)
- Quick pipeline: ~5-20 min (before commits)
- Full pipeline: ~1+ hour (nightly/pre-release)

### ✅ Easy Configuration

- Three pre-configured YAML files
- Simple path updates for your Zotero/Obsidian
- Automatic test collection management
- Clear skip messages if sources not configured

### ✅ Maintainability

- Organized by test type and layer
- Shared pytest fixtures for common setup
- Clear test names describing what's tested
- Parametrized tests for multiple scenarios

---

## Setup Instructions

### 1. **Update Configuration Files**

**`tests/fixtures/configs/config.integration.yaml`**:

```yaml
zotero:
  data_directory: "~/Zotero" # ← Your Zotero path
obsidian:
  vault_path: "~/Documents/Obsidian/MyVault" # ← Your vault path
```

**`tests/fixtures/configs/config.pipeline.yaml`**: Same paths + enable PDFs

### 2. **Install Test Dependencies**

```bash
pip install pytest pytest-cov pytest-html
```

### 3. **Run Initial Test**

```bash
pytest tests/unit/test_chunking.py -v  # Should pass without any config
```

### 4. **Run Integration Tests** (if Zotero/Obsidian configured)

```bash
pytest tests/integration/ -v
```

---

## Coverage Goals

| Layer                 | Target | Priority    |
| --------------------- | ------ | ----------- |
| Unit - Extraction     | 85%    | 🔴 Critical |
| Unit - Chunking       | 90%    | 🔴 Critical |
| Unit - Embedding      | 75%    | 🟡 High     |
| Unit - Storage        | 80%    | 🔴 Critical |
| Integration - Sources | 70%    | 🟡 High     |
| Integration - Chains  | 60%    | 🟢 Medium   |
| Pipeline - Fast       | 100%   | 🔴 Critical |
| Pipeline - Full       | 100%   | 🟡 High     |

---

## CI/CD Integration (Proposed)

### GitHub Actions Workflows

**`tests-unit.yml`**: Every push (2 min)

- ✓ Unit tests only
- ✓ Block merge on failure

**`tests-integration.yml`**: Daily or on-demand (15 min)

- ✓ Integration tests
- ✓ Report status (don't block)

**`tests-pipeline.yml`**: Manual or nightly (1+ hour)

- ✓ Full pipeline test
- ✓ Report results

---

## Documentation Files Created

1. **`TESTING_STRATEGY.md`** - Comprehensive testing strategy (10 pages)

   - Detailed breakdown of all test layers
   - Test organization structure
   - Coverage goals and metrics

2. **`TESTING_GUIDE.md`** - Practical testing guide (8 pages)

   - How to run tests
   - How to write new tests
   - Troubleshooting
   - Best practices

3. **`pytest.ini`** - Pytest configuration

   - Test discovery settings
   - Markers definition
   - Coverage configuration

4. **`tests/conftest.py`** - Shared pytest fixtures

   - Configuration fixtures (unit, integration, pipeline)
   - Data fixtures (sample texts, unicode, etc.)
   - Component fixtures (chunker, embedder, sources)
   - ChromaDB fixtures with auto-cleanup
   - Automatic test markers

5. **Configuration files**:

   - `config.unit.yaml` - No real sources
   - `config.integration.yaml` - Real Zotero/Obsidian
   - `config.pipeline.yaml` - Full setup with PDFs

6. **Unit test example**: `test_chunking.py` (24 comprehensive tests)

---

## Benefits

### For Development

- 🚀 **Fast feedback**: Unit tests in 2 minutes
- 🔍 **Easy debugging**: Isolated component failures
- 🎯 **Focused testing**: Test only what changed

### For Quality

- ✅ **Real data validation**: Tests with actual Zotero/Obsidian
- 📊 **Search quality**: Validates semantic search works
- 🛡️ **Regression prevention**: Automated regression suite

### For Maintenance

- 📚 **Clear organization**: Tests grouped by layer
- 🔧 **Easy setup**: Pre-configured YAML files
- 📖 **Good documentation**: Strategy + practical guide

---

## Next Steps

1. **Review** `TESTING_STRATEGY.md` for full details
2. **Update** config files with your Zotero/Obsidian paths
3. **Run** unit tests: `pytest tests/unit/ -v`
4. **Run** integration tests (if configured): `pytest tests/integration/ -v`
5. **Create** more unit tests for your components
6. **Add** integration tests for real data workflows
7. **Set up** CI/CD workflows (GitHub Actions template provided)

---

## Questions?

- For comprehensive strategy: See `TESTING_STRATEGY.md`
- For practical how-to: See `TESTING_GUIDE.md`
- For pytest docs: https://docs.pytest.org

This testing regime will help you:

- ✅ Debug issues quickly (unit tests pinpoint problems)
- ✅ Validate changes safely (regression tests prevent breakage)
- ✅ Test real conditions (integration/pipeline tests with actual data)
- ✅ Maintain code quality (organized, documented tests)
