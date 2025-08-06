import yaml
import os
import re
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import sqlite3
from sentence_transformers import SentenceTransformer
from semantic_search import load_index, load_metadata, semantic_search
from main import query_index

# -----------------------------------------------------------------------------
# CONFIG & PATHS
# -----------------------------------------------------------------------------
with open("config.yaml", "r") as fh:
    cfg = yaml.safe_load(fh)
INPUT_FOLDER = Path(cfg["input_folder"])
OUTPUT_FOLDER = Path(cfg["output_folder"])
INDEX_FILE = OUTPUT_FOLDER / "semantic_index.faiss"
DB_FILE = OUTPUT_FOLDER / "search_index.sqlite"
CHUNK_META = OUTPUT_FOLDER / "semantic_chunks.json"

# -----------------------------------------------------------------------------
# UTILS
# -----------------------------------------------------------------------------
def clean(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())

def highlight(text: str, terms: list[str]) -> str:
    if not terms:
        return text
    escaped = [re.escape(t) for t in terms if t]
    pattern = re.compile("(" + "|".join(escaped) + ")", re.IGNORECASE)
    return pattern.sub(r"<mark>\1</mark>", text)

def group_results(flat_results):
    grouped: dict[str, list[str]] = {}
    for ref, snippet in flat_results:
        fname = Path(ref).name
        grouped.setdefault(fname, []).append(snippet)
    return grouped

# -----------------------------------------------------------------------------
# LOAD
# -----------------------------------------------------------------------------
@st.cache_resource
def load_all():
    index = load_index(INDEX_FILE)
    refs, chunks = load_metadata(CHUNK_META)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    return index, refs, chunks, model

index, refs, chunks, model = load_all()
st.set_page_config(page_title="Semantic Search Advanced", layout="wide")

# -----------------------------------------------------------------------------
# APP
# -----------------------------------------------------------------------------
st.title("Advanced Search - Boolean & Fuzzy")

query = st.text_input("Enter boolean query (+ must, - must not, quotes for exact) or fuzzy~").strip()

if query:
    # Use the raw SQLite connection for advanced queries
    conn = sqlite3.connect(DB_FILE)
    try:
        # Fancy FTS5 SQL:
        # +term -> AND
        # term without + -> OR
        # "phrase" -> NEAR
        # -term -> NOT
        # term~ -> fuzzy (edit distance) using Unicode61 tokenizer settings
        sql = (
    "SELECT ref, snippet(chunks, 1, '<b>', '</b>', '...', 64) AS snip "
    "FROM chunks WHERE chunk_text MATCH ? LIMIT 50;"
)

        matched = conn.execute(sql, (query,)).fetchall()
    finally:
        conn.close()

    # Display grouped
    grouped = group_results(matched)
    for fname, snippets in grouped.items():
        st.markdown(f"## {fname}")
        for snip in snippets[:3]:
            snip_cln = clean(snip)
            terms = [t.strip('+-"~') for t in re.findall(r'[+\-]?"[^"]+"|[+\-]?\w+~?', query)]
            snip_high = highlight(snip_cln, terms)
            st.markdown(f"- {snip_high}", unsafe_allow_html=True)
        if len(snippets) > 3:
            with st.expander("More…"):
                for snip in snippets[3:]:
                    snip_cln = clean(snip)
                    snip_high = highlight(snip_cln, terms)
                    st.markdown(f"- {snip_high}", unsafe_allow_html=True)
