"""
Pytest configuration and shared fixtures for Re-Searcher tests.

This module provides:
- Shared test configurations (unit, integration, pipeline)
- Pytest fixtures for common test needs
- Temporary resource management
- ChromaDB test collection management
"""

import pytest
import yaml
from pathlib import Path
from datetime import datetime
import tempfile
import sys

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def project_root_dir():
    """Get project root directory."""
    return project_root


@pytest.fixture(scope="session")
def fixtures_dir():
    """Get test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def unit_config():
    """Minimal config for unit tests (no real Zotero/Obsidian)."""
    config_path = Path(__file__).parent / "fixtures" / "configs" / "config.unit.yaml"
    if not config_path.exists():
        pytest.skip(f"Unit test config not found: {config_path}")
    
    with open(config_path) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def integration_config():
    """Config with real Zotero/Obsidian but test ChromaDB."""
    config_path = Path(__file__).parent / "fixtures" / "configs" / "config.integration.yaml"
    if not config_path.exists():
        pytest.skip(f"Integration config not found: {config_path}")
    
    with open(config_path) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def pipeline_config():
    """Full pipeline config with all real components."""
    config_path = Path(__file__).parent / "fixtures" / "configs" / "config.pipeline.yaml"
    if not config_path.exists():
        pytest.skip(f"Pipeline config not found: {config_path}")
    
    with open(config_path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def test_config(request):
    """
    Provide appropriate config based on test marker.
    
    Usage:
        @pytest.mark.unit
        def test_something(test_config):
            # test_config will be unit_config
    """
    markers = [marker.name for marker in request.node.iter_markers()]
    
    if "integration" in markers:
        return integration_config()
    elif "pipeline" in markers:
        return pipeline_config()
    else:
        return unit_config()


# ============================================================================
# Temporary Resources
# ============================================================================

@pytest.fixture(scope="session")
def temp_test_dir():
    """Create temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_test_file(temp_test_dir):
    """Create a temporary file for testing."""
    test_file = temp_test_dir / f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    test_file.write_text("Test content")
    return test_file


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_text():
    """Provide sample text for testing."""
    return """
    This is a sample document about research methodology.
    
    The first section discusses qualitative methods including interviews,
    focus groups, and ethnographic observation. These methods are particularly
    useful for understanding complex social phenomena.
    
    The second section covers quantitative approaches such as surveys,
    experiments, and statistical analysis. These provide numerical data
    that can be analyzed for patterns and correlations.
    
    Finally, mixed methods combine both approaches to provide a more
    comprehensive understanding of research questions.
    """ * 5  # Repeat to make it long enough for chunking tests


@pytest.fixture
def sample_texts():
    """Provide multiple sample texts."""
    return [
        "Machine learning is a subset of artificial intelligence.",
        "Natural language processing enables computers to understand text.",
        "Deep learning uses neural networks with multiple layers.",
        "Embeddings represent words as vectors in high-dimensional space.",
        "Vector databases enable efficient similarity search.",
    ]


@pytest.fixture
def unicode_text():
    """Text with unicode characters for encoding tests."""
    return """
    Testing unicode characters: 你好世界 (Chinese), مرحبا بالعالم (Arabic),
    Привет мир (Russian), और हेल्लो वर्ल्ड (Hindi).
    
    Special characters: é, ü, ñ, ø, æ, ß
    Emoji: 🚀 📊 💡 🔬 📚
    """


@pytest.fixture
def empty_text():
    """Empty or whitespace-only text."""
    return ""


@pytest.fixture
def very_long_text():
    """Very long text for stress testing."""
    base = "This is a test sentence. " * 100
    return base * 50  # ~250KB of text


# ============================================================================
# ChromaDB Fixtures
# ============================================================================

@pytest.fixture
def test_collection_name():
    """Generate unique collection name for each test."""
    return f"test_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


@pytest.fixture
def chromadb_client(integration_config):
    """Provide ChromaDB HTTP client."""
    try:
        from src.storage.chroma import ChromaVectorStore
        store = ChromaVectorStore(integration_config)
        return store.client
    except Exception as e:
        pytest.skip(f"ChromaDB not available: {e}")


@pytest.fixture
def chromadb_collection(chromadb_client, test_collection_name):
    """Create a test ChromaDB collection and clean up afterward."""
    collection = chromadb_client.get_or_create_collection(
        name=test_collection_name,
        metadata={"hnsw:space": "cosine"}
    )
    yield collection
    
    # Cleanup
    try:
        chromadb_client.delete_collection(name=test_collection_name)
    except Exception:
        pass  # Collection may already be deleted


# ============================================================================
# Component Fixtures
# ============================================================================

@pytest.fixture
def text_chunker(unit_config):
    """Provide initialized TextChunker."""
    try:
        from src.processing.chunker import TextChunker
        return TextChunker(unit_config)
    except Exception as e:
        pytest.skip(f"TextChunker not available: {e}")


@pytest.fixture
def embedding_provider(integration_config):
    """Provide initialized LM Studio embedding provider."""
    try:
        from src.embedding.lmstudio import LMStudioEmbedding
        return LMStudioEmbedding(integration_config)
    except Exception as e:
        pytest.skip(f"LM Studio embedding provider not available: {e}")


@pytest.fixture
def zotero_source(integration_config):
    """Provide initialized ZoteroSource."""
    try:
        from src.sources.zotero import ZoteroSource
        source = ZoteroSource(integration_config)
        if not source.is_enabled():
            pytest.skip("Zotero not enabled in config")
        if not source.validate_config():
            pytest.skip("Zotero validation failed")
        return source
    except Exception as e:
        pytest.skip(f"ZoteroSource not available: {e}")


@pytest.fixture
def obsidian_source(integration_config):
    """Provide initialized ObsidianSource."""
    try:
        from src.sources.obsidian import ObsidianSource
        source = ObsidianSource(integration_config)
        if not source.is_enabled():
            pytest.skip("Obsidian not enabled in config")
        if not source.validate_config():
            pytest.skip("Obsidian validation failed")
        return source
    except Exception as e:
        pytest.skip(f"ObsidianSource not available: {e}")


# ============================================================================
# Markers
# ============================================================================

def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers",
        "unit: mark test as a unit test (fast, no external deps)"
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (requires Zotero/Obsidian)"
    )
    config.addinivalue_line(
        "markers",
        "pipeline: mark test as a pipeline/e2e test (full workflow)"
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow (takes > 1 minute)"
    )
    config.addinivalue_line(
        "markers",
        "requires_chromadb: test requires ChromaDB connection"
    )
    config.addinivalue_line(
        "markers",
        "requires_lmstudio: test requires LM Studio connection"
    )


# ============================================================================
# Hooks
# ============================================================================

def pytest_collection_modifyitems(config, items):
    """
    Automatically add markers based on test location.
    
    tests/unit/* -> @pytest.mark.unit
    tests/integration/* -> @pytest.mark.integration
    tests/pipeline/* -> @pytest.mark.pipeline
    """
    for item in items:
        file_path = str(item.fspath)
        
        if "/unit/" in file_path or "\\unit\\" in file_path:
            item.add_marker(pytest.mark.unit)
        elif "/integration/" in file_path or "\\integration\\" in file_path:
            item.add_marker(pytest.mark.integration)
        elif "/pipeline/" in file_path or "\\pipeline\\" in file_path:
            item.add_marker(pytest.mark.pipeline)
