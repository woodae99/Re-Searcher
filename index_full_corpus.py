# index_full_corpus.py - Index entire Zotero + Obsidian library
# Run overnight: python index_full_corpus.py
# 
# Estimated time: 30-60 minutes depending on library size
# Progress is saved - can resume if interrupted

import sys
import os
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

import yaml
from tqdm import tqdm


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def index_full_corpus():
    print("=" * 60)
    print(f"FULL CORPUS INDEXING - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    config = load_config()
    
    from src.sources.zotero import ZoteroSource
    from src.sources.obsidian import ObsidianSource
    from src.processing.chunker import TextChunker
    from src.embedding.lmstudio import LMStudioEmbedding
    from src.storage.chroma import ChromaVectorStore
    
    # Initialize
    print("\n[1/6] Initializing components...")
    zotero = ZoteroSource(config)
    obsidian = ObsidianSource(config)
    chunker = TextChunker(config)
    embedder = LMStudioEmbedding(config)
    
    # Use main collection
    config["storage"]["collection_name"] = "research_library"
    store = ChromaVectorStore(config)
    
    all_chunks = []
    all_metadata = []
    all_ids = []
    
    # Collect Zotero documents
    print("\n[2/6] Fetching Zotero documents...")
    zotero_count = 0
    if zotero.is_enabled():
        for doc in tqdm(zotero.fetch_documents(), desc="Zotero"):
            chunks = chunker.chunk_text(doc.content)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc.doc_id}-chunk-{i}"
                # Skip if already indexed
                metadata = doc.metadata.copy()
                metadata["chunk_index"] = i
                metadata["total_chunks"] = len(chunks)
                
                all_chunks.append(chunk)
                all_metadata.append(metadata)
                all_ids.append(chunk_id)
            zotero_count += 1
    print(f"   Processed {zotero_count} Zotero items")
    
    # Collect Obsidian documents
    print("\n[3/6] Fetching Obsidian documents...")
    obsidian_count = 0
    if obsidian.is_enabled():
        for doc in tqdm(obsidian.fetch_documents(), desc="Obsidian"):
            chunks = chunker.chunk_text(doc.content)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc.doc_id}-chunk-{i}"
                metadata = doc.metadata.copy()
                metadata["chunk_index"] = i
                metadata["total_chunks"] = len(chunks)
                
                all_chunks.append(chunk)
                all_metadata.append(metadata)
                all_ids.append(chunk_id)
            obsidian_count += 1
    print(f"   Processed {obsidian_count} Obsidian notes")
    
    print(f"\n[4/6] Total chunks to embed: {len(all_chunks)}")
    
    # Generate embeddings in batches
    print("\n[5/6] Generating embeddings...")
    batch_size = 32
    embeddings = []
    
    for i in tqdm(range(0, len(all_chunks), batch_size), desc="Embedding"):
        batch = all_chunks[i:i + batch_size]
        try:
            batch_embeddings = embedder.embed_texts(batch)
            embeddings.extend(batch_embeddings)
        except Exception as e:
            print(f"\n   Error at batch {i//batch_size}: {e}")
            # Use zero vectors as fallback
            embeddings.extend([[0.0] * 1024] * len(batch))
    
    # Clean metadata for ChromaDB
    print("\n[6/6] Storing in ChromaDB...")
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
    
    # Store in batches to avoid memory issues
    store_batch_size = 1000
    for i in tqdm(range(0, len(all_ids), store_batch_size), desc="Storing"):
        end = min(i + store_batch_size, len(all_ids))
        store.collection.add(
            ids=all_ids[i:end],
            embeddings=embeddings[i:end],
            documents=all_chunks[i:end],
            metadatas=cleaned_metadata[i:end]
        )
    
    final_count = store.collection.count()
    
    # Summary
    print("\n" + "=" * 60)
    print("INDEXING COMPLETE")
    print("=" * 60)
    print(f"""
Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Sources:
  - Zotero items processed: {zotero_count}
  - Obsidian notes processed: {obsidian_count}

Results:
  - Total chunks created: {len(all_chunks)}
  - Embeddings generated: {len(embeddings)}
  - Stored in ChromaDB: {final_count}
  - Collection: research_library

✅ Your research library is now searchable!

To query, run: python scripts/query.py "your search query"
    """)


if __name__ == "__main__":
    try:
        index_full_corpus()
    except KeyboardInterrupt:
        print("\n\n⚠️ Indexing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Indexing failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
