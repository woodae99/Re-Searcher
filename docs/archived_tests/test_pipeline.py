# test_pipeline.py - End-to-end pipeline test
# Run from project root: python test_pipeline.py

import sys
import os
from pathlib import Path

# Set up proper import path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

import yaml
from tqdm import tqdm


def load_config():
    """Load configuration from config.yaml"""
    config_path = Path("config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def test_end_to_end_pipeline(num_docs_zotero=10, num_docs_obsidian=10):
    """
    Test the full pipeline with multiple sources:
    1. Fetch documents from Zotero and Obsidian
    2. Chunk the text
    3. Generate embeddings via LM Studio
    4. Store in ChromaDB
    5. Run a test query
    """
    print("=" * 60)
    print("END-TO-END PIPELINE TEST (ZOTERO + OBSIDIAN)")
    print("=" * 60)
    
    config = load_config()
    
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
    config["storage"]["collection_name"] = "test_pipeline"
    store = ChromaVectorStore(config)
    
    # Fetch documents from both sources
    documents = []
    
    print(f"\n[2/6] Fetching {num_docs_zotero} documents from Zotero...")
    zotero_count = 0
    for doc in zotero.fetch_documents():
        documents.append(doc)
        zotero_count += 1
        if zotero_count >= num_docs_zotero:
            break
    print(f"      Fetched {zotero_count} documents from Zotero")
    
    print(f"\n[3/6] Fetching {num_docs_obsidian} documents from Obsidian...")
    obsidian_count = 0
    for doc in obsidian.fetch_documents():
        documents.append(doc)
        obsidian_count += 1
        if obsidian_count >= num_docs_obsidian:
            break
    print(f"      Fetched {obsidian_count} documents from Obsidian")
    
    print(f"\n      Total documents fetched: {len(documents)}")
    
    # Chunk documents
    print(f"\n[4/6] Chunking documents...")
    all_chunks = []
    all_metadata = []
    all_ids = []
    
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
    
    print(f"      Created {len(all_chunks)} chunks from {len(documents)} documents")
    
    # Generate embeddings
    print(f"\n[5/6] Generating embeddings via LM Studio...")
    print(f"      This may take a moment...")
    
    embeddings = []
    batch_size = 8  # Smaller batch for progress visibility
    
    for i in tqdm(range(0, len(all_chunks), batch_size), desc="      Embedding"):
        batch = all_chunks[i:i + batch_size]
        batch_embeddings = embedder.embed_texts(batch)
        embeddings.extend(batch_embeddings)
    
    print(f"      Generated {len(embeddings)} embeddings (dim={len(embeddings[0])})")
    
    # Store in ChromaDB
    print(f"\n[6/6] Storing in ChromaDB...")
    
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
    
    store.collection.add(
        ids=all_ids,
        embeddings=embeddings,
        documents=all_chunks,
        metadatas=cleaned_metadata
    )
    
    count = store.collection.count()
    print(f"      Stored {count} chunks in collection 'test_pipeline'")
    
    # Test query
    print("\n" + "=" * 60)
    print("TESTING QUERY")
    print("=" * 60)
    
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
            title = meta.get("title", "Untitled")[:40]
            source = meta.get("source_type", "unknown")
            print(f"   [{i+1}] ({1-dist:.3f}) {source}: {title}...")
            print(f"       Preview: {doc[:80]}...")
    
    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE TEST COMPLETE")
    print("=" * 60)
    print(f"""
Summary:
  - Documents from Zotero: {zotero_count}
  - Documents from Obsidian: {obsidian_count}
  - Total documents: {len(documents)}
  - Chunks created: {len(all_chunks)}
  - Embeddings generated: {len(embeddings)}
  - Stored in ChromaDB: {count}
  - Test queries run: {len(test_queries)}

✅ End-to-end pipeline with multiple sources is working!
    """)
    
    return True


if __name__ == "__main__":
    # Allow specifying number of docs from command line
    # Usage: python test_pipeline.py [num_zotero] [num_obsidian]
    num_docs_zotero = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    num_docs_obsidian = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    
    try:
        test_end_to_end_pipeline(num_docs_zotero, num_docs_obsidian)
    except Exception as e:
        print(f"\n❌ Pipeline test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
