# test_pipeline_fast.py - Fast end-to-end pipeline test without attachment extraction
# Run from project root: python test_pipeline_fast.py [num_zotero] [num_obsidian]

import sys
import os
from pathlib import Path

# Set up proper import path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

# Fix Unicode encoding for Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import yaml
from tqdm import tqdm
import time
import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PIPELINE_E2E") != "1",
    reason="legacy/manual pipeline E2E; set RUN_PIPELINE_E2E=1 with live services",
)


def load_config():
    """Load configuration from config.yaml"""
    config_path = Path("config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def test_end_to_end_pipeline(num_docs_zotero=10, num_docs_obsidian=10):
    """
    Test the full pipeline with multiple sources (fast mode - no attachment extraction):
    1. Fetch documents from Zotero and Obsidian
    2. Chunk the text
    3. Generate embeddings via LM Studio
    4. Store in ChromaDB
    5. Run a test query
    """
    print("=" * 70)
    print("END-TO-END PIPELINE TEST (ZOTERO + OBSIDIAN - FAST MODE)")
    print("=" * 70)
    
    start_time = time.time()
    
    config = load_config()
    
    # Disable attachment extraction for faster testing
    config["zotero"]["extract_attachments"] = False
    config["zotero"]["include_notes"] = True
    config["zotero"]["include_annotations"] = False
    
    # Import components
    from src.sources.zotero import ZoteroSource
    from src.sources.obsidian import ObsidianSource
    from src.processing.chunker import TextChunker
    from src.embedding.lmstudio import LMStudioEmbedding
    from src.storage.chroma import ChromaVectorStore
    
    # Initialize components
    print("\n[1/6] Initializing components...")
    zotero = ZoteroSource(config)
    obsidian = ObsidianSource(config)
    chunker = TextChunker(config)
    embedder = LMStudioEmbedding(config)
    
    # Use a test collection to avoid polluting the main one
    config["storage"]["collection_name"] = "test_pipeline_fast"
    store = ChromaVectorStore(config)
    
    # Fetch documents from both sources
    documents = []
    
    print(f"\n[2/6] Fetching {num_docs_zotero} documents from Zotero...")
    zotero_count = 0
    fetch_start = time.time()
    for doc in zotero.fetch_documents():
        documents.append(doc)
        zotero_count += 1
        if zotero_count >= num_docs_zotero:
            break
    zotero_time = time.time() - fetch_start
    print(f"      ✓ Fetched {zotero_count} documents from Zotero ({zotero_time:.2f}s)")
    
    print(f"\n[3/6] Fetching {num_docs_obsidian} documents from Obsidian...")
    obsidian_count = 0
    fetch_start = time.time()
    for doc in obsidian.fetch_documents():
        documents.append(doc)
        obsidian_count += 1
        if obsidian_count >= num_docs_obsidian:
            break
    obsidian_time = time.time() - fetch_start
    print(f"      ✓ Fetched {obsidian_count} documents from Obsidian ({obsidian_time:.2f}s)")
    
    print(f"\n      Total documents fetched: {len(documents)}")
    
    # Chunk documents
    print(f"\n[4/6] Chunking documents...")
    all_chunks = []
    all_metadata = []
    all_ids = []
    
    chunk_start = time.time()
    for doc in documents:
        chunks = chunker.chunk_text(doc.content)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc.doc_id}-chunk-{i}"
            metadata = doc.metadata.copy()
            metadata["chunk_index"] = i
            metadata["total_chunks"] = len(chunks)
            
            all_chunks.append(chunk)
            all_metadata.append(metadata)
            all_ids.append(chunk_id)
    
    chunk_time = time.time() - chunk_start
    print(f"      ✓ Created {len(all_chunks)} chunks from {len(documents)} documents ({chunk_time:.2f}s)")
    
    # Generate embeddings
    print(f"\n[5/6] Generating embeddings via LM Studio...")
    print(f"      This may take a moment...")
    
    embeddings = []
    batch_size = 8  # Smaller batch for progress visibility
    
    embed_start = time.time()
    for i in tqdm(range(0, len(all_chunks), batch_size), desc="      Embedding"):
        batch = all_chunks[i:i + batch_size]
        batch_embeddings = embedder.embed_texts(batch)
        embeddings.extend(batch_embeddings)
    
    embed_time = time.time() - embed_start
    print(f"      ✓ Generated {len(embeddings)} embeddings (dim={len(embeddings[0])}) ({embed_time:.2f}s)")
    
    # Store in ChromaDB
    print(f"\n[6/6] Storing in ChromaDB...")
    
    store_start = time.time()
    
    # ChromaDB expects string values in metadata
    cleaned_metadata = []
    for meta in all_metadata:
        cleaned = {}
        for k, v in meta.items():
            if isinstance(v, list):
                cleaned[k] = ", ".join(str(item) for item in v)
            elif v is None:
                cleaned[k] = ""
            else:
                cleaned[k] = str(v)
        cleaned_metadata.append(cleaned)
    
    # Store in batches to avoid timeout issues
    batch_size = 100
    total_stored = 0
    for i in tqdm(range(0, len(all_ids), batch_size), desc="      Storing"):
        batch_ids = all_ids[i:i + batch_size]
        batch_embeddings = embeddings[i:i + batch_size]
        batch_chunks = all_chunks[i:i + batch_size]
        batch_metadata = cleaned_metadata[i:i + batch_size]
        
        store.collection.add(
            ids=batch_ids,
            embeddings=batch_embeddings,
            documents=batch_chunks,
            metadatas=batch_metadata
        )
        total_stored += len(batch_ids)
    
    store_time = time.time() - store_start
    count = store.collection.count()
    print(f"      ✓ Stored {count} chunks in collection 'test_pipeline_fast' ({store_time:.2f}s)")
    
    # Test query
    print("\n" + "=" * 70)
    print("TESTING QUERIES")
    print("=" * 70)
    
    test_queries = [
        "What is embodied cognition?",
        "How does coaching relate to psychology?",
        "What are the key concepts in critical realism?"
    ]
    
    for query in test_queries:
        print(f"\n🔍 Query: '{query}'")
        
        # Embed the query
        query_embedding = embedder.embed_query(query)
        
        # Search
        results = store.collection.query(
            query_embeddings=[query_embedding],
            n_results=3,
            include=["documents", "metadatas", "distances"]
        )
        
        print(f"   Top 3 results:")
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        )):
            title = meta.get("title", "Untitled")[:50]
            source = meta.get("source_type", "unknown").replace("_", " ").title()
            similarity = 1 - (dist if dist is not None else 0)  # Convert distance to similarity
            print(f"   [{i+1}] ({similarity:.3f}) {source}: {title}...")
            print(f"       Preview: {doc[:80]}...")
    
    # Summary
    total_time = time.time() - start_time
    
    print("\n" + "=" * 70)
    print("PIPELINE TEST COMPLETE")
    print("=" * 70)
    print(f"""
Performance Summary:
  ╭─────────────────────────────────────────────────────────────╮
  │ Sources                                                     │
  ├─────────────────────────────────────────────────────────────┤
  │ • Zotero documents:      {zotero_count:6d}  ({zotero_time:6.2f}s)           │
  │ • Obsidian documents:    {obsidian_count:6d}  ({obsidian_time:6.2f}s)           │
  │ • Total documents:       {len(documents):6d}                         │
  ├─────────────────────────────────────────────────────────────┤
  │ Processing                                                  │
  ├─────────────────────────────────────────────────────────────┤
  │ • Chunks created:        {len(all_chunks):6d}  ({chunk_time:6.2f}s)           │
  │ • Embeddings generated:  {len(embeddings):6d}  ({embed_time:6.2f}s)           │
  │ • Stored in ChromaDB:    {count:6d}  ({store_time:6.2f}s)           │
  │ • Test queries run:      {len(test_queries):6d}                         │
  ├─────────────────────────────────────────────────────────────┤
  │ TOTAL TIME:              {total_time:6.2f}s                         │
  ╰─────────────────────────────────────────────────────────────╯

✅ End-to-end pipeline with multiple sources is working!
    """)
    
    return True


if __name__ == "__main__":
    # Allow specifying number of docs from command line
    # Usage: python test_pipeline_fast.py [num_zotero] [num_obsidian]
    num_docs_zotero = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    num_docs_obsidian = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    
    try:
        test_end_to_end_pipeline(num_docs_zotero, num_docs_obsidian)
    except Exception as e:
        print(f"\n❌ Pipeline test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
