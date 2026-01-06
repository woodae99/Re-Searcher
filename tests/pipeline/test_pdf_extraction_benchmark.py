# test_pdf_extraction_benchmark.py - Benchmark PDF extraction performance
# Run from project root: python test_pdf_extraction_benchmark.py [num_zotero_with_pdfs]

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
import time
import sqlite3
from pathlib import Path as PathlibPath


def load_config():
    """Load configuration from config.yaml"""
    config_path = Path("config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def test_pdf_extraction_benchmark(num_docs_with_pdfs=10):
    """
    Benchmark PDF extraction performance.
    Tests how long it takes to extract text from PDF attachments.
    """
    print("=" * 70)
    print("PDF EXTRACTION BENCHMARK")
    print("=" * 70)
    
    config = load_config()
    
    # Only enable PDF extraction
    config["zotero"]["extract_attachments"] = True
    config["zotero"]["include_notes"] = False
    config["zotero"]["include_annotations"] = False
    config["zotero"]["max_extraction_threads"] = 4
    
    from src.sources.zotero import ZoteroSource
    from src.processing.chunker import TextChunker
    from src.embedding.lmstudio import LMStudioEmbedding
    from src.storage.chroma import ChromaVectorStore
    
    print("\n[1/5] Initializing components...")
    zotero = ZoteroSource(config)
    chunker = TextChunker(config)
    embedder = LMStudioEmbedding(config)
    
    config["storage"]["collection_name"] = "test_pdf_extraction"
    store = ChromaVectorStore(config)
    
    print(f"\n[2/5] Fetching {num_docs_with_pdfs} documents from Zotero (with PDF extraction)...")
    print("      This may take several minutes depending on PDF sizes...\n")
    
    documents = []
    attachment_count = 0
    fetch_start = time.time()
    
    for i, doc in enumerate(zotero.fetch_documents(), 1):
        documents.append(doc)
        if doc.metadata.get("source_type") == "zotero_fulltext":
            attachment_count += 1
            filename = doc.metadata.get("file_name", "unknown")
            print(f"      [{i}] Extracted: {filename}")
        
        if len(documents) >= num_docs_with_pdfs:
            break
    
    zotero_time = time.time() - fetch_start
    print(f"\n      ✓ Fetched {len(documents)} items ({attachment_count} PDFs extracted) ({zotero_time:.2f}s)")
    
    if len(documents) == 0:
        print("\n⚠️  No documents found. Zotero may not be accessible.")
        return False
    
    # Chunk documents
    print(f"\n[3/5] Chunking {len(documents)} documents...")
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
    print(f"      ✓ Created {len(all_chunks)} chunks ({chunk_time:.2f}s)")
    print(f"      Average: {len(all_chunks) / len(documents):.1f} chunks per document")
    
    # Generate embeddings
    print(f"\n[4/5] Generating {len(all_chunks)} embeddings...")
    
    from tqdm import tqdm
    embeddings = []
    batch_size = 8
    
    embed_start = time.time()
    for i in tqdm(range(0, len(all_chunks), batch_size), desc="      Embedding"):
        batch = all_chunks[i:i + batch_size]
        batch_embeddings = embedder.embed_texts(batch)
        embeddings.extend(batch_embeddings)
    
    embed_time = time.time() - embed_start
    print(f"      ✓ Generated {len(embeddings)} embeddings (dim={len(embeddings[0])}) ({embed_time:.2f}s)")
    print(f"      Rate: {len(embeddings) / embed_time:.1f} embeddings/sec")
    
    # Store in ChromaDB
    print(f"\n[5/5] Storing in ChromaDB...")
    
    store_start = time.time()
    
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
    
    # Store in batches
    batch_size = 100
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
    
    store_time = time.time() - store_start
    count = store.collection.count()
    
    total_time = zotero_time + chunk_time + embed_time + store_time
    
    print("\n" + "=" * 70)
    print("PDF EXTRACTION BENCHMARK RESULTS")
    print("=" * 70)
    print(f"""
Extraction & Processing Performance:
  ╭─────────────────────────────────────────────────────────────╮
  │ PDF Extraction (Zotero)                                     │
  ├─────────────────────────────────────────────────────────────┤
  │ • Items processed:       {len(documents):6d}                        │
  │ • PDFs extracted:        {attachment_count:6d}                        │
  │ • Extraction time:       {zotero_time:6.2f}s                         │
  │ • Rate:                  {attachment_count / zotero_time:6.2f} PDFs/sec                 │
  ├─────────────────────────────────────────────────────────────┤
  │ Text Processing                                             │
  ├─────────────────────────────────────────────────────────────┤
  │ • Chunks created:        {len(all_chunks):6d}  ({chunk_time:6.2f}s)           │
  │ • Embeddings generated:  {len(embeddings):6d}  ({embed_time:6.2f}s)           │
  │ • Stored in ChromaDB:    {count:6d}  ({store_time:6.2f}s)           │
  ├─────────────────────────────────────────────────────────────┤
  │ TOTAL TIME:              {total_time:6.2f}s                         │
  ╰─────────────────────────────────────────────────────────────╯

✅ PDF extraction benchmark complete!
    """)
    
    return True


if __name__ == "__main__":
    # Note: PDF extraction is resource-intensive
    # Start with small numbers and increase as needed
    num_docs = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    
    try:
        test_pdf_extraction_benchmark(num_docs)
    except Exception as e:
        print(f"\nBenchmark failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
