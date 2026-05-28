# Re-Searcher: Project Context for Codex

## Overview
Re-Searcher is a semantic search system for academic research, built for Colin's PhD thesis work on coaching theory. It indexes and searches across Zotero (reference manager) and Obsidian (markdown notes) using BGE-M3 embeddings via LM Studio.

## Current State (January 2025)

### What's Working
- **Full corpus indexed**: 649,094 chunks from 8,239 Zotero items + 4,720 Obsidian notes
- **ChromaDB**: Running in Docker on port 8000, collection name: `research_library`
- **Embeddings**: BGE-M3 (1024 dimensions) via LM Studio at `http://localhost:1234/v1`
- **Query CLI**: `python scripts/query.py "search query"` works perfectly
- **Rich metadata**: Authors, titles, DOIs, backlinks, source types preserved per chunk

### The Problem We're Solving
The `chroma-mcp` server (official Chroma MCP) can connect to our ChromaDB but **cannot query** because:
- It uses a default 384-dimension embedder
- Our collection uses 1024-dimension BGE-M3 embeddings
- chroma-mcp doesn't support custom OpenAI base URLs for embedding

We tried configuring chroma-mcp with environment variables (`OPENAI_API_BASE`, etc.) but it doesn't pass them through to its embedding function.

### Solution: Build a Custom MCP Server
Create a lightweight MCP server that wraps our existing pipeline, exposing a `search_research_library` tool that Codex can call directly.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Codex.ai     │────▶│  research-mcp    │────▶│   ChromaDB      │
│   (MCP client)  │     │  (new server)    │     │   (Docker:8000) │
└─────────────────┘     └────────┬─────────┘     └─────────────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │   LM Studio      │
                        │   (BGE-M3)       │
                        │   localhost:1234 │
                        └──────────────────┘
```

## Key Files

### Configuration
- `config.yaml` - Main config (endpoints, paths, chunking params)
- `.chroma_env` - Local env vars (gitignored)

### Pipeline Components
- `src/pipeline.py` - ResearchRAGPipeline orchestrator
- `src/embedding/lmstudio.py` - LM Studio embedding client
- `src/storage/chroma.py` - ChromaVectorStore implementation
- `src/sources/zotero.py` - Zotero data extraction
- `src/sources/obsidian.py` - Obsidian vault indexing

### Scripts
- `scripts/query.py` - CLI query interface (working reference)
- `scripts/index.py` - Incremental indexing
- `index_full_corpus.py` - Full batch indexing (ran overnight)

## Task: Create MCP Server

### Requirements
1. **MCP server** using Python `mcp` package
2. **Single tool**: `search_research_library`
   - Input: query string, optional k (number of results)
   - Output: list of results with text, metadata, scores, backlinks
3. **Use existing pipeline**: Reuse `ResearchRAGPipeline` from `src/pipeline.py`
4. **Config-driven**: Read from `config.yaml`

### Suggested Implementation
```python
# src/mcp_server.py
from mcp.server import Server
from mcp.types import Tool, TextContent
from pipeline import ResearchRAGPipeline

server = Server("research-mcp")
pipeline = ResearchRAGPipeline(Path("config.yaml"))

@server.tool()
async def search_research_library(query: str, k: int = 5) -> list:
    """Search the research library using semantic search."""
    results = pipeline.query(query, k=k)
    # Format results with metadata, backlinks, scores
    return formatted_results
```

### Codex Desktop Config Entry
```json
"research-mcp": {
  "command": "python",
  "args": ["C:/Users/colin/Dev/GitHub/Re-Searcher/src/mcp_server.py"],
  "env": {
    "PYTHONPATH": "C:/Users/colin/Dev/GitHub/Re-Searcher"
  }
}
```

## Testing
After implementation:
1. Restart Codex Desktop
2. Test tool appears in available tools
3. Query: "Whitehead process philosophy coaching"
4. Verify results match `query.py` output

## Dependencies
Key packages (see requirements.txt):
- `chromadb>=0.4.0`
- `openai>=1.0.0` (for LM Studio API compatibility)
- `PyYAML>=6.0`
- `mcp` (add to requirements)

## Environment
- Windows 11
- Python 3.13
- LM Studio v4 beta with BGE-M3 model loaded
- Docker Desktop running ChromaDB container
