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

## HTTP (Streamable) Server

To expose the MCP server over HTTP (for remote clients), use the streamable HTTP
entrypoint. This runs the same MCP tools but over an HTTP endpoint suitable for
MCP clients that use a URL.

Start the server:

```bash
python src/mcp_http_server.py
```

Optional environment variables:
- `MCP_HTTP_HOST` (default: 127.0.0.1)
- `MCP_HTTP_PORT` (default: 8001)
- `MCP_CONFIG_PATH` (default: config.yaml)

### LAN Access (Windows)

If you want to access the server from another device on your LAN, you must bind
to all interfaces and allow the port through the firewall.

PowerShell (run from repo root):

```powershell
$env:MCP_HTTP_HOST="0.0.0.0"
$env:MCP_HTTP_PORT="8001"
python -m src.mcp_http_server
```

One-line PowerShell command (run from repo root):

```powershell
$env:MCP_HTTP_HOST="0.0.0.0"; $env:MCP_HTTP_PORT="8001"; python -m src.mcp_http_server
```

Or use the helper batch file (run from repo root):

```
run_mcp_http_lan.bat
```

Or use the helper PowerShell script (run from repo root):

```powershell
.\run_mcp_http_lan.ps1
```

Test from another device on the LAN:

```
curl http://<server-ip>:8001/healthz
```

If this times out, ensure Windows Firewall allows inbound TCP on port 8001.

Windows Firewall (GUI steps):
1. Open "Windows Defender Firewall with Advanced Security".
2. Click "Inbound Rules" -> "New Rule..."
3. Select "Port" -> Next.
4. Choose "TCP" and enter "8001" -> Next.
5. Select "Allow the connection" -> Next.
6. Check "Private" (and "Domain" if needed) -> Next.
7. Name it e.g. "MCP HTTP Server 8001" -> Finish.

Example MCP URL (for clients that accept a URL):

```
http://<host>:8001/mcp
```

Health check:

```
http://<host>:8001/healthz
```

Note: This HTTP endpoint has no auth by default. If you expose it publicly,
put it behind a firewall or reverse proxy and only open it when needed.

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

**Basic Parameters:**
- **query** (required): Search query text
- **k** (optional): Number of final results (1-50, default: 5)
- **mode** (optional): Retrieval filter strategy - `"fast"` or `"strict"`
  - `fast`: Broad vector recall followed by post-filtering (usually better for large corpora)
  - `strict`: Apply compatible metadata filters in Chroma before retrieval (useful for exact scoped searches)
- **chunk_level** (optional): Filter by chunk level. v0.6 production uses `"mid"` for text/markdown and `"atomic"` for Zotero annotations; `"coarse"`/`"fine"` are legacy/experimental.

**Retrieval Controls:**
- **k_recall** (optional): How many candidates to retrieve before filtering (default: from config, typically 50)
- **no_rerank** (optional): Set `true` to disable configured cross-encoder reranking (faster, uses pure vector similarity)
- **no_diversity** (optional): Set `true` to disable deduplication (allows many chunks from same source)
- **max_per_source** (optional): Max results per source document (auto-enables diversity, e.g., 1 for broad scan, 10 for deep dive)

**Metadata Filters:**
- **source_type** (optional): Filter by type - `"zotero"`, `"zotero_fulltext"`, `"zotero_note"`, `"zotero_annotation"`, or `"obsidian"`
- **zotero_key** (optional): Filter to specific Zotero item (exact key match)
- **author** (optional): Filter by author name (case-insensitive substring match)
- **title_contains** (optional): Filter by title (case-insensitive substring match)
- **year_min** (optional): Minimum publication year (inclusive)
- **year_max** (optional): Maximum publication year (inclusive)

**Returns:** Results with chunk metadata including:
- Chunk level (`mid` for text/markdown, `atomic` for Zotero annotations in v0.6)
- Section headings (for Obsidian notes)
- Standard metadata (title, authors, DOI, backlinks)

**Usage Examples:**
```json
// Broad overview across sources: prefer survey_research_sources
{"query": "dialectic in coaching", "k": 10}

// Deep dive into specific author
{"query": "process philosophy", "k": 10, "author": "Whitehead", "chunk_level": "mid", "max_per_source": 5}

// Recent research only
{"query": "coaching psychology", "year_min": 2020, "chunk_level": "mid"}

// Exact scoped metadata filtering in Chroma
{"query": "coaching psychology", "mode": "strict", "source_type": "zotero_fulltext", "year_min": 2020}
```

### survey_research_sources

Run a broad v0.6 survey by searching `mid` chunks, grouping hits by source via
the registry, and returning source rows with hit counts, best score, selection
metadata, and representative chunk IDs/snippets. Use this instead of legacy
coarse chunk search for candidate discovery.

**Example:**
```json
{"query": "dialectic in coaching", "k": 10, "k_recall": 100, "item_type": "book"}
```

### get_chunk_context

Fetch a chunk by ID. In legacy hierarchical indexes, this can also fetch a parent chunk.
In v0.6 single-grain indexes, use `get_source_chunks` to enumerate neighbouring chunks
from the same source in `chunk_index` order.

**Parameters:**
- **chunk_id** (required): The ID of the chunk (from search results)
- **include_parent** (optional): Include parent chunk text for legacy hierarchical chunks (default: true)

**Returns:** The chunk text plus parent context when legacy `parent_id` metadata exists.

**Example workflow:**
1. Search returns a relevant result
2. Call `get_source_chunks` with the source identity from the hit
3. Read nearby `mid` chunks in `chunk_index` order for context

### get_source_chunks

Enumerate chunks for one source document using plain Chroma metadata reads. This
does not embed the query and does not apply similarity ranking, so it is the tool
to use when a mission needs full source coverage or an absence claim.

**Source identity fields:**
- Zotero sources use `zotero_key`.
- Obsidian/local sources use the indexed `source_id`; pass that value as
  `source_path`. For Obsidian notes, values typically look like
  `obsidian-<relative_path>`.

**Parameters:**
- **zotero_key** or **source_path**: exactly one is required
- **chunk_level** (optional): `coarse`, `mid`, `fine`, or `atomic`
- **include_text** (optional): default `true`; use `false` for ids + metadata only
- **limit** (optional): default `50`, max `200`
- **offset** (optional): default `0`

**Ordering and cost:** The server fetches all matching metadata for the requested
source, sorts globally by `chunk_index` when present and chunk id as a tie-break,
then applies pagination. If no `chunk_index` exists, it sorts by chunk id and says
so in the response.

### list_sources

Return the source register with per-level chunk counts. Rows from this tool can
be fed directly into `get_source_chunks`.

The register is served from the **source registry**, a SQLite database
(`output/registry.<collection>.sqlite`) that the indexing pipeline maintains in
the same code paths that write to ChromaDB. Responses are immediate; there is no
cold scan, background cache build, or retry loop.

**Source identity fields:**
- Zotero rows report `identity_field=zotero_key`.
- Obsidian/local rows report `identity_field=source_id`; use the row's
  `identity_value` as `source_path` in `get_source_chunks`.

**Parameters:**
- **source_type** (optional): `zotero`, `zotero_fulltext`, `zotero_note`,
  `zotero_annotation`, or `obsidian`. Matches sources that have *any* chunks of
  that type (a Zotero item with both notes and fulltext matches both filters).
- **title_contains** (optional): case-insensitive title filter
- **author** (optional): case-insensitive authors filter
- **collection** (optional): case-insensitive substring match on Zotero collection
  names — scopes a register to one Zotero collection (e.g. `"Process"` for a
  systematic-review register). Only Zotero sources carry collections.
- **limit** (optional): default `100`, max `500`
- **offset** (optional): default `0`

**One-time backfill:** Collections indexed before the registry existed need a
single backfill scan:

```bash
python scripts/build_registry.py
```

The scan checkpoints its offset with every committed batch, so it can be
interrupted (Ctrl-C, reboot) and re-run to resume. It finishes with an integrity
audit (`output/registry_audit.json`) that diffs the index against the Zotero
database and Obsidian vault. After the backfill, routine indexing keeps the
registry in sync automatically.

### index_status

Report index health in one call: registry source/chunk counts, live ChromaDB
chunk count, drift between the two, backfill/refresh timestamps, and the
server's git SHA. Takes no parameters.

Use it as a preflight check before systematic per-source missions: if drift is
non-zero or the last index run is older than expected, fix the index before
trusting enumeration results.

## CLI parity

Every tool has a CLI equivalent with identical logic and output. Enumeration tools
live in `scripts/sources.py`; search and survey live in `scripts/query.py`:

```bash
python scripts/sources.py list --source-type zotero_fulltext --title-contains coaching
python scripts/sources.py chunks --zotero-key XMN6HI9Y --chunk-level mid --no-text
python scripts/sources.py status
python scripts/query.py "how is process used in coaching" --zotero-key XMN6HI9Y --no-rerank --json
python scripts/query.py "process becoming flux" --survey --max-per-source 1 --json
```

Add `--json` to any `sources.py` subcommand or to `query.py` for machine-readable
output. The CLI `--json` payloads are built from the same `src/mcp_formatters`
the MCP tools use, so they match the corresponding tool output by construction
(`search_research_library` ↔ `query.py --json`, `survey_research_sources` ↔
`query.py --survey --json`).

**stdout is the data channel.** With `--json`, stdout carries only JSON — all
status/diagnostic lines (`[OK]` connect/source banners, `Query:`/`[TIMING]`) go to
stderr. Parse stdout directly; no banner-slicing needed.

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
   - Diagnose with `python -c "import mcp.server; import sys; print(sys.executable)"` from the project root.
   - To fix, either install the requirements into the same Python (e.g., `pip install -r requirements.txt`) or launch the MCP script with the explicit Python that has `mcp` installed (Windows: `run_mcp.bat`).

3. **Check ChromaDB**: Ensure the native ChromaDB server (python process) is listening on port 8000
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

## Hierarchical Chunking

The MCP server supports the hierarchical chunking strategy:

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
- **search_by_author** - Filter by author
- **search_by_source** - Filter by source type (Zotero/Obsidian)
- **get_siblings** - Get chunks at the same level from the same document

All of these would follow the same pattern:

1. Add tool definition
2. Add handler method that delegates to pipeline
3. Add formatter if needed
4. Update tests
