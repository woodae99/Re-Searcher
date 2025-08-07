# src/api.py

import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

from .semantic_search import (
    load_index,
    load_metadata,
    semantic_search as perform_semantic_search,
)
from .main import query_index as perform_keyword_search
from .extract_text import load_config
from sentence_transformers import SentenceTransformer
import sqlite3
from langchain.chains.summarize import load_summarize_chain
from langchain.llms import HuggingFacePipeline
from transformers import pipeline

from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# --- Environment and Rate Limiting ---
load_dotenv()
limiter = Limiter(key_func=get_remote_address)

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Re-Searcher API",
    description="API for semantic and keyword search over a local document library.",
    version="0.1.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Security ---
API_KEY = os.getenv("API_KEY")
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

async def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        raise HTTPException(
            status_code=403,
            detail="Could not validate credentials",
        )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Or specify origins: ["http://localhost:8501"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Configuration and Global Resources ---
# It's better to load models and indexes once at startup.
# In a real-world app, you might use a more robust dependency injection system.
def load_resources():
    """Load all necessary models and data indexes."""
    global cfg, model, faiss_index, refs, chunks, db_conn

    config_path = Path(__file__).parent.parent / "config.example.yaml"
    if not config_path.exists():
        raise RuntimeError(f"Configuration file not found at {config_path}")

    cfg = load_config(config_path)
    output_dir = Path(cfg.get('output_folder', 'output'))

    # Load Semantic Search resources
    faiss_index_path = output_dir / "semantic_index.faiss"
    meta_path = output_dir / "semantic_chunks.json"

    if not faiss_index_path.exists() or not meta_path.exists():
        raise RuntimeError("Index files not found. Please run the indexing script first (e.g., `python -m src.semantic_search`).")

    model = SentenceTransformer(cfg.get('embedding_model', 'all-MiniLM-L6-v2'))
    faiss_index = load_index(faiss_index_path)
    refs, chunks = load_metadata(meta_path)

    # Load Keyword Search resources
    db_path = output_dir / "search_index.sqlite"
    if not db_path.exists():
        raise RuntimeError("Keyword search database not found. Please run the indexing script.")

    # Use a single, read-only connection for the app's lifetime
    db_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # Load Summarization pipeline
    global summarizer
    summarizer = pipeline("summarization", model="facebook/bart-large-cnn")

@app.on_event("startup")
async def startup_event():
    """Load resources on app startup."""
    print("Loading API resources...")
    load_resources()
    print("Resources loaded successfully.")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on app shutdown."""
    if 'db_conn' in globals():
        db_conn.close()
    print("API resources cleaned up.")


# --- API Endpoints ---

@app.get("/api/status", summary="Get API status and configuration", dependencies=[Security(get_api_key)])
def get_status():
    """Returns the current status of the API and basic configuration info."""
    return {
        "status": "ok",
        "index_type": "FAISS (semantic) + SQLite FTS5 (keyword)",
        "embedding_model": cfg.get('embedding_model'),
        "total_chunks": len(chunks) if 'chunks' in globals() else "Not loaded",
        "config": {
            "input_folder": cfg.get('input_folder'),
            "output_folder": cfg.get('output_folder'),
            "zotero_enabled": cfg.get('zotero', {}).get('enabled'),
        }
    }

# Placeholder for the search endpoint
class SearchQuery(BaseModel):
    query: str
    mode: str = Field("hybrid", pattern="^(semantic|keyword|hybrid)$")
    top_k: int = 10
    tags: Optional[List[str]] = None
    collections: Optional[List[str]] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None

class SearchResult(BaseModel):
    path: str
    snippet: str
    score: float

@app.post("/api/search", response_model=List[SearchResult], dependencies=[Security(get_api_key)])
def search(query: SearchQuery):
    """Performs a search based on the query, mode, and top_k parameters."""

    semantic_results = []
    keyword_results = []

    # Perform semantic search
    if query.mode in ["semantic", "hybrid"]:
        raw_sem_results = perform_semantic_search(faiss_index, query.query, model, chunks, refs, k=query.top_k * 5) # Fetch more to filter

        # This is post-filtering. For large datasets, pre-filtering or filtering during search is better.
        # This requires a more advanced setup (e.g., FAISS with metadata, or a different vector DB).
        for ref, text, score in raw_sem_results:
            # We need to fetch metadata for each result to filter. This is inefficient.
            # A better approach would be to have metadata available alongside the search results.
            doc_id = ref
            if doc_id.startswith("zotero-"):
                item_id = int(doc_id.replace("zotero-", ""))
                zotero_cfg = cfg.get('zotero', {})
                zotero_data_dir = Path(zotero_cfg.get('data_directory')).expanduser()
                conn = get_db_connection(zotero_data_dir / 'zotero.sqlite')
                from .zotero import get_single_zotero_item
                item_data = get_single_zotero_item(conn, item_id, zotero_data_dir / 'storage')
                conn.close()

                if item_data:
                    # Apply filters
                    if query.tags and not any(tag in item_data.get('tags', []) for tag in query.tags):
                        continue
                    if query.collections and not any(c in item_data.get('collections', []) for c in query.collections):
                        continue

                    year_str = item_data.get('metadata', {}).get('date', '')
                    if year_str and (query.year_min or query.year_max):
                        try:
                            year = int(year_str.split('-')[0])
                            if query.year_min and year < query.year_min:
                                continue
                            if query.year_max and year > query.year_max:
                                continue
                        except (ValueError, IndexError):
                            pass # Ignore if year is not a valid format

            semantic_results.append(SearchResult(path=ref, snippet=text, score=score))

    # Perform keyword search
    if query.mode in ["keyword", "hybrid"]:
        raw_key_results = perform_keyword_search(db_conn, query.query, max_results=query.top_k * 5) # Fetch more to find matches
        # Simple combination: just append keyword results to semantic
        # A real implementation would use a more sophisticated ranking fusion (like RRF)
        for path, snippet in raw_key_results:
             # Avoid duplicates
            if not any(r.path == path for r in semantic_results):
                 keyword_results.append(SearchResult(path=path, snippet=snippet, score=0.0)) # Keyword score is not directly comparable

    if query.mode == "semantic":
        return semantic_results
    elif query.mode == "keyword":
        return keyword_results
    else: # hybrid
        # Simple combination, not ranked.
        return (semantic_results + keyword_results)[:query.top_k]


# Placeholder for the document endpoint
@app.get("/api/doc/{doc_id}", summary="Get a single document's metadata", dependencies=[Security(get_api_key)])
def get_document(doc_id: str):
    """
    Retrieves all stored metadata for a single document, identified by its path or Zotero ID.
    """
    if doc_id.startswith("zotero-"):
        try:
            item_id = int(doc_id.replace("zotero-", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Zotero item ID format.")

        zotero_cfg = cfg.get('zotero', {})
        if not zotero_cfg.get('enabled'):
            raise HTTPException(status_code=404, detail="Zotero integration is not enabled in config.")

        zotero_data_dir = Path(zotero_cfg.get('data_directory')).expanduser()
        conn = get_db_connection(zotero_data_dir / 'zotero.sqlite')
        if not conn:
            raise HTTPException(status_code=500, detail="Could not connect to Zotero database.")

        from .zotero import get_single_zotero_item
        item_data = get_single_zotero_item(conn, item_id, zotero_data_dir / 'storage')
        conn.close()

        if not item_data:
            raise HTTPException(status_code=404, detail=f"Zotero item with ID {item_id} not found.")
        return item_data
    else:
        # For file-based documents, we don't have rich metadata stored yet.
        # A future implementation could store file metadata in the SQLite DB.
        if Path(doc_id).exists():
             return {
                "id": doc_id,
                "path": doc_id,
                "metadata": {"title": Path(doc_id).name}
            }
        else:
            raise HTTPException(status_code=404, detail=f"Document with path '{doc_id}' not found.")

class SummarizeQuery(BaseModel):
    text: str

@app.post("/api/summarise", summary="Summarize a block of text", dependencies=[Security(get_api_key)])
def summarise(query: SummarizeQuery):
    """
    Summarizes the provided text using a pre-loaded model.
    """
    if not 'summarizer' in globals():
        raise HTTPException(status_code=500, detail="Summarization model not loaded.")

    summary = summarizer(query.text, max_length=150, min_length=30, do_sample=False)
    return {"summary": summary[0]['summary_text']}

# --- Main entry point for running the API with uvicorn ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
