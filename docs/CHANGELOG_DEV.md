# Dev Changelog (local, unmerged)

## 2026-04-01
- Added query retrieval mode control (`fast` vs `strict`) and timing telemetry in query path. (`src/pipeline.py`, `src/retrieval/filters.py`)
- Reworked interactive query CLI with runtime filter commands and explicit mode selection. (`scripts/query.py`)
- Wired `QualityFilterGuard` into active chunk processing and fixed stable chunk IDs to use `chunk_index`. (`src/pipeline.py`)
- Added Zotero delta ingestion state, changed-key targeting, and guarded delete/reprocess flow. (`src/pipeline.py`, `src/sources/zotero.py`, `src/storage/chroma.py`, `src/indexing.py`)
- Added Zotero local API delta support (`since`-based item/fulltext change detection) with SQLite fallback. (`src/sources/zotero.py`)
- Added fulltext-first Zotero extraction with fallback for likely partial indexing on large PDFs. (`src/sources/zotero.py`)
- Updated configs with retrieval mode defaults, delta controls, and Zotero fulltext settings. (`config.example.yaml`, `config.yaml`)
- Added session handoff and resume notes after stopping a broad delta run. (`docs/SESSION_HANDOFF_2026-04-01.md`, `docs/TRACKING.md`)

## 2026-01-26
- Added HTTP (streamable) MCP server for remote/LAN access. (`src/mcp_http_server.py`)
- Added helper scripts for LAN access on Windows. (`run_mcp_http_lan.bat`, `run_mcp_http_lan.ps1`)
- Updated MCP documentation with HTTP server setup, LAN access, and firewall instructions. (`docs/MCP_SERVER.md`)
- Updated README with MCP Access section. (`README.md`)

## 2026-01-09
- Added a QualityFilterGuard to drop low-information chunks before embedding, with dry-run, whitelist/blacklist, reporting, and per-batch summaries. (`src/processing/quality_filter.py`, `src/pipeline.py`, `tests/unit/test_quality_filter_guard.py`, `config.example.yaml`)
- Added per-batch timing metrics and optional JSONL logging via `PIPELINE_METRICS_LOG`. (`src/pipeline.py`)
- Added MCP debug logging (flagged by `MCP_DEBUG_LOG`) and reranker debug logging (flagged by `RERANK_DEBUG_LOG`). (`src/mcp_server.py`, `src/retrieval/rerank.py`)
- Added reranker response cleanup to strip `<think>` tags and isolate JSON payloads. (`src/retrieval/rerank.py`)
- Updated MCP formatter to include parent context fields when present. (`src/mcp_formatters/formatters.py`)
- Improved epub item detection for compatibility across ebooklib versions. (`src/extract_text.py`)
- Enabled aggressive embed/store pipeline settings in `config.yaml` (local only, gitignored). (`config.yaml`)
- Added logging env vars to `run_mcp.bat` for MCP/rerank debug logs. (`run_mcp.bat`)

Notes:
- `runs/` is now ignored to avoid committing run artifacts.
