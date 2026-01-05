# test_components.py - Test individual Re-Searcher components
# Run from project root: python test_components.py

import sys
import os
from pathlib import Path

# Set up proper import path - add the project root so 'src' is importable
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Change to project directory
os.chdir(project_root)

import yaml


def load_config():
    """Load configuration from config.yaml"""
    config_path = Path("config.yaml")
    if not config_path.exists():
        print("❌ config.yaml not found! Copy config.example.yaml to config.yaml")
        return None
    
    with open(config_path) as f:
        return yaml.safe_load(f)


def test_zotero_source(config):
    """Test Zotero source - can it connect and fetch items?"""
    print("\n" + "=" * 50)
    print("TEST 1: Zotero Source")
    print("=" * 50)
    
    from src.sources.zotero import ZoteroSource
    
    zotero = ZoteroSource(config)
    
    if not zotero.is_enabled():
        print("⚠️  Zotero is disabled in config")
        return False
    
    if not zotero.validate_config():
        print("❌ Zotero config validation failed")
        return False
    
    print(f"✅ Zotero data dir: {zotero.data_dir}")
    print(f"✅ Database exists: {zotero.db_path.exists()}")
    
    # Try to fetch first 5 documents
    print("\n📚 Fetching first 5 documents...")
    count = 0
    for doc in zotero.fetch_documents():
        count += 1
        print(f"\n  [{count}] {doc.metadata.get('source_type')}: {doc.metadata.get('title', 'Untitled')[:50]}")
        print(f"      Content preview: {doc.content[:100]}...")
        
        if count >= 5:
            break
    
    print(f"\n✅ Successfully fetched {count} documents from Zotero")
    return count > 0


def test_obsidian_source(config):
    """Test Obsidian source - can it read markdown files?"""
    print("\n" + "=" * 50)
    print("TEST 2: Obsidian Source")
    print("=" * 50)
    
    from src.sources.obsidian import ObsidianSource
    
    obsidian = ObsidianSource(config)
    
    if not obsidian.is_enabled():
        print("⚠️  Obsidian is disabled in config")
        return False
    
    if not obsidian.validate_config():
        print("❌ Obsidian config validation failed")
        return False
    
    print(f"✅ Vault path: {obsidian.vault_path}")
    print(f"✅ Include folders: {obsidian.obsidian_config.get('include_folders', [])}")
    print(f"✅ Exclude folders: {obsidian.obsidian_config.get('exclude_folders', [])}")
    
    # Try to fetch first 5 documents
    print("\n📝 Fetching first 5 notes...")
    count = 0
    for doc in obsidian.fetch_documents():
        count += 1
        print(f"\n  [{count}] {doc.metadata.get('title', 'Untitled')}")
        print(f"      Tags: {doc.metadata.get('tags', [])}")
        print(f"      Content preview: {doc.content[:100]}...")
        
        if count >= 5:
            break
    
    print(f"\n✅ Successfully fetched {count} notes from Obsidian")
    return count > 0


def test_chunker(config):
    """Test text chunker"""
    print("\n" + "=" * 50)
    print("TEST 3: Text Chunker")
    print("=" * 50)
    
    from src.processing.chunker import TextChunker
    
    chunker = TextChunker(config)
    
    # Test with sample text
    sample_text = """
    This is a test document about research methodology. 
    
    The first section discusses qualitative methods including interviews, 
    focus groups, and ethnographic observation. These methods are particularly 
    useful for understanding complex social phenomena.
    
    The second section covers quantitative approaches such as surveys, 
    experiments, and statistical analysis. These provide numerical data 
    that can be analyzed for patterns and correlations.
    
    Finally, mixed methods combine both approaches to provide a more 
    comprehensive understanding of research questions.
    """ * 10  # Make it longer to trigger chunking
    
    chunks = chunker.chunk_text(sample_text)
    
    print(f"✅ Input text length: {len(sample_text)} chars")
    print(f"✅ Number of chunks: {len(chunks)}")
    
    for i, chunk in enumerate(chunks[:3]):
        print(f"\n  Chunk {i+1}: {len(chunk)} chars")
        print(f"  Preview: {chunk[:80]}...")
    
    return True


def test_lmstudio_connection(config):
    """Test LM Studio embedding connection"""
    print("\n" + "=" * 50)
    print("TEST 4: LM Studio Embedding")
    print("=" * 50)
    
    from src.embedding.lmstudio import LMStudioEmbedding
    
    embedder = LMStudioEmbedding(config)
    
    print(f"Endpoint: {embedder.endpoint}")
    print(f"Model: {embedder.model}")
    
    # Test connection with a simple embedding
    try:
        test_texts = ["This is a test sentence about research."]
        embeddings = embedder.embed_texts(test_texts)
        
        print(f"\n✅ Connection successful!")
        print(f"✅ Embedding dimension: {len(embeddings[0])}")
        print(f"✅ First 5 values: {embeddings[0][:5]}")
        return True
        
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\n⚠️  Make sure LM Studio is running with BGE-M3 model loaded")
        print("   and the server is started on port 1234")
        return False


def test_chroma_connection(config):
    """Test ChromaDB connection"""
    print("\n" + "=" * 50)
    print("TEST 5: ChromaDB Connection")
    print("=" * 50)
    
    from src.storage.chroma import ChromaVectorStore
    
    store = ChromaVectorStore(config)
    
    print(f"Endpoint: {store.endpoint}")
    print(f"Collection: {store.collection_name}")
    
    try:
        # Test connection - collection is created in __init__
        count = store.collection.count()
        
        print(f"\n✅ Connection successful!")
        print(f"✅ Current document count: {count}")
        return True
        
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\n⚠️  Make sure ChromaDB is running:")
        print("   docker run -p 8000:8000 chromadb/chroma")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("Re-Searcher Component Tests")
    print("=" * 50)
    
    config = load_config()
    if not config:
        sys.exit(1)
    
    results = {}
    
    # Run tests - wrap each in try/except to continue on failure
    try:
        results["Zotero"] = test_zotero_source(config)
    except Exception as e:
        print(f"❌ Zotero test crashed: {e}")
        results["Zotero"] = False
    
    try:
        results["Obsidian"] = test_obsidian_source(config)
    except Exception as e:
        print(f"❌ Obsidian test crashed: {e}")
        results["Obsidian"] = False
    
    try:
        results["Chunker"] = test_chunker(config)
    except Exception as e:
        print(f"❌ Chunker test crashed: {e}")
        results["Chunker"] = False
    
    try:
        results["LM Studio"] = test_lmstudio_connection(config)
    except Exception as e:
        print(f"❌ LM Studio test crashed: {e}")
        results["LM Studio"] = False
    
    try:
        results["ChromaDB"] = test_chroma_connection(config)
    except Exception as e:
        print(f"❌ ChromaDB test crashed: {e}")
        results["ChromaDB"] = False
    
    # Summary
    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)
    
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
    
    all_passed = all(results.values())
    print("\n" + ("🎉 All tests passed!" if all_passed else "⚠️  Some tests failed"))
