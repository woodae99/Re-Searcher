# Re-Searcher

Local-first semantic + keyword search for researchers.

Re-Searcher indexes your research library (Zotero + Obsidian + local files), stores embeddings + rich metadata, and supports practical “find sources / deep dive / build a brief” workflows.

## What’s in here

- **Indexing pipeline**: extract → chunk → embed → store (resumable, batch-concurrent)
- **Retrieval pipeline**: metadata filters, diversity/dedupe, robust reranking
- **Source registry**: SQLite register of indexed sources for systematic-review
  enumeration (list/enumerate/status with drift detection; CLI + MCP parity)
- **Integrations**: Zotero (SQLite), Obsidian (frontmatter + links), local files
- **Optional MCP server**: expose retrieval/indexing to agents/tools via MCP

## Docs

- Start here: `docs/USAGE_GUIDE.md`
- Workflows: `docs/WORKFLOWS.md`
- MCP server: `docs/MCP_SERVER.md`
- Full docs site (MkDocs): `mkdocs serve`

## Quick start

### 1) Install

This repo is managed with **Poetry** (Python **3.11+**):

```bash
poetry install
```

(Alternative legacy install is available via `requirements.txt`, but Poetry is the canonical path.)

### 2) Configure

```bash
cp config.example.yaml config.yaml
# edit config.yaml (paths, endpoints, etc.)
```

### 3) Run indexing

```bash
python src/main.py
```

If interrupted, just run it again — it will resume automatically (see `docs/USAGE_GUIDE.md`).

## Development

- Run tests:
  ```bash
  pytest
  ```
- Run docs locally:
  ```bash
  mkdocs serve
  ```

## Repo hygiene

Generated outputs are written under `output/` (and should not be committed).

---

If you’re trying to *use* Re-Searcher rather than modify it, start with `docs/WORKFLOWS.md` — it’s the most “how a human actually uses this” document.
