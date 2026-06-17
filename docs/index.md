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
- **v0.6 extraction bake-off and routing** → [Extraction Bake-off and Routing Strategy](EXTRACTION_BAKEOFF_AND_ROUTING.md)
- **v0.6 register as index ledger (W10 — reconciliation-driven updates)** → [Register as Index Ledger](SPEC_REGISTER_AS_INDEX_LEDGER.md)
- **v0.6 ledger implementation handoff (P2–P5, cold-start spec)** → [Ledger Implementation Handoff](SPEC_LEDGER_IMPLEMENTATION_HANDOFF.md)
- **v0.6 register selection metadata + annotation refinements (W8/W2)** → [Register Metadata Spec](SPEC_W8_REGISTER_METADATA.md)

## Documentation site

Run the docs locally:

```bash
mkdocs serve
```

Build a static site:

```bash
mkdocs build
```
