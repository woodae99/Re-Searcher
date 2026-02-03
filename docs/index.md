# Re-Searcher

Re-Searcher is a local-first research indexing + retrieval toolkit.

It’s designed to:
- ingest a research library (Zotero + Obsidian + local files)
- chunk + embed content
- store vectors + rich metadata
- support practical research workflows: “find sources”, “deep dive”, “build a brief”, and RAG-style answering

## Where to start

- **Quick start / day-to-day usage** → [Usage Guide](USAGE_GUIDE.md)
- **Research workflows** → [Workflows](WORKFLOWS.md)
- **MCP server** (connect tools/agents to Re-Searcher) → [MCP Server](MCP_SERVER.md)
- **Integrations** (Zotero, Obsidian, etc.) → [Integrations](integrations.md)
- **System design / query & indexing pipeline** → [Specification](specification.md)

## Documentation site

Run the docs locally:

```bash
mkdocs serve
```

Build a static site:

```bash
mkdocs build
```
