# Re-Searcher: Project Context

## Overview

Re-Searcher is a semantic search and systematic-review system for academic research,
built for Colin's PhD thesis work on coaching theory. It indexes Zotero (references,
PDF fulltext, notes, annotations) and Obsidian (markdown vault) into ChromaDB using
BGE-M3 embeddings served by LM Studio, and exposes the corpus to humans (CLI) and
agents (MCP) with deliberate parity between the two surfaces.

## Current State (June 2026)

- **Production collection**: `research_library`, ~9.85M chunks from ~8,200 Zotero
  items + ~4,700 Obsidian notes. ChromaDB runs natively on Windows (python process, port 8000) — moved out of Docker for performance.
- **Embeddings**: BGE-M3 (1024-dim) via LM Studio at `http://localhost:1234/v1`,
  JIT-loaded on the local RTX 5090. Reranking uses a small LLM via the same server.
- **Source registry**: SQLite mirror of source/chunk identity at
  `output/registry.<collection>.sqlite`, maintained by the indexing pipeline in the
  same code paths that write to ChromaDB. Enumeration (`list_sources`, status,
  drift checks) reads the registry — never a collection scan.
- **Everything runs on this machine (Bambino)**. Remote agents reach the MCP server
  over HTTP (port 8001); local use goes through stdio MCP or the CLI scripts.

## Architecture

```
Claude / Hermes (MCP clients)          Colin (CLI)
        │                                  │
        ▼                                  ▼
src/mcp_server.py  ◀── shared logic ──▶  scripts/*.py
        │        (src/enumeration.py,
        │         src/mcp_formatters/)
        ▼
src/pipeline.py ──▶ ChromaDB (native, :8000)   ←─ vectors + chunk text
        │      └──▶ output/registry.*.sqlite  ←─ source register, sync state
        ▼
LM Studio (:1234, BGE-M3 embed + rerank)
```

## Key Files

- `config.yaml` — main config (gitignored; see `config.example.yaml`)
- `src/pipeline.py` — ResearchRAGPipeline: indexing (batched, resumable, delta) + query
- `src/registry.py` — source registry (SQLite) + checkpointed backfill
- `src/registry_audit.py` — integrity audit: registry vs Zotero SQLite vs vault
- `src/enumeration.py` — shared get_source_chunks logic (MCP + CLI)
- `src/mcp_server.py` — MCP tools: search_research_library, get_chunk_context,
  get_source_chunks, list_sources, index_status
- `src/mcp_http_server.py` — HTTP transport wrapper (LAN access, port 8001)
- `src/sources/{zotero,obsidian}.py` — extraction; `src/storage/chroma.py` — vector store

## Common Commands

```bash
python scripts/index.py                  # incremental index update (delta-aware, resumable)
python scripts/index.py --request-stop   # cleanly stop a running index after current batch
python scripts/query.py "..." -k 5       # semantic search (mirrors MCP search tool)
python scripts/sources.py list --collection "Process"   # source register (mirrors list_sources)
python scripts/sources.py chunks --zotero-key KEY       # enumerate one source
python scripts/sources.py status         # registry vs Chroma drift check
python scripts/build_registry.py         # one-time registry backfill + integrity audit (resumable)
python -m pytest tests/ --ignore=tests/integration --ignore=tests/pipeline -q
```

Known pre-existing test failures: `tests/test_rerank_json.py` and
`tests/test_resumable_indexing.py` need an OpenAI-compatible API key in the
environment; they are unrelated to most changes.

## Conventions (keep these)

- **Thin MCP wrapper**: tool handlers delegate to the pipeline/registry; no business
  logic in `mcp_server.py`. Output formatting lives in `src/mcp_formatters/`.
- **CLI/MCP parity**: every capability exists on both surfaces and runs the same code.
  Enumeration tools get a `scripts/sources.py` subcommand; search/survey live in
  `scripts/query.py`. Machine output is identical too — `--json` on both `sources.py`
  and `query.py` emits payloads built from the shared `src/mcp_formatters` the MCP
  tools use, so CLI↔MCP parity holds by construction, not by a second serializer.
- **stdout is the data channel**: a `--json` command must emit only JSON on stdout.
  All status/diagnostic lines (`[OK]` connect/source banners, `Query:`/`[TIMING]`)
  go to **stderr** (see `src/storage/chroma.py`, `src/pipeline.py`). Consumers parse
  stdout directly — no "skip to the first `{`" slicing. `tests/integration/test_mission_surface_parity.py`
  enforces this and fails if a status line leaks onto stdout.
- **Source identity rule** (single definition in `src/registry.py`): Zotero-derived
  chunks group by `zotero_key`; everything else by `source_id`
  (`obsidian-<relative_path>` for vault notes).
- **Resumability**: any operation that can run for minutes must checkpoint durable
  state and resume after interruption (see `_process_batches`, the backfill).
  This machine is a workstation, not an always-on server.
- **Registry sync**: code that writes to or deletes from ChromaDB must update the
  registry in the same step. Drift is detected via `index_status` / `sources.py status`
  and repaired with `scripts/build_registry.py`.

## Roadmap

See `CHANGELOG.md` for shipped work. Agreed next phases (June 2026):

- **Phase 2 — trustworthy updates (shipped in 0.5.0)**: Obsidian per-file delta +
  deletes, Zotero `/deleted?since=` handling, batched deletes with no key cap,
  version-keyed progress, fail-loud embeddings, per-source change hashes.
- **Phase 3 — throughput (next)**: raise embedding concurrency (the 4-day rebuild
  bottleneck), honor configured store batch sizes, parallel upserts, weekly
  reconcile in `routine_update_re_searcher.cmd`, metadata-only update path
  (deferred from Phase 2).
