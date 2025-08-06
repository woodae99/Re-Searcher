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
    """Collapse excess whitespace & line‑breaks for a tidy snippet."""
    return " ".join(text.replace("\n", " ").split())


def highlight(text: str, terms: list[str]) -> str:
    """Return *text* with every TERM wrapped in <mark>."""
    if not terms:
        return text
    escaped = [re.escape(t) for t in terms if t]
    pattern = re.compile("(" + "|".join(escaped) + ")", re.IGNORECASE)
    return pattern.sub(r"<mark>\1</mark>", text)


def group_results(flat_results):
    """Group (ref, snippet) → {filename: [snippets…]}"""
    grouped: dict[str, list[str]] = {}
    for ref, snippet in flat_results:
        fname = Path(ref).name
        grouped.setdefault(fname, []).append(snippet)
    return grouped


@st.cache_resource(show_spinner="Loading semantic resources…")
def load_all():
    index = load_index(INDEX_FILE)
    refs, chunks = load_metadata(CHUNK_META)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    return index, refs, chunks, model

# -----------------------------------------------------------------------------
# STREAMLIT APP
# -----------------------------------------------------------------------------

st.set_page_config(page_title="Semantic Search", layout="wide")
st.title("🔍 Semantic Search Interface")

index, refs, chunks, model = load_all()

main_tab, advanced_tab = st.tabs(["Search", "Advanced Query Builder"])

with main_tab:
    # ---------------------- controls -----------------------------------------
    mode = st.radio(
        "Search mode",
        ["Semantic", "Keyword", "Hybrid"],
        index=0,
        help="Choose between semantic similarity, exact‑match keyword search, or both.",
    )
    query = st.text_input("Enter your search query")
    k = st.slider("Number of results", 1, 20, 5)
    score_threshold = st.slider("Minimum similarity (lower is closer)", 0.0, 1.0, 1.0, 0.01)

    if query:
        terms = query.split()

        # ---------------------------- Keyword ---------------------------------
        if mode in ("Keyword", "Hybrid"):
            with sqlite3.connect(DB_FILE) as conn:
                kw_raw = query_index(conn, query, max_results=k * 20)
            kw_grouped = group_results(kw_raw)

        # --------------------------- Semantic ---------------------------------
        if mode in ("Semantic", "Hybrid"):
            sem_results = semantic_search(
                index, query, model, chunks, refs, k=k
            )
            # apply distance threshold (lower is closer)
            sem_results = [r for r in sem_results if r[2] <= score_threshold]

        # ----------------------------- Render ---------------------------------
        if mode == "Keyword":
            st.subheader("🔍 Keyword Results")
            for fname, snippets in kw_grouped.items():
                st.markdown(f"### {fname}")
                for snip in snippets[:3]:
                    st.markdown(
                        f"- …{highlight(clean(snip), terms)}…", unsafe_allow_html=True
                    )
                if len(snippets) > 3:
                    with st.expander("More…"):
                        for snip in snippets[3:]:
                            st.markdown(
                                f"- …{highlight(clean(snip), terms)}…",
                                unsafe_allow_html=True,
                            )
                st.divider()

        elif mode == "Semantic":
            st.subheader("🧠 Semantic Results")
            for i, (ref, text, score) in enumerate(sem_results, 1):
                fname = Path(ref).name
                snippet = highlight(clean(text)[:200], terms)
                st.markdown(f"**{i}. {fname}** — {snippet}…", unsafe_allow_html=True)
                st.caption(f"Score: {score:.4f}")

        else:  # Hybrid view
            left, right = st.columns(2)

            with left:
                st.subheader("🧠 Semantic")
                for i, (ref, text, score) in enumerate(sem_results, 1):
                    fname = Path(ref).name
                    snippet = highlight(clean(text)[:120], terms)
                    st.markdown(f"**{i}. {fname}** — {snippet}…", unsafe_allow_html=True)
                    st.caption(f"Score: {score:.4f}")

            with right:
                st.subheader("🔍 Keyword")
                for fname, snippets in kw_grouped.items():
                    st.markdown(f"**{fname}**")
                    for snip in snippets[:3]:
                        st.markdown(
                            f"- …{highlight(clean(snip), terms)}…",
                            unsafe_allow_html=True,
                        )
                    if len(snippets) > 3:
                        with st.expander("More…"):
                            for snip in snippets[3:]:
                                st.markdown(
                                    f"- …{highlight(clean(snip), terms)}…",
                                    unsafe_allow_html=True,
                                )

# -----------------------------------------------------------------------------
# ADVANCED TAB (placeholder)
# -----------------------------------------------------------------------------

with advanced_tab:
    st.subheader("🧠 Advanced Query Builder")
    st.info("This section will eventually support:")
    st.markdown(
        """
        - Custom concept linking and ontology-aware search
        - AND / OR / NEAR operators
        - Toggle between strict term match and semantic expansion
        - Pre‑defined query templates based on research interests
        """
    )
    st.caption("(Coming soon. If you have preferences for how you'd like to build these queries, jot them down here.)")
