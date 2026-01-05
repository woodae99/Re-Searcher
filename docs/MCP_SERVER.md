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

**Note**: Adjust paths for your system!

### 3. Restart Claude Desktop

After updating config, completely restart Claude Desktop for changes to take effect.

## Usage

Once configured, Claude will have access to the `search_research_library` tool:

```
You: "What does Whitehead say about process philosophy?"

Claude: [Uses search_research_library tool internally]
        Based on your research library, Whitehead discusses...
```

### Tool Parameters

- **query** (required): Search query text
- **k** (optional): Number of results (1-50, default: 5)

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

## Future Enhancements

Potential additions (easy to implement with current architecture):

- **get_collection_stats** - Return library statistics
- **list_sources** - Show available data sources
- **search_by_author** - Filter by author
- **search_by_source** - Filter by source type (Zotero/Obsidian)
- **get_backlinks** - Get related documents via backlinks

All of these would follow the same pattern:
1. Add tool definition
2. Add handler method that delegates to pipeline
3. Add formatter if needed
4. Update tests
