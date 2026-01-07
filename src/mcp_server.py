#!/usr/bin/env python3
"""MCP Server for Re-Searcher.

This server exposes the Re-Searcher pipeline as MCP tools that Claude can use.
It's designed as a thin wrapper around the existing pipeline to ensure:
- Changes to pipeline logic automatically propagate to MCP
- No duplication of business logic
- Easy maintenance and updates
"""

import asyncio
import sys
import os
from pathlib import Path
from typing import Any, Dict, List

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

from src.pipeline import ResearchRAGPipeline
from src.mcp_formatters.formatters import (
    format_search_results,
    format_error_response,
    format_hierarchy_info,
)


# Configuration
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


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

        # Register request handlers
        self._register_handlers()

    def _register_handlers(self):
        """Register MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available MCP tools."""
            return [
                Tool(
                    name="search_research_library",
                    description=(
                        "Search the research library using semantic search. "
                        "Searches across indexed chunks from Zotero references and Obsidian notes. "
                        "Returns relevant passages with metadata including hierarchical context "
                        "(chunk level, parent references, section headings)."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query text",
                            },
                            "k": {
                                "type": "integer",
                                "description": "Number of results to return (default: 5)",
                                "default": 5,
                                "minimum": 1,
                                "maximum": 50,
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
                        "Fine chunks link to mid chunks, mid chunks link to coarse chunks."
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
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            """Handle tool execution requests."""
            if name == "search_research_library":
                return await self._search_research_library(arguments)
            elif name == "get_chunk_context":
                return await self._get_chunk_context(arguments)
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
            await self._initialize_pipeline()

            # Extract arguments
            query = arguments.get("query")
            k = arguments.get("k", 5)

            if not query:
                raise ValueError("Query parameter is required")

            # Execute search using existing pipeline
            results = self.pipeline.query(query, k=k)

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
