# Re-Searcher MCP Server

## Overview

The MCP (Model Context Protocol) server exposes Re-Searcher's semantic search capabilities as tools that Claude can use directly. This allows Claude Desktop to search your research library in real-time during conversations.

## Architecture

```
Claude Desktop → MCP Server → ResearchRAGPipeline → ChromaDB + LM Studio
```

The MCP server is designed as a **thin wrapper** around the existing `ResearchRAGPipeline`. This means:

- ✅ All search logic stays in the pipeline
- ✅ Pipeline improvements automatically benefit MCP
- ✅ No code duplication
- ✅ Easy to maintain and update

## Files

### Core Components

- **`src/mcp_server.py`** - Main MCP server

  - Handles MCP protocol communication
  - Delegates all search logic to pipeline
  - Lazy initialization for fast startup

- **`src/mcp/formatters.py`** - Result formatters
  - Converts pipeline results to MCP format
  - Isolated formatting logic for easy updates
  - Resilient to metadata schema changes

### Tests

- **`tests/test_mcp_formatters.py`** - Formatter unit tests

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This includes the `mcp>=1.0.0` package.

### 2. Configure Claude Desktop

Add to your Claude Desktop config file:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "research-mcp": {
      "command": "python",
      "args": ["C:/Users/colin/Dev/GitHub/Re-Searcher/src/mcp_server.py"],
      "env": {
        "PYTHONPATH": "C:/Users/colin/Dev/GitHub/Re-Searcher"
      }
    }
  }
}
```

Or, to ensure LM Studio uses the same Python where `mcp` is installed, point the server to the provided `run_mcp.bat` which uses an explicit Python executable:

```json
{
  "mcpServers": {
    "research-mcp": {
      "command": "C:/Users/colin/Dev/GitHub/Re-Searcher/run_mcp.bat",
      "args": [],
      "env": {
        "PYTHONPATH": "C:/Users/colin/Dev/GitHub/Re-Searcher"
      }
    }
  }
}
```

**Note**: Adjust paths for your system!

### 3. Restart Claude Desktop

After updating config, completely restart Claude Desktop for changes to take effect.

## Usage

Once configured, Claude will have access to the research library tools:

```
You: "What does Whitehead say about process philosophy?"

Claude: [Uses search_research_library tool internally]
        Based on your research library, Whitehead discusses...

You: "Can you expand on that second result?"

Claude: [Uses get_chunk_context to fetch parent chunk]
        Here's the broader context from the same section...
```

## Available Tools

### search_research_library

Search the research library using semantic search.

**Parameters:**
- **query** (required): Search query text
- **k** (optional): Number of results (1-50, default: 5)

**Returns:** Results with hierarchical metadata including:
- Chunk level (coarse/mid/fine)
- Parent chunk ID (for context expansion)
- Section headings (for Obsidian notes)
- Standard metadata (title, authors, DOI, backlinks)

### get_chunk_context

Fetch a chunk and its parent for expanded context. Use this when a fine-grained search result needs more surrounding text.

**Parameters:**
- **chunk_id** (required): The ID of the chunk (from search results)
- **include_parent** (optional): Include parent chunk text (default: true)

**Returns:** The chunk text plus its parent chunk, enabling hierarchical context expansion:
- Fine chunks → Mid chunks → Coarse chunks

**Example workflow:**
1. Search returns a fine-grained result
2. Call `get_chunk_context` with the chunk_id
3. Get the parent (mid-level) chunk for more context
4. If needed, call again with parent's parent_id for coarse-level context

## Maintenance

### When Pipeline Changes

The MCP server is designed to handle pipeline changes gracefully:

**✅ Automatically handled:**

- New data sources added
- Embedding model changes
- ChromaDB configuration updates
- New metadata fields added

**⚙️ May need updates:**

- If result format from `pipeline.query()` changes fundamentally
  - Update `src/mcp/formatters.py`
  - Run tests: `python tests/test_mcp_formatters.py`

### Adding New Tools

To add a new MCP tool:

1. Add tool definition in `_register_handlers()` → `list_tools()`
2. Add handler method (e.g., `_get_collection_stats()`)
3. Create formatter in `src/mcp/formatters.py` if needed
4. Update tests

Example:

```python
# In mcp_server.py
@self.server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ... existing tools ...
        Tool(
            name="get_collection_stats",
            description="Get statistics about the research library",
            inputSchema={"type": "object", "properties": {}},
        )
    ]

async def _get_collection_stats(self, arguments: Dict) -> list[TextContent]:
    stats = self.pipeline.vector_store.get_collection_stats()
    formatted = format_collection_stats(stats)
    return [TextContent(type="text", text=str(formatted))]
```

### Configuration

The server uses `config.yaml` from the project root by default. You can specify a custom config:

```bash
python src/mcp_server.py /path/to/custom/config.yaml
```

Or in Claude Desktop config:

```json
{
  "args": ["src/mcp_server.py", "/path/to/custom/config.yaml"]
}
```

## Troubleshooting

### Server won't start

1. **Check config exists**: `config.yaml` must exist in project root
2. **Check dependencies**: `pip install -r requirements.txt`

   - If you see an error like `ModuleNotFoundError: No module named 'mcp.server'` when starting the MCP plugin in LM Studio, it usually means LM Studio launched the script using a Python environment that does not have the `mcp` package installed.
   - Diagnose by running `python check_mcp.py` from the project root (it prints Python executable and whether `mcp.server` can be imported).
   - To fix, either install the requirements into the same Python (e.g., `pip install -r requirements.txt`) or launch the MCP script with the explicit Python that has `mcp` installed (Windows: `run_mcp.bat`).

3. **Check ChromaDB**: Ensure Docker container is running on port 8000
4. **Check LM Studio**: Ensure it's running with BGE-M3 loaded on port 1234

### No results returned

1. **Check collection**: Run `python scripts/query.py "test query"` to verify data exists
2. **Re-index if needed**: `python scripts/index.py`

### Results don't match query.py

This shouldn't happen since both use the same pipeline. If it does:

1. Check you're using the same `config.yaml`
2. Verify ChromaDB collection name matches
3. Check for pipeline initialization errors in logs

## Design Principles

The MCP server follows these principles for maintainability:

1. **Separation of Concerns**

   - MCP protocol ↔ Business logic separated
   - Formatting logic isolated in formatters.py

2. **Delegation Over Duplication**

   - All search logic delegated to pipeline
   - No reimplementation of existing functionality

3. **Resilient to Change**

   - Uses `.get()` for optional metadata fields
   - Graceful error handling
   - Lazy initialization

4. **Config-Driven**

   - No hardcoded endpoints or paths
   - Reads from same config.yaml as rest of project

5. **Easy to Test**
   - Formatters tested independently
   - Mock-friendly design

## Hierarchical Chunking (vNext)

The MCP server supports the vNext hierarchical chunking strategy:

### Chunk Levels

| Level | Description | Parent |
|-------|-------------|--------|
| coarse | Large document sections | None |
| mid | Medium paragraphs | coarse |
| fine | Small, precise passages | mid |
| atomic | Single annotations | None |

### Context Expansion

When Claude finds a relevant fine-grained chunk, it can use `get_chunk_context` to "zoom out":

```
fine chunk (precise match)
    ↓ get_chunk_context
mid chunk (paragraph context)
    ↓ get_chunk_context (with parent's parent_id)
coarse chunk (section context)
```

This allows Claude to start with precise matches and expand context as needed.

## Future Enhancements

Potential additions (easy to implement with current architecture):

- **get_collection_stats** - Return library statistics
- **list_sources** - Show available data sources
- **search_by_author** - Filter by author
- **search_by_source** - Filter by source type (Zotero/Obsidian)
- **get_siblings** - Get chunks at the same level from the same document

All of these would follow the same pattern:

1. Add tool definition
2. Add handler method that delegates to pipeline
3. Add formatter if needed
4. Update tests
