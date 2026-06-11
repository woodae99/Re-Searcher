#!/usr/bin/env python3
"""MCP Server for Re-Searcher.

This server exposes the Re-Searcher pipeline as MCP tools that Claude can use.
It's designed as a thin wrapper around the existing pipeline to ensure:
- Changes to pipeline logic automatically propagate to MCP
- No duplication of business logic
- Easy maintenance and updates
"""

import asyncio
import logging
import subprocess
import sys
import os
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import MCP package with friendly error message if it's missing
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    import mcp.server.stdio
except ImportError as e:
    sys.stderr.write(
        "[ERROR] MCP import error: Python cannot import the 'mcp' package.\n"
        f"Python executable: {sys.executable}\n"
        "This usually means the 'mcp' package is not installed in the Python environment that launched this script.\n"
        "To fix, either:\n"
        "  - Install project requirements: `pip install -r requirements.txt`\n"
        "  - Install MCP directly: `pip install 'mcp>=1.0.0'`\n"
        "  - Use the provided `run_mcp.bat` to run the script with the correct Python installation.\n"
        "After installing, restart the MCP plugin in LM Studio.\n"
    )
    raise

from src.enumeration import build_source_chunks_payload, clamp_int
from src.pipeline import ResearchRAGPipeline
from src.mcp_formatters.formatters import (
    format_search_results,
    format_error_response,
    format_hierarchy_info,
    format_source_chunks,
    format_list_sources,
    format_index_status,
)


# Configuration
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
logging.basicConfig(
    level=os.getenv("RESEARCH_MCP_LOG_LEVEL", "INFO").upper(),
    format="[%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class MCPServerBusyError(RuntimeError):
    """Raised when the MCP server is already handling its search capacity."""


def _git_sha() -> str:
    """Best-effort short SHA of the running checkout, for deploy traceability."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


class ResearchMCPServer:
    """MCP Server wrapper for Re-Searcher pipeline."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        """
        Initialize MCP server with Re-Searcher pipeline.

        Args:
            config_path: Path to config.yaml (defaults to project root)
        """
        self.config_path = config_path
        self.server = Server("research-mcp")
        self.pipeline = None
        self._init_lock = asyncio.Lock()
        self.git_sha = _git_sha()
        logger.info(
            "research-mcp starting: git_sha=%s config=%s python=%s",
            self.git_sha,
            config_path,
            sys.executable,
        )

        runtime_config = self._load_runtime_config()
        mcp_config = runtime_config.get("mcp", {}) or {}
        self.max_concurrent_searches = max(
            1, int(mcp_config.get("max_concurrent_searches", 1))
        )
        self.search_acquire_timeout_seconds = float(
            mcp_config.get("search_acquire_timeout_seconds", 900)
        )
        self._search_semaphore = asyncio.Semaphore(self.max_concurrent_searches)

        # Register request handlers
        self._register_handlers()

    def _load_runtime_config(self) -> Dict[str, Any]:
        """Load lightweight runtime config used by the MCP wrapper itself."""
        if not self.config_path.exists():
            return {}

        with open(self.config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}

        if not isinstance(loaded, dict):
            return {}

        return loaded

    def _register_handlers(self):
        """Register MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available MCP tools."""
            return [
                Tool(
                    name="search_research_library",
                    description=(
                        "Search the research library using semantic search across Zotero references and Obsidian notes. "
                        "Returns relevant passages with metadata. "
                        "Supports hierarchical chunking (coarse/mid/fine), metadata filtering, diversity controls, and reranking. "
                        "Cold-start note: if ChromaDB has been idle or recently started, the first call can take several minutes "
                        "while the database wakes up; later calls are usually much faster."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query text. Use natural language to describe what you're looking for.",
                            },
                            "k": {
                                "type": "integer",
                                "description": (
                                    "Number of final results to return after all filtering and reranking (default: 5). "
                                    "This is your top-k output. Increase for broader exploration, decrease for focused results."
                                ),
                                "default": 5,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "k_recall": {
                                "type": "integer",
                                "description": (
                                    "How many candidates to retrieve from vector store before reranking and diversity filtering "
                                    "(default: from config, typically 50). Use higher values when applying heavy post-filters "
                                    "(e.g., k_recall=100 when filtering by specific author or year to ensure enough hits remain)."
                                ),
                                "minimum": 1,
                                "maximum": 1000,
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["fast", "strict"],
                                "description": (
                                    "Retrieval filter strategy. 'fast' performs broad vector recall and applies most metadata filters "
                                    "after retrieval, which is usually better for large corpora. 'strict' applies compatible metadata "
                                    "filters directly in Chroma before retrieval, which can be useful for exact scoped searches. "
                                    "Omit to use retrieval.mode_default from config.yaml."
                                ),
                            },
                            "chunk_level": {
                                "type": "string",
                                "enum": ["coarse", "mid", "fine"],
                                "description": (
                                    "Filter by hierarchical chunk granularity: "
                                    "'coarse' = large sections with broad context (~1500-2500 chars, good for overview/gist), "
                                    "'mid' = medium sections with balanced context (~800-1500 chars, good for general queries), "
                                    "'fine' = small focused segments like paragraphs/headings (good for precise matches but may lack context). "
                                    "Omit to search all levels (default). Recommend 'coarse' or 'mid' for better context in results."
                                ),
                            },
                            "no_rerank": {
                                "type": "boolean",
                                "description": (
                                    "Set to true to disable LLM-based reranking for this query (falls back to pure vector similarity). "
                                    "Use when you want faster results or to debug embedding quality. Default: false (reranking enabled if configured)."
                                ),
                                "default": False,
                            },
                            "no_diversity": {
                                "type": "boolean",
                                "description": (
                                    "Set to true to disable diversity/deduplication filtering (allows multiple chunks from same source). "
                                    "Use for deep dives into specific sources where you want all relevant chunks. "
                                    "Default: false (diversity enabled if configured)."
                                ),
                                "default": False,
                            },
                            "max_per_source": {
                                "type": "integer",
                                "description": (
                                    "Maximum results allowed per source document (auto-enables diversity if not already on). "
                                    "Use max_per_source=1 for broad survey across many sources, "
                                    "max_per_source=10 for deep dive into each relevant source. "
                                    "Default from config is typically 2 results per source."
                                ),
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "source_type": {
                                "type": "string",
                                "enum": ["zotero", "zotero_fulltext", "zotero_note", "zotero_annotation", "obsidian"],
                                "description": (
                                    "Restrict search to a specific source type: "
                                    "'zotero' = base Zotero item metadata, "
                                    "'zotero_fulltext' = PDF/document full text, "
                                    "'zotero_note' = Zotero standalone notes, "
                                    "'zotero_annotation' = PDF highlights and comments, "
                                    "'obsidian' = Obsidian vault markdown notes. "
                                    "Useful for focusing on specific content types."
                                ),
                            },
                            "zotero_key": {
                                "type": "string",
                                "description": (
                                    "Restrict search to a single Zotero item by its key (exact match). "
                                    "Use this for deep diving into one specific paper or book. "
                                    "Example: 'XMN6HI9Y' to search only within that item."
                                ),
                            },
                            "author": {
                                "type": "string",
                                "description": (
                                    "Filter results where author field contains this substring (case-insensitive). "
                                    "Example: 'Smith' finds 'John Smith', 'Smith et al', etc. "
                                    "Useful for finding all works by or involving a specific researcher."
                                ),
                            },
                            "title_contains": {
                                "type": "string",
                                "description": (
                                    "Filter results where title contains this substring (case-insensitive). "
                                    "Example: 'coaching' finds any title mentioning coaching. "
                                    "Useful for narrowing to specific topics or book titles."
                                ),
                            },
                            "year_min": {
                                "type": "integer",
                                "description": (
                                    "Restrict to publications from this year onwards (inclusive). "
                                    "Example: 2020 for recent research only. "
                                    "Combine with year_max for a range (e.g., year_min=2015, year_max=2020)."
                                ),
                            },
                            "year_max": {
                                "type": "integer",
                                "description": (
                                    "Restrict to publications up to this year (inclusive). "
                                    "Example: 2010 for historical research. "
                                    "Combine with year_min for a specific time period."
                                ),
                            },
                            "where": {
                                "type": "object",
                                "description": (
                                    "Advanced: raw Chroma 'where' dict for custom filtering. "
                                    "Use sparingly for edge cases not covered by explicit parameters. "
                                    "Will be merged with other filters via AND logic."
                                ),
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="get_chunk_context",
                    description=(
                        "Get the parent chunk for a given chunk ID. "
                        "Use this to expand context when a fine-grained chunk needs more surrounding text. "
                        "Fine chunks link to mid chunks, mid chunks link to coarse chunks. "
                        "Cold-start note: if ChromaDB has been idle or recently started, the first call can take several minutes."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chunk_id": {
                                "type": "string",
                                "description": "The ID of the chunk to get context for",
                            },
                            "include_parent": {
                                "type": "boolean",
                                "description": "Include the parent chunk text (default: true)",
                                "default": True,
                            },
                        },
                        "required": ["chunk_id"],
                    },
                ),
                Tool(
                    name="get_source_chunks",
                    description=(
                        "Enumerate chunks belonging to one source document without embedding or semantic ranking. "
                        "Use zotero_key for Zotero sources. Use source_path for Obsidian/local sources; this maps to the indexed "
                        "metadata field source_id (for Obsidian, values look like obsidian-<relative_path>). "
                        "Results are globally sorted by chunk_index when present, then chunk id. "
                        "This tool fetches all matching metadata for the source to make pagination stable, then fetches page text only when requested."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "zotero_key": {
                                "type": "string",
                                "description": "Exact Zotero item key. Required when enumerating Zotero sources.",
                            },
                            "source_path": {
                                "type": "string",
                                "description": "Exact indexed source_id for Obsidian/local sources, as returned by list_sources.",
                            },
                            "chunk_level": {
                                "type": "string",
                                "enum": ["coarse", "mid", "fine", "atomic"],
                                "description": "Optional chunk granularity filter.",
                            },
                            "include_text": {
                                "type": "boolean",
                                "description": "Include chunk text. Set false for ids + metadata census mode.",
                                "default": True,
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum chunks to return in this page (default 50, max 200).",
                                "default": 50,
                                "minimum": 1,
                                "maximum": 200,
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Zero-based page offset after stable sorting.",
                                "default": 0,
                                "minimum": 0,
                            },
                        },
                    },
                ),
                Tool(
                    name="list_sources",
                    description=(
                        "List distinct indexed sources and chunk counts without embedding or semantic ranking. "
                        "Zotero source identity is zotero_key; Obsidian/local source identity is source_id, which can be passed "
                        "to get_source_chunks as source_path. "
                        "Served from the source registry (SQLite), which the indexing pipeline maintains; responses are immediate. "
                        "If the registry has not been built yet (pre-registry collection), the response explains how to backfill it."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source_type": {
                                "type": "string",
                                "enum": ["zotero", "zotero_fulltext", "zotero_note", "zotero_annotation", "obsidian"],
                                "description": "Optional exact source type filter.",
                            },
                            "title_contains": {
                                "type": "string",
                                "description": "Case-insensitive post-filter on source title.",
                            },
                            "author": {
                                "type": "string",
                                "description": "Case-insensitive post-filter on source authors.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum sources to return in this page (default 100, max 500).",
                                "default": 100,
                                "minimum": 1,
                                "maximum": 500,
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Zero-based page offset after aggregation and sorting.",
                                "default": 0,
                                "minimum": 0,
                            },
                        },
                    },
                ),
                Tool(
                    name="index_status",
                    description=(
                        "Report index health: source/chunk counts in the registry, live ChromaDB chunk count, "
                        "drift between the two, last index run, and server build SHA. "
                        "Call this before systematic per-source missions to confirm the index is reachable and fresh."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            """Handle tool execution requests."""
            if name == "search_research_library":
                return await self._search_research_library(arguments)
            elif name == "get_chunk_context":
                return await self._get_chunk_context(arguments)
            elif name == "get_source_chunks":
                return await self._get_source_chunks(arguments)
            elif name == "list_sources":
                return await self._list_sources(arguments)
            elif name == "index_status":
                return await self._index_status(arguments)
            else:
                raise ValueError(f"Unknown tool: {name}")

    async def _initialize_pipeline(self):
        """
        Initialize the pipeline lazily (on first use).

        This allows the server to start quickly and fail gracefully
        if there are configuration issues.
        """
        if self.pipeline is not None:
            return

        async with self._init_lock:
            if self.pipeline is not None:
                return

            # Check config exists
            if not self.config_path.exists():
                raise FileNotFoundError(
                    f"Configuration file not found: {self.config_path}\n"
                    f"Please create config.yaml from config.example.yaml"
                )

            # Initialize pipeline - this is where all the real work happens
            # Any pipeline changes (new sources, different embeddings, etc.)
            # automatically work here without modifying MCP server code
            self.pipeline = ResearchRAGPipeline(self.config_path)

            # Verify collection has data
            stats = self.pipeline.vector_store.get_collection_stats()
            doc_count = stats.get("document_count", 0)
            if doc_count == 0:
                raise RuntimeError(
                    "Collection is empty! Run indexing first:\n"
                    "  python scripts/index.py"
                )

    async def _run_search_query(self, **query_kwargs):
        """Run a blocking pipeline query without blocking the async MCP loop."""
        await self._initialize_pipeline()

        timeout = self.search_acquire_timeout_seconds
        try:
            if timeout <= 0:
                await self._search_semaphore.acquire()
            else:
                await asyncio.wait_for(
                    self._search_semaphore.acquire(),
                    timeout=timeout,
                )
        except asyncio.TimeoutError as exc:
            raise MCPServerBusyError(
                "research-mcp is still busy with earlier queued search requests. "
                "This can happen during a cold ChromaDB start or a burst of reranked searches; "
                "retry later or send no_rerank=true for faster targeted follow-ups."
            ) from exc

        try:
            query_call = partial(self.pipeline.query, **query_kwargs)
            return await asyncio.to_thread(query_call)
        finally:
            self._search_semaphore.release()

    async def _search_research_library(
        self, arguments: Dict[str, Any]
    ) -> list[TextContent]:
        """
        Execute search_research_library tool.

        Args:
            arguments: Tool arguments with 'query' and optional 'k'

        Returns:
            List of TextContent with search results
        """
        try:
            # Ensure pipeline is initialized
            # Extract arguments
            query = arguments.get("query")
            k = arguments.get("k", 5)
            k_recall = arguments.get("k_recall")
            mode = arguments.get("mode")
            chunk_level = arguments.get("chunk_level")
            no_rerank = bool(arguments.get("no_rerank", False))
            no_diversity = bool(arguments.get("no_diversity", False))
            max_per_source = arguments.get("max_per_source")

            source_type = arguments.get("source_type")
            zotero_key = arguments.get("zotero_key")
            author = arguments.get("author")
            title_contains = arguments.get("title_contains")
            year_min = arguments.get("year_min")
            year_max = arguments.get("year_max")
            where = arguments.get("where")

            if not query:
                raise ValueError("Query parameter is required")

            # Execute search using existing pipeline
            results = await self._run_search_query(
                query_text=query,
                k=k,
                retrieval_mode=mode,
                k_recall_override=k_recall,
                chunk_level=chunk_level,
                rerank_enabled=(False if no_rerank else None),
                diversity_enabled=(False if no_diversity else None),
                diversity_max_per_key=max_per_source,
                source_type=source_type,
                zotero_key=zotero_key,
                year_min=year_min,
                year_max=year_max,
                author_contains=author,
                title_contains=title_contains,
                where=where,
            )

            # Format results using separate formatter
            formatted_results = format_search_results(results)

            # Build response text with hierarchical context
            response_parts = [
                f"Found {len(formatted_results)} results for: {query}\n"
            ]

            for result in formatted_results:
                # Header with rank and score
                response_parts.append(
                    f"\n--- Result #{result['rank']} (Score: {result['score']}) ---"
                )

                # Core metadata
                response_parts.append(f"Title: {result['title']}")
                response_parts.append(f"Authors: {result['authors']}")
                response_parts.append(f"Source: {result['source_type']}")

                # Hierarchical context (vNext)
                chunk_level = result.get("chunk_level", "unknown")
                response_parts.append(f"Chunk Level: {chunk_level}")

                if "heading_path" in result:
                    response_parts.append(f"Section: {result['heading_path']}")

                if "parent_id" in result:
                    response_parts.append(
                        f"Parent Chunk: {result['parent_id']}"
                    )
                    response_parts.append(
                        "  (Use get_chunk_context to expand)"
                    )

                # Links and references
                if "backlink" in result:
                    response_parts.append(f"Link: {result['backlink']}")

                if "doi" in result:
                    response_parts.append(f"DOI: {result['doi']}")

                if "url" in result:
                    response_parts.append(f"URL: {result['url']}")

                # Chunk ID for context expansion
                response_parts.append(f"Chunk ID: {result['id']}")

                # The actual text
                response_parts.append(f"\nText:\n{result['text']}")
                response_parts.append("")  # blank line

            response_text = "\n".join(response_parts)

            return [TextContent(type="text", text=response_text)]

        except Exception as e:
            error_info = format_error_response(e)
            error_text = (
                f"Error executing search: {error_info['error']}\n"
                f"Message: {error_info['message']}"
            )
            return [TextContent(type="text", text=error_text)]

    async def _get_chunk_context(
        self, arguments: Dict[str, Any]
    ) -> list[TextContent]:
        """
        Get context for a chunk by fetching its parent.

        Args:
            arguments: Tool arguments with 'chunk_id' and optional 'include_parent'

        Returns:
            List of TextContent with chunk context
        """
        try:
            await self._initialize_pipeline()

            chunk_id = arguments.get("chunk_id")
            include_parent = arguments.get("include_parent", True)

            if not chunk_id:
                raise ValueError("chunk_id parameter is required")

            # Get the chunk from the vector store
            collection = self.pipeline.vector_store.collection
            chunk_result = collection.get(
                ids=[chunk_id],
                include=["documents", "metadatas"]
            )

            if not chunk_result["ids"]:
                return [TextContent(
                    type="text",
                    text=f"Chunk not found: {chunk_id}"
                )]

            chunk_text = chunk_result["documents"][0]
            chunk_meta = chunk_result["metadatas"][0]

            response_parts = [
                f"=== Chunk: {chunk_id} ===\n",
                f"Level: {chunk_meta.get('chunk_level', 'unknown')}",
                f"Title: {chunk_meta.get('title', 'Untitled')}",
                f"Source: {chunk_meta.get('source_type', 'unknown')}",
            ]

            if "heading_path" in chunk_meta:
                response_parts.append(f"Section: {chunk_meta['heading_path']}")

            response_parts.append(f"\nText:\n{chunk_text}\n")

            # Fetch parent if requested and available
            if include_parent and "parent_id" in chunk_meta:
                parent_id = chunk_meta["parent_id"]
                parent_result = collection.get(
                    ids=[parent_id],
                    include=["documents", "metadatas"]
                )

                if parent_result["ids"]:
                    parent_text = parent_result["documents"][0]
                    parent_meta = parent_result["metadatas"][0]

                    response_parts.append(f"\n=== Parent Chunk: {parent_id} ===\n")
                    response_parts.append(
                        f"Level: {parent_meta.get('chunk_level', 'unknown')}"
                    )

                    # Check if parent has a grandparent
                    if "parent_id" in parent_meta:
                        response_parts.append(
                            f"Grandparent: {parent_meta['parent_id']}"
                        )
                        response_parts.append(
                            "  (Use get_chunk_context again to expand further)"
                        )

                    response_parts.append(f"\nText:\n{parent_text}")
                else:
                    response_parts.append(
                        f"\n[Parent chunk {parent_id} not found in collection]"
                    )
            elif include_parent:
                response_parts.append(
                    "\n[This chunk has no parent - it may be a coarse or top-level chunk]"
                )

            return [TextContent(type="text", text="\n".join(response_parts))]

        except Exception as e:
            error_info = format_error_response(e)
            error_text = (
                f"Error getting chunk context: {error_info['error']}\n"
                f"Message: {error_info['message']}"
            )
            return [TextContent(type="text", text=error_text)]

    async def _get_source_chunks(
        self, arguments: Dict[str, Any]
    ) -> list[TextContent]:
        """Enumerate chunks for a single source by exact metadata identity.

        Delegates to src.enumeration so the CLI surface runs identical logic.
        """
        try:
            await self._initialize_pipeline()

            payload = await asyncio.to_thread(
                partial(
                    build_source_chunks_payload,
                    self.pipeline.vector_store.collection,
                    zotero_key=arguments.get("zotero_key"),
                    source_path=arguments.get("source_path"),
                    chunk_level=arguments.get("chunk_level"),
                    include_text=bool(arguments.get("include_text", True)),
                    limit=arguments.get("limit", 50),
                    offset=arguments.get("offset", 0),
                )
            )

            return [TextContent(type="text", text=format_source_chunks(payload))]

        except Exception as e:
            error_info = format_error_response(e)
            error_text = (
                f"Error getting source chunks: {error_info['error']}\n"
                f"Message: {error_info['message']}"
            )
            return [TextContent(type="text", text=error_text)]

    async def _list_sources(
        self, arguments: Dict[str, Any]
    ) -> list[TextContent]:
        """List distinct indexed sources with chunk counts, from the source registry."""
        try:
            await self._initialize_pipeline()

            source_type = arguments.get("source_type")
            if source_type and source_type not in {
                "zotero",
                "zotero_fulltext",
                "zotero_note",
                "zotero_annotation",
                "obsidian",
            }:
                raise ValueError("Invalid source_type")

            title_contains = arguments.get("title_contains")
            author = arguments.get("author")
            limit = clamp_int(arguments.get("limit"), 100, minimum=1, maximum=500)
            offset = clamp_int(arguments.get("offset"), 0, minimum=0, maximum=10**12)

            registry = self.pipeline.registry
            ready = await asyncio.to_thread(registry.is_ready)
            if not ready:
                return [TextContent(
                    type="text",
                    text=(
                        "Source registry is empty. This collection was indexed before "
                        "the registry existed, so it needs a one-time backfill:\n"
                        "  python scripts/build_registry.py\n"
                        "The backfill is checkpointed and resumes if interrupted. "
                        "Once built, the indexing pipeline maintains the registry "
                        "automatically and list_sources responds immediately."
                    ),
                )]

            payload = await asyncio.to_thread(
                partial(
                    registry.list_sources_payload,
                    source_type=source_type,
                    title_contains=title_contains,
                    author=author,
                    limit=limit,
                    offset=offset,
                )
            )

            return [TextContent(type="text", text=format_list_sources(payload))]

        except Exception as e:
            error_info = format_error_response(e)
            error_text = (
                f"Error listing sources: {error_info['error']}\n"
                f"Message: {error_info['message']}"
            )
            return [TextContent(type="text", text=error_text)]

    async def _index_status(
        self, arguments: Dict[str, Any]
    ) -> list[TextContent]:
        """Report registry/vector-store health for mission preflight checks."""
        try:
            await self._initialize_pipeline()

            registry_status = await asyncio.to_thread(self.pipeline.registry.status)
            chroma_count = await asyncio.to_thread(
                self.pipeline.vector_store.collection.count
            )
            stats = self.pipeline.vector_store.get_collection_stats()

            payload = {
                "git_sha": self.git_sha,
                "collection_name": stats.get("collection_name", "unknown"),
                "endpoint": stats.get("endpoint", "unknown"),
                "chroma_chunk_count": chroma_count,
                "registry": registry_status,
                "drift": chroma_count - registry_status.get("chunk_count", 0),
            }

            return [TextContent(type="text", text=format_index_status(payload))]

        except Exception as e:
            error_info = format_error_response(e)
            error_text = (
                f"Error getting index status: {error_info['error']}\n"
                f"Message: {error_info['message']}"
            )
            return [TextContent(type="text", text=error_text)]

    async def run(self):
        """Run the MCP server using stdio transport."""
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


async def main():
    """Main entry point for MCP server."""
    # Allow custom config path via environment or command line
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH

    sys.stderr.write(f"Starting MCP server using Python: {sys.executable}\n")
    server = ResearchMCPServer(config_path)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
