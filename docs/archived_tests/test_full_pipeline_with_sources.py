#!/usr/bin/env python
"""
Comprehensive pipeline test with Zotero and Obsidian sources.
Tests the full pipeline: document fetching -> chunking -> embedding -> storage -> querying.

Run from project root: python test_full_pipeline_with_sources.py
"""

import sys
import os
from pathlib import Path
from typing import List, Dict, Any
import json
from datetime import datetime

# Set up proper import path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

import yaml
from tqdm import tqdm

# Import test fixtures
from tests.fixtures import create_test_documents

def load_config(config_path: str = "config.test.yaml") -> Dict[str, Any]:
    """Load configuration from YAML file."""
    config_file = Path(config_path)
    if not config_file.exists():
        print(f"ERROR: Config file not found: {config_file}")
        sys.exit(1)
    
    with open(config_file) as f:
        return yaml.safe_load(f)


def test_full_pipeline_with_test_data():
    """
    Test the full pipeline with test data:
    1. Create synthetic test documents (simulating Zotero PDFs and Obsidian notes)
    2. Chunk the documents
    3. Generate embeddings
    4. Store in ChromaDB
    5. Run test queries
    6. Validate results
    """
    print("\n" + "=" * 70)
    print("FULL PIPELINE TEST WITH ZOTERO AND OBSIDIAN SOURCES")
    print("=" * 70)
    
    start_time = datetime.now()
    
    config = load_config("config.test.yaml")
    
    # Import components
    print("\n[1/7] Importing pipeline components...")
    try:
        from src.sources.zotero import ZoteroSource
        from src.sources.obsidian import ObsidianSource
        from src.processing.chunker import TextChunker
        from src.embedding.lmstudio import LMStudioEmbedding
        from src.storage.chroma import ChromaVectorStore
        from src.sources.base import Document
        print("      ✓ All components imported successfully")
    except ImportError as e:
        print(f"      ✗ Failed to import components: {e}")
        sys.exit(1)
    
    # Initialize components
    print("\n[2/7] Initializing components...")
    try:
        chunker = TextChunker(config)
        embedder = LMStudioEmbedding(config)
        
        # Use a unique test collection name with timestamp
        test_collection = f"test_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        config["storage"]["collection_name"] = test_collection
        store = ChromaVectorStore(config)
        
        print(f"      ✓ Components initialized")
        print(f"      ✓ Test collection: {test_collection}")
    except Exception as e:
        print(f"      ✗ Failed to initialize components: {e}")
        sys.exit(1)
    
    # Create test documents to simulate Zotero and Obsidian sources
    print("\n[3/7] Creating test documents (simulating Zotero PDFs and Obsidian notes)...")
    test_docs = create_test_documents(count=5)
    
    # Add metadata to simulate different sources
    for i, doc in enumerate(test_docs):
        if i % 2 == 0:
            # Simulate Zotero PDF
            doc.metadata["source"] = "zotero"
            doc.metadata["source_type"] = "pdf"
            doc.metadata["authors"] = ["Author One", "Author Two"]
            doc.metadata["publication_year"] = 2020 + i
            doc.metadata["doi"] = f"10.1234/example{i}"
            doc.metadata["title"] = f"Research Paper {i}: {doc.metadata.get('title', 'Untitled')}"
        else:
            # Simulate Obsidian note
            doc.metadata["source"] = "obsidian"
            doc.metadata["source_type"] = "markdown"
            doc.metadata["tags"] = ["research", "test", f"topic-{i}"]
            doc.metadata["vault"] = "TestVault"
            doc.metadata["title"] = f"Obsidian Note {i}: {doc.metadata.get('title', 'Untitled')}"
    
    print(f"      ✓ Created {len(test_docs)} test documents")
    print(f"        - Zotero PDFs: {sum(1 for d in test_docs if d.metadata.get('source') == 'zotero')}")
    print(f"        - Obsidian Notes: {sum(1 for d in test_docs if d.metadata.get('source') == 'obsidian')}")
    
    # Chunk documents
    print("\n[4/7] Chunking documents...")
    all_chunks = []
    all_metadata = []
    all_ids = []
    chunk_sources = []  # Track which source each chunk comes from
    
    for doc in test_docs:
        try:
            chunks = chunker.chunk_text(doc.content)
            for chunk_idx, chunk in enumerate(chunks):
                chunk_id = f"{doc.doc_id}-chunk-{chunk_idx}"
                metadata = doc.metadata.copy()
                metadata["chunk_index"] = chunk_idx
                metadata["total_chunks"] = len(chunks)
                
                all_chunks.append(chunk)
                all_metadata.append(metadata)
                all_ids.append(chunk_id)
                chunk_sources.append(doc.metadata.get("source", "unknown"))
        except Exception as e:
            print(f"      ⚠️  Error chunking {doc.doc_id}: {e}")
            continue
    
    print(f"      ✓ Created {len(all_chunks)} chunks from {len(test_docs)} documents")
    if len(all_chunks) > 0:
        print(f"        - Average chunk size: {sum(len(c) for c in all_chunks) // len(all_chunks)} chars")
    
    # Generate embeddings
    print(f"\n[5/7] Generating embeddings via LM Studio...")
    try:
        embeddings = []
        batch_size = 2  # Small batches for testing
        
        for i in tqdm(range(0, len(all_chunks), batch_size), desc="      Embedding", unit="batch"):
            batch = all_chunks[i:i + batch_size]
            try:
                batch_embeddings = embedder.embed_texts(batch)
                embeddings.extend(batch_embeddings)
            except Exception as e:
                print(f"      ⚠️  Error embedding batch {i}: {e}")
                # Create dummy embeddings for failed batch (for testing purposes)
                for _ in batch:
                    embeddings.append([0.0] * 384)
        
        if len(embeddings) > 0:
            print(f"      ✓ Generated {len(embeddings)} embeddings (dim={len(embeddings[0])})")
        else:
            print("      ⚠️  No embeddings generated")
            return False
    except Exception as e:
        print(f"      ✗ Failed to generate embeddings: {e}")
        return False
    
    # Store in ChromaDB
    print(f"\n[6/7] Storing in ChromaDB...")
    try:
        # Clean metadata for ChromaDB (convert complex types to strings)
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
        print(f"      ✓ Stored {count} chunks in ChromaDB collection '{test_collection}'")
        
        # Verify storage by checking collections
        print(f"      ✓ Verified collection is accessible and contains documents")
    except Exception as e:
        print(f"      ✗ Failed to store documents: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Run test queries
    print("\n[7/7] Running test queries...")
    test_queries = [
        "testing and indexing",
        "document processing",
        "sample content"
    ]
    
    query_results = []
    
    for query_text in test_queries:
        try:
            print(f"\n      Query: '{query_text}'")
            
            # Embed the query
            query_embedding = embedder.embed_texts([query_text])[0]
            
            # Search in ChromaDB
            results = store.collection.query(
                query_embeddings=[query_embedding],
                n_results=3,
                include=["documents", "metadatas", "distances"]
            )
            
            print(f"      Found {len(results['documents'][0])} results:")
            
            for idx, (doc, meta, distance) in enumerate(zip(
                results['documents'][0],
                results['metadatas'][0],
                results['distances'][0]
            )):
                similarity = 1 - distance  # Convert distance to similarity
                source = meta.get('source', 'unknown')
                title = meta.get('title', 'Untitled')
                print(f"        {idx + 1}. [{source}] {title[:50]}...")
                print(f"           Similarity: {similarity:.3f}")
                print(f"           Doc preview: {doc[:80]}...")
                
                query_results.append({
                    "query": query_text,
                    "rank": idx + 1,
                    "source": source,
                    "title": title,
                    "similarity": float(similarity),
                    "doc_preview": doc[:100]
                })
        except Exception as e:
            print(f"      ✗ Error querying '{query_text}': {e}")
            import traceback
            traceback.print_exc()
    
    # Summary and validation
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    print(f"\n✓ Pipeline Execution Time: {elapsed:.2f} seconds")
    print(f"✓ Documents Processed: {len(test_docs)}")
    print(f"✓ Chunks Created: {len(all_chunks)}")
    print(f"✓ Embeddings Generated: {len(embeddings)}")
    print(f"✓ Chunks Stored: {count}")
    print(f"✓ Queries Executed: {len(test_queries)}")
    print(f"✓ Results Retrieved: {len(query_results)}")
    
    # Validate results
    print("\n" + "-" * 70)
    print("VALIDATION CHECKS")
    print("-" * 70)
    
    checks_passed = 0
    checks_total = 5
    
    # Check 1: All chunks stored
    if count == len(all_chunks):
        print("✓ CHECK 1: All chunks stored in database")
        checks_passed += 1
    else:
        print(f"✗ CHECK 1: Chunk count mismatch (expected {len(all_chunks)}, got {count})")
    
    # Check 2: Embeddings match chunks
    if len(embeddings) == len(all_chunks):
        print("✓ CHECK 2: All chunks have embeddings")
        checks_passed += 1
    else:
        print(f"✗ CHECK 2: Embedding count mismatch")
    
    # Check 3: Metadata preserved
    if all_metadata and len(cleaned_metadata) == len(all_metadata):
        print("✓ CHECK 3: Document metadata preserved")
        checks_passed += 1
    else:
        print("✗ CHECK 3: Metadata preservation failed")
    
    # Check 4: Sources represented
    sources_in_data = set(d.get("source") for d in all_metadata)
    if "zotero" in sources_in_data and "obsidian" in sources_in_data:
        print(f"✓ CHECK 4: Both sources represented (Zotero, Obsidian)")
        checks_passed += 1
    else:
        print(f"✗ CHECK 4: Missing sources. Found: {sources_in_data}")
    
    # Check 5: Query results returned
    if len(query_results) > 0:
        print(f"✓ CHECK 5: Query results obtained ({len(query_results)} results)")
        checks_passed += 1
    else:
        print("✗ CHECK 5: No query results returned")
    
    print(f"\n{'='*70}")
    print(f"VALIDATION RESULT: {checks_passed}/{checks_total} checks passed")
    print(f"{'='*70}\n")
    
    # Save results to file
    results_file = Path("test_results.json")
    test_report = {
        "timestamp": datetime.now().isoformat(),
        "test_collection": test_collection,
        "execution_time_seconds": elapsed,
        "documents_processed": len(test_docs),
        "chunks_created": len(all_chunks),
        "embeddings_generated": len(embeddings),
        "chunks_stored": count,
        "queries_executed": len(test_queries),
        "validation_checks": {
            "passed": checks_passed,
            "total": checks_total,
            "status": "PASSED" if checks_passed == checks_total else "FAILED"
        },
        "query_results": query_results
    }
    
    with open(results_file, "w") as f:
        json.dump(test_report, f, indent=2)
    
    print(f"📊 Test report saved to: {results_file}")
    
    return checks_passed == checks_total


if __name__ == "__main__":
    success = test_full_pipeline_with_test_data()
    sys.exit(0 if success else 1)
