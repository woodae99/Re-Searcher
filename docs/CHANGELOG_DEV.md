# Dev Changelog (local, unmerged)

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
