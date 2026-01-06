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
from typing import Any, Dict

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
from src.mcp_formatters.formatters import format_search_results, format_error_response


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
                        "Searches across 649K+ chunks from Zotero references and Obsidian notes. "
                        "Returns relevant passages with metadata (authors, titles, DOIs, backlinks)."
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
                )
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            """Handle tool execution requests."""
            if name != "search_research_library":
                raise ValueError(f"Unknown tool: {name}")

            return await self._search_research_library(arguments)

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
            # This is the key delegation - all search logic stays in pipeline
            results = self.pipeline.query(query, k=k)

            # Format results using separate formatter
            # If metadata structure changes, only formatters.py needs updates
            formatted_results = format_search_results(results)

            # Build response text
            response_parts = [
                f"Found {len(formatted_results)} results for: {query}\n"
            ]

            for result in formatted_results:
                response_parts.append(f"\n--- Result #{result['rank']} (Score: {result['score']}) ---")
                response_parts.append(f"Title: {result['title']}")
                response_parts.append(f"Authors: {result['authors']}")
                response_parts.append(f"Source: {result['source_type']}")

                if "backlink" in result:
                    response_parts.append(f"Link: {result['backlink']}")

                if "doi" in result:
                    response_parts.append(f"DOI: {result['doi']}")

                response_parts.append(f"\nText:\n{result['text']}")
                response_parts.append("")  # blank line

            response_text = "\n".join(response_parts)

            return [TextContent(type="text", text=response_text)]

        except Exception as e:
            # Format error using separate formatter
            error_info = format_error_response(e)
            error_text = (
                f"Error executing search: {error_info['error']}\n"
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
