#!/usr/bin/env python
"""
Database query validation script.
Queries the ChromaDB test collection to verify stored documents and results.

Run from project root: python validate_test_database.py
"""

import sys
import os
from pathlib import Path
from typing import Dict, Any, List
import json
from datetime import datetime

# Set up proper import path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

import yaml


def load_config(config_path: str = "config.test.yaml") -> Dict[str, Any]:
    """Load configuration from YAML file."""
    config_file = Path(config_path)
    if not config_file.exists():
        print(f"ERROR: Config file not found: {config_file}")
        sys.exit(1)
    
    with open(config_file) as f:
        return yaml.safe_load(f)


def validate_database_results(test_collection: str = None):
    """
    Validate the ChromaDB test collection.
    Queries the database and verifies the stored documents.
    """
    print("\n" + "=" * 70)
    print("CHROMADB TEST COLLECTION VALIDATION")
    print("=" * 70)
    
    config = load_config("config.test.yaml")
    
    # Import ChromaDB store
    print("\n[1/5] Initializing ChromaDB connection...")
    try:
        from src.storage.chroma import ChromaVectorStore
        from src.embedding.lmstudio import LMStudioEmbedding
        
        # If no collection specified, use the one from test config
        if not test_collection:
            test_collection = config["storage"]["collection_name"]
        
        config["storage"]["collection_name"] = test_collection
        store = ChromaVectorStore(config)
        embedder = LMStudioEmbedding(config)
        
        print(f"      ✓ Connected to ChromaDB")
        print(f"      ✓ Collection: {test_collection}")
    except Exception as e:
        print(f"      ✗ Failed to connect to ChromaDB: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Check collection stats
    print("\n[2/5] Retrieving collection statistics...")
    try:
        collection_count = store.collection.count()
        print(f"      ✓ Total documents in collection: {collection_count}")
        
        if collection_count == 0:
            print("      ⚠️  Collection is empty!")
            return False
    except Exception as e:
        print(f"      ✗ Failed to get collection stats: {e}")
        return False
    
    # Analyze metadata
    print("\n[3/5] Analyzing stored metadata...")
    try:
        # Get all documents to analyze metadata
        all_docs = store.collection.get(
            include=["documents", "metadatas"]
        )
        
        if all_docs and all_docs['metadatas']:
            metadatas = all_docs['metadatas']
            
            # Analyze sources
            sources = {}
            doc_types = {}
            chunk_indices = {}
            
            for meta in metadatas:
                source = meta.get('source', 'unknown')
                doc_type = meta.get('source_type', 'unknown')
                chunk_idx = meta.get('chunk_index', 'unknown')
                
                sources[source] = sources.get(source, 0) + 1
                doc_types[doc_type] = doc_types.get(doc_type, 0) + 1
                chunk_indices[str(chunk_idx)] = chunk_indices.get(str(chunk_idx), 0) + 1
            
            print(f"      ✓ Documents by source:")
            for source, count in sorted(sources.items()):
                print(f"        - {source}: {count}")
            
            print(f"      ✓ Documents by type:")
            for doc_type, count in sorted(doc_types.items()):
                print(f"        - {doc_type}: {count}")
            
            print(f"      ✓ Chunk distribution: {len(chunk_indices)} different chunk indices")
        else:
            print("      ⚠️  No metadata found")
    except Exception as e:
        print(f"      ✗ Failed to analyze metadata: {e}")
        import traceback
        traceback.print_exc()
    
    # Run validation queries
    print("\n[4/5] Running validation queries...")
    validation_queries = {
        "generic": "testing and indexing pipeline",
        "content": "document processing and chunking",
        "system": "sample test data"
    }
    
    query_results = {}
    validation_data = []
    
    for query_name, query_text in validation_queries.items():
        try:
            print(f"\n      Query: '{query_text}'")
            
            # Embed the query
            query_embedding = embedder.embed_texts([query_text])[0]
            
            # Search in ChromaDB
            results = store.collection.query(
                query_embeddings=[query_embedding],
                n_results=5,
                include=["documents", "metadatas", "distances"]
            )
            
            num_results = len(results['documents'][0]) if results['documents'] else 0
            print(f"      ✓ Results found: {num_results}")
            
            top_results = []
            for idx in range(num_results):
                doc = results['documents'][0][idx]
                meta = results['metadatas'][0][idx]
                distance = results['distances'][0][idx]
                similarity = 1 - distance
                
                result_info = {
                    "rank": idx + 1,
                    "source": meta.get('source', 'unknown'),
                    "source_type": meta.get('source_type', 'unknown'),
                    "title": meta.get('title', 'Untitled'),
                    "similarity": float(similarity),
                    "doc_id": meta.get('id', 'unknown'),
                    "chunk_index": meta.get('chunk_index', -1)
                }
                top_results.append(result_info)
                
                print(f"        {idx + 1}. [Similarity: {similarity:.3f}] {meta.get('source', 'unknown')}: {meta.get('title', 'Untitled')[:50]}")
                print(f"           Preview: {doc[:80]}...")
            
            query_results[query_name] = {
                "query": query_text,
                "results_count": num_results,
                "top_results": top_results
            }
            
            validation_data.append({
                "query_type": query_name,
                "query": query_text,
                "results": num_results,
                "data": top_results
            })
        except Exception as e:
            print(f"      ✗ Error running query '{query_name}': {e}")
            import traceback
            traceback.print_exc()
    
    # Generate validation report
    print("\n" + "=" * 70)
    print("VALIDATION REPORT")
    print("=" * 70)
    
    print(f"\n✓ Collection Statistics:")
    print(f"  - Total documents: {collection_count}")
    print(f"  - Queries executed: {len(validation_queries)}")
    print(f"  - Successful queries: {sum(1 for v in validation_queries if v in query_results)}")
    
    if collection_count > 0:
        print(f"\n✓ Data Quality Checks:")
        
        # Check 1: Data diversity
        if sources and len(sources) > 1:
            print(f"  ✓ Multiple sources present: {list(sources.keys())}")
        else:
            print(f"  ⚠️  Limited source diversity")
        
        # Check 2: Chunk distribution
        if chunk_indices and len(chunk_indices) > 1:
            print(f"  ✓ Chunking present: {len(chunk_indices)} different chunk indices")
        else:
            print(f"  ⚠️  Limited chunk distribution")
        
        # Check 3: Query results
        avg_results = sum(v['results_count'] for v in query_results.values()) / len(query_results) if query_results else 0
        if avg_results > 0:
            print(f"  ✓ Average query results: {avg_results:.1f} per query")
        else:
            print(f"  ✗ No query results obtained")
    
    # Save validation report
    report_file = Path("database_validation_report.json")
    validation_report = {
        "timestamp": datetime.now().isoformat(),
        "collection": test_collection,
        "statistics": {
            "total_documents": collection_count,
            "sources": sources if 'sources' in locals() else {},
            "document_types": doc_types if 'doc_types' in locals() else {},
            "chunks": len(chunk_indices) if 'chunk_indices' in locals() else 0
        },
        "queries": validation_data,
        "validation_status": "PASSED" if collection_count > 0 else "FAILED"
    }
    
    with open(report_file, "w") as f:
        json.dump(validation_report, f, indent=2)
    
    print(f"\n📊 Validation report saved to: {report_file}")
    
    print("\n" + "=" * 70)
    print(f"VALIDATION {'PASSED ✓' if collection_count > 0 else 'FAILED ✗'}")
    print("=" * 70 + "\n")
    
    return collection_count > 0


def list_available_collections():
    """List all available collections in ChromaDB."""
    print("\n" + "=" * 70)
    print("AVAILABLE CHROMADB COLLECTIONS")
    print("=" * 70 + "\n")
    
    config = load_config("config.test.yaml")
    
    try:
        from src.storage.chroma import ChromaVectorStore
        
        # Create a temporary store just to get access to the client
        store = ChromaVectorStore(config)
        
        # List all collections
        collections = store.client.list_collections()
        
        if collections:
            print(f"Found {len(collections)} collection(s):\n")
            for collection in collections:
                name = collection.name if hasattr(collection, 'name') else collection.get('name', 'Unknown')
                count = collection.count() if hasattr(collection, 'count') else 'Unknown'
                print(f"  • {name}")
                if count != 'Unknown':
                    print(f"    Documents: {count}")
            print()
        else:
            print("No collections found in ChromaDB\n")
    except Exception as e:
        print(f"Error listing collections: {e}\n")


if __name__ == "__main__":
    # List available collections first
    list_available_collections()
    
    # Validate the latest test collection (test_pipeline_20260106_080225)
    success = validate_database_results(test_collection="test_pipeline_20260106_080225")
    
    sys.exit(0 if success else 1)
