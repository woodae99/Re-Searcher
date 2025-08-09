# ui/semantic_app.py

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import streamlit as st
from dotenv import load_dotenv

# --- Configuration and API Key ---
load_dotenv()
API_URL = "http://127.0.0.1:8000"
API_KEY = os.getenv("API_KEY")
HEADERS = {"X-API-Key": API_KEY}


# --- Utility Functions ---
def clean(text: str) -> str:
    """Collapse excess whitespace & line-breaks for a tidy snippet."""
    return " ".join(text.replace("\n", " ").split())


def highlight(text: str, terms: list[str]) -> str:
    """Return *text* with every TERM wrapped in <mark>."""
    if not terms:
        return text
    escaped = [re.escape(t) for t in terms if t]
    pattern = re.compile("(" + "|".join(escaped) + ")", re.IGNORECASE)
    return pattern.sub(r"<mark>\1</mark>", text)


# --- API Communication ---
@st.cache_data(show_spinner="Searching...")
def search_api(query: str, mode: str, top_k: int, filters: Dict[str, Any]):
    """Calls the backend search API with filters."""
    payload = {"query": query, "mode": mode.lower(), "top_k": top_k, **filters}
    try:
        response = requests.post(f"{API_URL}/api/search", json=payload, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        return None


@st.cache_data(show_spinner="Loading filter options...")
def get_filter_options():
    """Fetches all Zotero items to populate filter options."""
    # This is a temporary solution. A dedicated endpoint would be better.
    # We are fetching all documents to extract all possible tags and collections.
    try:
        # This is a hack. The search endpoint should ideally provide filter options.
        # We search for a common term to get all documents.
        response = requests.post(
            f"{API_URL}/api/search",
            json={"query": "e", "mode": "keyword", "top_k": 1000},
            headers=HEADERS,
        )
        response.raise_for_status()
        items = response.json()

        all_tags = set()
        all_collections = set()
        years = []

        for item in items:
            if item["path"].startswith("zotero-"):
                doc_id = item["path"]
                doc_response = requests.get(
                    f"{API_URL}/api/doc/{doc_id}", headers=HEADERS
                )
                if doc_response.ok:
                    doc_data = doc_response.json()
                    all_tags.update(doc_data.get("tags", []))
                    all_collections.update(doc_data.get("collections", []))
                    year_str = doc_data.get("metadata", {}).get("date", "")
                    if year_str:
                        try:
                            years.append(int(year_str.split("-")[0]))
                        except (ValueError, IndexError):
                            pass

        min_year = min(years) if years else 2000
        max_year = max(years) if years else 2024
        return sorted(list(all_tags)), sorted(list(all_collections)), min_year, max_year

    except requests.exceptions.RequestException:
        return [], [], 2000, 2024


# --- Streamlit App ---
st.set_page_config(page_title="Re-Searcher", layout="wide")
st.title("Re-Searcher")

# --- Sidebar for Filters ---
with st.sidebar:
    st.header("Filters")
    tags, collections, min_year, max_year = get_filter_options()

    selected_tags = st.multiselect("Tags", tags)
    selected_collections = st.multiselect("Collections", collections)
    selected_year_range = st.slider(
        "Year Range", min_year, max_year, (min_year, max_year)
    )

    filters = {
        "tags": selected_tags,
        "collections": selected_collections,
        "year_min": selected_year_range[0],
        "year_max": selected_year_range[1],
    }

# --- Main Search UI ---
main_tab, advanced_tab = st.tabs(["Search", "Advanced Query Builder"])

with main_tab:
    st.header("Semantic and Keyword Search")

    mode = st.radio("Search mode", ["Hybrid", "Semantic", "Keyword"], index=0)
    query = st.text_input("Enter your search query", key="main_query")
    k = st.slider("Number of results", 1, 50, 10, key="main_k")

    if query:
        results = search_api(query, mode, k, filters)

        if results:
            st.subheader(f"Found {len(results)} results:")
            for i, res in enumerate(results):
                path = res.get("path", "Unknown Path")
                snippet = res.get("snippet", "No snippet available.")
                score = res.get("score", 0.0)

                fname = Path(path).name
                st.markdown(f"**{i+1}. {fname}**")

                if mode in ["Semantic", "Hybrid"] and score > 0:
                    st.caption(f"Similarity Score: {score:.4f}")

                highlighted_snippet = highlight(clean(snippet), query.split())
                st.markdown(f"> {highlighted_snippet}...", unsafe_allow_html=True)

                with st.expander("View Full Text"):
                    doc_id = res.get("path")
                    if doc_id:
                        doc_response = requests.get(
                            f"{API_URL}/api/doc/{doc_id}", headers=HEADERS
                        )
                        if doc_response.ok:
                            doc_data = doc_response.json()
                            full_text = ""
                            if (
                                "metadata" in doc_data
                                and "fulltext" in doc_data["metadata"]
                            ):
                                full_text = doc_data["metadata"]["fulltext"]
                            elif "notes" in doc_data:
                                full_text += "\n\n".join(doc_data["notes"])

                            if full_text:
                                st.text(full_text)
                            else:
                                st.warning("Full text not available for this item.")
                        else:
                            st.error("Could not fetch full document.")

                st.divider()
        else:
            st.warning("No results found.")

with advanced_tab:
    st.header("Advanced Keyword Search (SQLite FTS5)")
    st.info('Example: `(semantic OR vector) AND NOT "exact phrase"`')

    advanced_query = st.text_input("Enter advanced keyword query", key="advanced_query")

    if advanced_query:
        adv_results = search_api(advanced_query, "keyword", 20, filters)

        if adv_results:
            st.subheader(f"Found {len(adv_results)} results:")
            for i, res in enumerate(adv_results):
                path = res.get("path", "Unknown Path")
                snippet = res.get("snippet", "No snippet available.")

                fname = Path(path).name
                st.markdown(f"**{i+1}. {fname}**")

                terms = re.findall(r"[\\w\"]+", advanced_query)
                highlighted_snippet = highlight(clean(snippet), terms)
                st.markdown(f"> ...{highlighted_snippet}...", unsafe_allow_html=True)

                with st.expander("View Full Text"):
                    doc_id = res.get("path")
                    if doc_id:
                        doc_response = requests.get(
                            f"{API_URL}/api/doc/{doc_id}", headers=HEADERS
                        )
                        if doc_response.ok:
                            doc_data = doc_response.json()
                            full_text = ""
                            if (
                                "metadata" in doc_data
                                and "fulltext" in doc_data["metadata"]
                            ):
                                full_text = doc_data["metadata"]["fulltext"]
                            elif "notes" in doc_data:
                                full_text += "\n\n".join(doc_data["notes"])

                            if full_text:
                                st.text(full_text)
                            else:
                                st.warning("Full text not available for this item.")
                        else:
                            st.error("Could not fetch full document.")
                st.divider()
        else:
            st.warning("No results found for the advanced query.")
