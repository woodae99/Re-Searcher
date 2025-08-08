# Step-by-step prototype of a minimal semantic + keyword search tool

# Step 1: Extract text from files (already done in extract_text.py)
# Step 2: Chunk text into small segments
# Step 3: Build keyword search index using SQLite FTS5

from pathlib import Path
from typing import List, Dict
import sqlite3
import yaml
import textwrap
import os

from extract_text import load_config, walk_and_extract


def chunk_text(text: str, size: int = 300, overlap: int = 50) -> List[str]:
    """Chunk text into overlapping segments by character count."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += size - overlap
    return chunks


def chunk_documents(docs: Dict[str, str], cfg: Dict) -> Dict[str, List[str]]:
    chunked = {}
    for path, text in docs.items():
        chunks = chunk_text(text, cfg['chunk_size'], cfg['overlap'])
        chunked[path] = chunks
    return chunked


def preview_chunks(chunked_docs: Dict[str, List[str]], max_files: int = 3):
    print("\n🧩 Previewing document chunks:\n")
    for i, (path, chunks) in enumerate(chunked_docs.items()):
        print(f"--- {path} ({len(chunks)} chunks) ---")
        for idx, chunk in enumerate(chunks[:2]):
            print(f"Chunk {idx+1}:\n{textwrap.shorten(chunk, width=300)}\n")
        if i >= max_files - 1:
            break


def create_search_index(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS chunks")
    cur.execute("""
        CREATE VIRTUAL TABLE chunks USING fts5(
            path UNINDEXED,
            chunk_text
        )
    """)
    conn.commit()
    return conn


def index_chunks(conn, chunked_docs: Dict[str, List[str]]):
    cur = conn.cursor()
    for path, chunks in chunked_docs.items():
        for chunk in chunks:
            cur.execute("INSERT INTO chunks (path, chunk_text) VALUES (?, ?)", (path, chunk))
    conn.commit()


def query_index(conn, query: str, max_results: int = 10):
    cur = conn.cursor()
    cur.execute("SELECT path, snippet(chunks, 1, '...', '...', '...', 64) FROM chunks WHERE chunk_text MATCH ? LIMIT ?", (query, max_results))
    return cur.fetchall()


if __name__ == "__main__":
    # Prefer a user‑specific config.yaml; fall back to the example if not present
    config_dir = Path(__file__).parent.parent
    config_path = config_dir / "config.yaml"
    if not config_path.exists():
        config_path = config_dir / "config.example.yaml"
    cfg = load_config(config_path)
    docs = walk_and_extract(cfg)
    print(f"\n✅ Extracted {len(docs)} documents.")

    chunked = chunk_documents(docs, cfg)
    print(f"✅ Chunked into {sum(len(v) for v in chunked.values())} total chunks.\n")

    preview_chunks(chunked)

    db_path = Path(cfg['output_folder']) / "search_index.sqlite"
    os.makedirs(cfg['output_folder'], exist_ok=True)
    conn = create_search_index(db_path)
    index_chunks(conn, chunked)
    print(f"✅ Indexed chunks in SQLite FTS5 at {db_path}\n")

    while True:
        q = input("🔎 Enter keyword search (or 'q' to quit): ").strip()
        if q.lower() == 'q':
            break
        results = query_index(conn, q)
        if not results:
            print("No matches found.\n")
        for path, snippet in results:
            print(f"📄 {path}\n...{snippet}...\n")
