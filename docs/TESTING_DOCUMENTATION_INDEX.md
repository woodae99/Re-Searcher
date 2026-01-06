# Re-Searcher Testing Documentation Index

Welcome! This document guides you through the comprehensive testing strategy for Re-Searcher.

## 📚 Documentation Structure

### For Busy Developers

Start here if you just want to run tests:

- **[TESTING_QUICK_REFERENCE.md](TESTING_QUICK_REFERENCE.md)** ⚡
  - Common commands and examples
  - Configuration in 2 minutes
  - Quick troubleshooting

### For Understanding the Strategy

Want to understand the full testing approach:

- **[TESTING_REGIME_SUMMARY.md](TESTING_REGIME_SUMMARY.md)** 📊
  - Overview of all three test layers
  - Why each layer matters
  - Benefits and next steps

### For Implementation Details

Need complete documentation:

- **[TESTING_STRATEGY.md](TESTING_STRATEGY.md)** 📖
  - Detailed testing hierarchy
  - What gets tested at each layer
  - Test organization structure
  - Coverage goals and metrics
  - CI/CD integration details

### For Practical How-To

Learning how to write and run tests:

- **[TESTING_GUIDE.md](TESTING_GUIDE.md)** 🛠️
  - How to run tests
  - How to write new tests
  - Fixture reference
  - Debugging tips
  - Best practices

---

## 🚀 Quick Start (5 minutes)

### 1. Run Unit Tests (No Setup Required)

```bash
pytest tests/unit/ -v
```

Should complete in ~2 minutes with all tests passing.

### 2. Configure Real Data (2 minutes)

Edit `tests/fixtures/configs/config.integration.yaml`:

```yaml
zotero:
  data_directory: "~/Zotero" # ← Update to your path

obsidian:
  vault_path: "~/Documents/Obsidian/MyVault" # ← Update to your path
```

### 3. Run Integration Tests (With Real Data)

```bash
pytest tests/integration/ -v
```

Should complete in ~15 minutes using actual Zotero items and Obsidian vault.

### 4. Run Quick Pipeline Test

```bash
pytest tests/pipeline/test_pipeline_fast.py -v
```

Complete end-to-end test (notes only) in ~5-20 minutes.

---

## 📋 The Three Testing Layers

### Layer 1: Unit Tests (Foundation)

- **Duration**: ~2 minutes
- **What**: Individual components in isolation
- **Why**: Fast feedback, easy debugging
- **Location**: `tests/unit/`
- **Examples**:
  - Text chunking (24 tests)
  - Embedding generation
  - PDF text extraction
  - Data source initialization
- **Run**: `pytest tests/unit/ -v`

### Layer 2: Integration Tests (Validation)

- **Duration**: ~15 minutes
- **What**: Components working together with real data
- **Why**: Ensure real Zotero/Obsidian data works
- **Location**: `tests/integration/`
- **Examples**:
  - Fetch items from real Zotero library
  - Extract text from real PDFs
  - Read real Obsidian vault
  - Component chains (extract → chunk → embed)
- **Run**: `pytest tests/integration/ -v`
- **Requires**: Zotero configured, Obsidian vault available

### Layer 3: Pipeline Tests (Complete Validation)

- **Duration**: 5 minutes (fast) to 30+ minutes (full)
- **What**: End-to-end workflows
- **Why**: Validate complete system functionality
- **Location**: `tests/pipeline/`
- **Three variants**:
  - Fast: Notes only (5-20 min)
  - Full: With PDF extraction (30+ min)
  - Regression: Quick checks (5 min)
- **Run**: `pytest tests/pipeline/ -v`

---

## 🗂️ File Structure

```
Re-Searcher/
├── TESTING_STRATEGY.md              📖 Comprehensive strategy (10 pages)
├── TESTING_REGIME_SUMMARY.md        📊 Executive summary
├── TESTING_GUIDE.md                 🛠️ Practical how-to guide
├── TESTING_QUICK_REFERENCE.md       ⚡ Quick command reference
├── TESTING_DOCUMENTATION_INDEX.md   📚 This file
│
├── pytest.ini                        ⚙️ Pytest configuration
│
├── tests/
│   ├── conftest.py                 🔧 Shared pytest fixtures
│   │                                  - Configurations (unit, integration, pipeline)
│   │                                  - Test data fixtures
│   │                                  - Component fixtures
│   │                                  - ChromaDB fixtures
│   │
│   ├── unit/                       ✅ Unit tests (fast)
│   │   ├── test_chunking.py        • 24 chunking tests
│   │   ├── test_embedding.py       • Embedding generation
│   │   ├── test_extraction.py      • PDF/document extraction
│   │   ├── test_storage.py         • ChromaDB operations
│   │   └── test_sources.py         • Source initialization
│   │
│   ├── integration/                ✅ Integration tests (real data)
│   │   ├── test_source_integration.py   • Real Zotero/Obsidian
│   │   ├── test_processing_chain.py     • Component chains
│   │   └── test_data_flow.py            • Data integrity
│   │
│   ├── pipeline/                   ✅ Pipeline tests (end-to-end)
│   │   ├── test_pipeline_fast.py        • Notes-only pipeline
│   │   ├── test_pipeline_full.py        • With PDF extraction
│   │   ├── test_regression.py           • Regression tests
│   │   └── test_query_semantics.py      • Search quality
│   │
│   └── fixtures/
│       ├── sample_files/           📁 Test documents
│       │   ├── sample.pdf          • Sample PDF
│       │   ├── sample.docx         • Sample Word doc
│       │   └── sample.html         • Sample HTML
│       │
│       └── configs/                ⚙️ Test configurations
│           ├── config.unit.yaml           • Unit test setup
│           ├── config.integration.yaml    • Integration setup
│           └── config.pipeline.yaml       • Full setup
```

---

## 🎯 When to Use Each Test Layer

### During Active Development

```bash
# Run unit tests frequently (2 min)
pytest tests/unit/ -v

# Run only the component you're working on
pytest tests/unit/test_my_component.py -v
```

### Before Committing Code

```bash
# Run unit tests + regression tests
pytest tests/unit/ tests/pipeline/test_regression.py -v

# Takes ~7 minutes
```

### Before Submitting PR

```bash
# Run unit + integration + fast pipeline
pytest tests/unit/ tests/integration/ tests/pipeline/test_pipeline_fast.py -v

# Takes ~30 minutes
```

### Before Release

```bash
# Full comprehensive testing
pytest tests/pipeline/test_pipeline_full.py -v

# Takes 1+ hour but validates everything
```

---

## 🔧 Configuration Setup

All test configurations are in `tests/fixtures/configs/`:

### config.unit.yaml

- ✅ No real Zotero/Obsidian needed
- ✅ Uses synthetic test data
- ✅ Can run immediately
- ✗ Doesn't test real sources

### config.integration.yaml

- ⚠️ Requires Zotero + Obsidian configured
- ✅ Uses your real library/vault
- ✅ Tests with actual data
- 🔧 **YOU NEED TO UPDATE THIS** - see Quick Start

### config.pipeline.yaml

- ⚠️ Same as integration + PDF extraction
- ✅ Complete end-to-end testing
- 🔧 **YOU NEED TO UPDATE THIS** - see Quick Start

---

## 📊 Test Coverage Summary

```
Unit Tests (tests/unit/)
├── Chunking (24 tests)
│   ├── Basic functionality
│   ├── Overlap handling
│   ├── Multiple strategies
│   └── Edge cases (unicode, long text, etc.)
├── Embedding (15+ tests)
│   ├── Single & batch embedding
│   ├── Dimension validation
│   └── Special character handling
├── Extraction (15+ tests)
│   ├── PDF, DOCX, HTML
│   └── Error handling
├── Storage (10+ tests)
│   ├── CRUD operations
│   ├── Metadata preservation
│   └── Filtering
└── Sources (10+ tests)
    ├── Initialization
    └── Configuration validation

Integration Tests (tests/integration/)
├── Source Integration (5+ tests)
│   ├── Zotero item fetching
│   ├── PDF extraction
│   ├── Obsidian reading
│   └── Metadata preservation
├── Processing Chains (5+ tests)
│   ├── Extract → Chunk
│   ├── Chunk → Embed
│   └── Embed → Store → Query
└── Data Flow (3+ tests)
    └── Integrity throughout pipeline

Pipeline Tests (tests/pipeline/)
├── Fast Pipeline (1+ test)
│   ├── Real Zotero notes
│   ├── Real Obsidian notes
│   ├── Complete chunking & embedding
│   └── Semantic search validation
├── Full Pipeline (1+ test)
│   ├── Real Zotero with PDFs
│   ├── PDF text extraction
│   ├── Real Obsidian notes
│   └── Complete workflow
├── Regression (5+ tests)
│   ├── Core functionality checks
│   ├── Search quality validation
│   └── Data integrity checks
└── Query Semantics (3+ tests)
    └── Search quality metrics
```

---

## ⚡ Common Commands

```bash
# Unit tests (instant)
pytest tests/unit/ -v

# Integration tests (15 min)
pytest tests/integration/ -v

# Quick pipeline (5-20 min)
pytest tests/pipeline/test_pipeline_fast.py -v

# Full pipeline (30+ min)
pytest tests/pipeline/test_pipeline_full.py -v

# Regression only (5 min)
pytest tests/pipeline/test_regression.py -v

# Specific test
pytest tests/unit/test_chunking.py::TestTextChunkerBasic -v

# With coverage
pytest tests/unit/ --cov=src --cov-report=html

# Only failed tests
pytest tests/ --lf -v

# Parallel execution (4 workers)
pytest tests/ -n 4 -v

# Stop on first failure
pytest tests/ -x -v

# Show print output
pytest tests/ -v -s
```

---

## 🐛 Troubleshooting

### Tests Skip with "not available" Message

**For unit tests**:

- Usually means LM Studio not running
- Check: `curl http://localhost:1234/v1/models`

**For integration tests**:

- Zotero not configured or not found
- Obsidian vault path wrong
- Check: `tests/fixtures/configs/config.integration.yaml`

**For pipeline tests**:

- Check both config files
- Verify Zotero database exists
- Verify Obsidian vault exists

### ChromaDB Connection Errors

```bash
# Check if running
curl http://localhost:8000/api/v1

# Verify config endpoint
tests/fixtures/configs/config.*.yaml
```

### LM Studio Connection Errors

```bash
# Check if running
curl http://localhost:1234/v1/models

# Verify config endpoint and API key
tests/fixtures/configs/config.*.yaml
```

---

## 📖 Next Steps

### 1. First Time Setup (5 minutes)

1. Read: **TESTING_QUICK_REFERENCE.md**
2. Run: `pytest tests/unit/ -v`
3. Update: Config files with your Zotero/Obsidian paths
4. Run: `pytest tests/integration/ -v`

### 2. Understanding the Strategy (15 minutes)

1. Read: **TESTING_REGIME_SUMMARY.md**
2. Review: Test organization in `tests/` folder
3. Check: Which layer tests what

### 3. Writing New Tests (20 minutes)

1. Read: **TESTING_GUIDE.md** (section "Writing New Tests")
2. Look at: Example tests in `tests/unit/test_chunking.py`
3. Create: Your first unit test
4. Run: `pytest tests/unit/test_my_new_test.py -v`

### 4. Full Deep Dive (45 minutes)

1. Read: **TESTING_STRATEGY.md**
2. Review: All test files in `tests/`
3. Understand: Coverage goals and CI/CD integration
4. Plan: What tests to add for your features

---

## ✨ Key Features of This Testing Regime

✅ **Real Data Testing**

- Tests use actual Zotero items, PDFs, and Obsidian vault
- Validates system works with real-world data

✅ **Fast Feedback Loop**

- Unit tests in 2 minutes for active development
- Regression tests in 5 minutes before commits
- Full validation available but not required for every change

✅ **Clear Organization**

- Three test layers with clear purposes
- Each layer tests something different
- Easy to find and fix failing tests

✅ **Easy Setup**

- Just update 2 config file paths
- Run tests immediately
- Auto-skip unsupported tests with clear messages

✅ **Comprehensive Coverage**

- Unit tests for all components
- Integration tests for real sources
- Pipeline tests for complete workflows

✅ **Maintainability**

- Shared pytest fixtures
- Clear test names
- Well-organized folder structure
- Extensive documentation

---

## 📚 Documentation Map

```
Need quick answer?
├─ How do I run tests?
│  └─ See: TESTING_QUICK_REFERENCE.md
├─ How do I configure?
│  └─ See: TESTING_QUICK_REFERENCE.md + TESTING_GUIDE.md
├─ How do I write tests?
│  └─ See: TESTING_GUIDE.md (Writing New Tests section)
├─ What should I test?
│  └─ See: TESTING_STRATEGY.md + TESTING_REGIME_SUMMARY.md
├─ Why this testing structure?
│  └─ See: TESTING_REGIME_SUMMARY.md
└─ Everything explained in detail?
   └─ See: TESTING_STRATEGY.md
```

---

## 🎓 Learning Path

**If you have 5 minutes**: TESTING_QUICK_REFERENCE.md  
**If you have 15 minutes**: TESTING_REGIME_SUMMARY.md  
**If you have 30 minutes**: TESTING_GUIDE.md  
**If you have 1 hour**: TESTING_STRATEGY.md + all the above

---

## 💡 Pro Tips

1. **Development**: Run only unit tests (`pytest tests/unit/`) during active coding
2. **Before commit**: Run regression tests (`pytest tests/pipeline/test_regression.py`)
3. **Before PR**: Run everything you can in ~30 min (`pytest tests/unit/ tests/integration/ tests/pipeline/test_pipeline_fast.py`)
4. **Nightly**: Let full pipeline tests run automatically
5. **Debugging**: Use `-v -s` flags to see print output
6. **Coverage**: Check with `pytest --cov=src`

---

## 📞 Support

- **Configuration issues**: See TESTING_GUIDE.md "Troubleshooting"
- **How to write tests**: See TESTING_GUIDE.md "Writing New Tests"
- **Design questions**: See TESTING_STRATEGY.md or TESTING_REGIME_SUMMARY.md
- **Pytest help**: https://docs.pytest.org

---

## 🚀 Ready to Start?

1. **First time?** → Start with [TESTING_QUICK_REFERENCE.md](TESTING_QUICK_REFERENCE.md)
2. **Want to understand?** → Read [TESTING_REGIME_SUMMARY.md](TESTING_REGIME_SUMMARY.md)
3. **Ready to write tests?** → Follow [TESTING_GUIDE.md](TESTING_GUIDE.md)
4. **Need all details?** → Review [TESTING_STRATEGY.md](TESTING_STRATEGY.md)

**Now go run: `pytest tests/unit/ -v`** ✨
