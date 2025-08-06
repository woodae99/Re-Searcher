# semantic_search.py

from pathlib import Path
from typing import List, Dict, Tuple
import yaml
import faiss
import os
import json
import numpy as np
import textwrap
import hashlib

from sentence_transformers import SentenceTransformer
from extract_text import load_config, walk_and_extract
from main import chunk_documents


INDEX_FILE = "semantic_index.faiss"
CHUNK_META = "semantic_chunks.json"


def embed_chunks(chunked_docs: Dict[str, List[str]], model_name: str = 'all-MiniLM-L6-v2') -> Tuple[List[str], List[str], np.ndarray]:
    print("\n🧠 Generating embeddings...")
    model = SentenceTransformer(model_name)
    texts = []
    file_refs = []
    for path, chunks in chunked_docs.items():
        for chunk in chunks:
            texts.append(chunk)
            file_refs.append(path)
    embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
    return file_refs, texts, embeddings


def create_faiss_index(embeddings: np.ndarray) -> faiss.IndexFlatL2:
    index = faiss.IndexFlatL2(embeddings.shape[1])
    index.add(embeddings)
    return index


def semantic_search(index, query: str, model, texts: List[str], refs: List[str], k: int = 5):
    q_vec = model.encode([query], convert_to_numpy=True)
    D, I = index.search(q_vec, k)
    results = [(refs[i], texts[i], float(D[0][rank])) for rank, i in enumerate(I[0])]
    return results


def save_index(index: faiss.IndexFlatL2, index_path: Path):
    faiss.write_index(index, str(index_path))


def save_metadata(refs: List[str], chunks: List[str], output_path: Path):
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({"refs": refs, "chunks": chunks}, f, indent=2)


def load_index(index_path: Path) -> faiss.IndexFlatL2:
    return faiss.read_index(str(index_path))


def load_metadata(meta_path: Path) -> Tuple[List[str], List[str]]:
    with open(meta_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['refs'], data['chunks']


def hash_folder(folder: Path) -> str:
    """Create a hash of file paths and modification times."""
    h = hashlib.md5()
    for file in sorted(folder.rglob("*")):
        if file.is_file():
            h.update(str(file).encode())
            h.update(str(file.stat().st_mtime).encode())
    return h.hexdigest()


if __name__ == "__main__":
    config_path = Path(__file__).parent / "config.yaml"
    cfg = load_config(config_path)
    output_dir = Path(cfg['output_folder'])
    os.makedirs(output_dir, exist_ok=True)

    index_path = output_dir / INDEX_FILE
    meta_path = output_dir / CHUNK_META
    hash_path = output_dir / "source_hash.txt"

    current_hash = hash_folder(Path(cfg['input_folder']))

    if index_path.exists() and meta_path.exists() and hash_path.exists():
        previous_hash = hash_path.read_text().strip()
        if previous_hash == current_hash:
            print("\n📦 Using cached FAISS index and chunk metadata...")
            index = load_index(index_path)
            refs, chunks = load_metadata(meta_path)
            model = SentenceTransformer("all-MiniLM-L6-v2")
        else:
            print("\n🔄 Source files changed. Rebuilding semantic index...")
            docs = walk_and_extract(cfg)
            chunked = chunk_documents(docs, cfg)
            refs, chunks, embeddings = embed_chunks(chunked)
            index = create_faiss_index(embeddings)
            save_index(index, index_path)
            save_metadata(refs, chunks, meta_path)
            hash_path.write_text(current_hash)
            model = SentenceTransformer("all-MiniLM-L6-v2")
    else:
        print("\n⚙️ No cached index found. Creating new semantic index...")
        docs = walk_and_extract(cfg)
        chunked = chunk_documents(docs, cfg)
        refs, chunks, embeddings = embed_chunks(chunked)
        index = create_faiss_index(embeddings)
        save_index(index, index_path)
        save_metadata(refs, chunks, meta_path)
        hash_path.write_text(current_hash)
        model = SentenceTransformer("all-MiniLM-L6-v2")

    print("\n✅ Semantic index ready. Type a query to test it:\n")

    while True:
        query = input("🧠 semantic> ").strip()
        if query.lower() in ('q', 'quit', 'exit'):
            break
        results = semantic_search(index, query, model, chunks, refs, k=5)
        for path, text, dist in results:
            print(f"📄 {path} [score: {dist:.4f}]\n{textwrap.shorten(text, width=300)}\n")
