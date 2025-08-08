# semantic_search.py

import hashlib
import json
import os
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple

import faiss
import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

from .extract_text import extract_text, load_config, walk_and_extract
from .main import chunk_documents
from .zotero import get_zotero_data

INDEX_FILE = "semantic_index.faiss"
CHUNK_META = "semantic_chunks.json"


def embed_chunks(
    chunked_docs: Dict[str, List[str]], model_name: str = "all-MiniLM-L6-v2"
) -> Tuple[List[str], List[str], np.ndarray]:
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


def semantic_search(
    index, query: str, model, texts: List[str], refs: List[str], k: int = 5
):
    q_vec = model.encode([query], convert_to_numpy=True)
    D, I = index.search(q_vec, k)
    results = [(refs[i], texts[i], float(D[0][rank])) for rank, i in enumerate(I[0])]
    return results


def save_index(index: faiss.IndexFlatL2, index_path: Path):
    faiss.write_index(index, str(index_path))


def save_metadata(refs: List[str], chunks: List[str], output_path: Path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"refs": refs, "chunks": chunks}, f, indent=2)


def load_index(index_path: Path) -> faiss.IndexFlatL2:
    return faiss.read_index(str(index_path))


def load_metadata(meta_path: Path) -> Tuple[List[str], List[str]]:
    with open(meta_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["refs"], data["chunks"]


def hash_sources(cfg: Dict) -> str:
    """Create a hash of file paths and modification times, and Zotero DB."""
    h = hashlib.md5()

    # Hash local folder
    input_folder = Path(cfg.get("input_folder"))
    if input_folder and input_folder.exists():
        for file in sorted(input_folder.rglob("*")):
            if file.is_file():
                h.update(str(file).encode())
                h.update(str(file.stat().st_mtime).encode())

    # Hash Zotero database
    zotero_cfg = cfg.get("zotero", {})
    if zotero_cfg.get("enabled"):
        zotero_db = Path(zotero_cfg.get("data_directory", "")) / "zotero.sqlite"
        if zotero_db.exists():
            h.update(str(zotero_db).encode())
            h.update(str(zotero_db.stat().st_mtime).encode())

    return h.hexdigest()


def process_zotero_library(library: List[Dict[str, Any]]) -> Dict[str, str]:
    """Processes Zotero library data to extract text from notes and attachments."""
    docs = {}
    for item in library:
        # 1. Add text from notes
        full_text = ""
        if item.get("notes"):
            full_text += "\n\n".join(item["notes"])

        # 2. Extract text from attachments
        if item.get("attachments"):
            for attachment in item["attachments"]:
                path = Path(attachment["path"])
                if path.exists():
                    print(f"Extracting Zotero attachment: {path.name}")
                    full_text += "\n\n" + extract_text(path)

        if full_text.strip():
            # Use a zotero:// link as the canonical reference
            zotero_id = item["zotero_id"]
            docs[f"zotero://select/items/{zotero_id}"] = full_text

    return docs


if __name__ == "__main__":
    # --- Config and Path Setup ---
    # Prefer a user‑specific config.yaml; fall back to the example if not present
    config_dir = Path(__file__).parent.parent
    config_path = config_dir / "config.yaml"
    if not config_path.exists():
        config_path = config_dir / "config.example.yaml"
    cfg = load_config(config_path)
    output_dir = Path(cfg.get("output_folder", "output"))
    os.makedirs(output_dir, exist_ok=True)

    index_path = output_dir / INDEX_FILE
    meta_path = output_dir / CHUNK_META
    hash_path = output_dir / "source_hash.txt"

    # --- Check for changes and decide whether to re-index ---
    current_hash = hash_sources(cfg)
    previous_hash = hash_path.read_text().strip() if hash_path.exists() else None

    if index_path.exists() and meta_path.exists() and current_hash == previous_hash:
        print("\n📦 Using cached FAISS index and chunk metadata...")
        index = load_index(index_path)
        refs, chunks = load_metadata(meta_path)
        model = SentenceTransformer(cfg.get("embedding_model", "all-MiniLM-L6-v2"))
    else:
        print("\n🔄 Source data has changed or no cache found. Rebuilding index...")

        # 1. Extract from local files
        docs = walk_and_extract(cfg)
        print(f"✅ Extracted {len(docs)} documents from local files.")

        # 2. Extract from Zotero
        zotero_cfg = cfg.get("zotero", {})
        if zotero_cfg.get("enabled"):
            zotero_data_dir = Path(zotero_cfg.get("data_directory")).expanduser()
            if zotero_data_dir.exists():
                zotero_library = get_zotero_data(
                    zotero_data_dir / "zotero.sqlite", zotero_data_dir / "storage"
                )
                zotero_docs = process_zotero_library(zotero_library)
                print(f"✅ Extracted {len(zotero_docs)} documents from Zotero.")
                docs.update(zotero_docs)
            else:
                print(f"⚠️ Zotero data directory not found at: {zotero_data_dir}")

        # 3. Chunk, Embed, Index
        chunked = chunk_documents(docs, cfg)
        print(f"✅ Chunked into {sum(len(v) for v in chunked.values())} total chunks.")

        refs, chunks, embeddings = embed_chunks(
            chunked, cfg.get("embedding_model", "all-MiniLM-L6-v2")
        )
        index = create_faiss_index(embeddings)

        # 4. Save artifacts
        save_index(index, index_path)
        save_metadata(refs, chunks, meta_path)
        hash_path.write_text(current_hash)
        print(f"✅ Saved new index and metadata to {output_dir}")

        model = SentenceTransformer(cfg.get("embedding_model", "all-MiniLM-L6-v2"))

    # --- Interactive Query Loop ---
    print("\n✅ Semantic index ready. Type a query to test it (or 'q' to quit):\n")
    while True:
        query = input("🧠 semantic> ").strip()
        if query.lower() in ("q", "quit", "exit"):
            break
        results = semantic_search(index, query, model, chunks, refs, k=5)
        for path, text, dist in results:
            print(
                f"📄 {path} [score: {dist:.4f}]\n{textwrap.shorten(text, width=300)}\n"
            )
