# Session Handoff - 2026-04-01

## What we changed this session

- Added retrieval mode control: `fast` (post-filter) vs `strict` (Chroma where-filter).
- Added query timing telemetry in pipeline output.
- Reworked interactive query CLI with runtime filter commands and `--mode`.
- Wired `QualityFilterGuard` into active chunk processing path.
- Fixed stable chunk ID generation to use per-document `chunk_index`.
- Added Zotero delta ingestion state + targeted reprocessing by changed item keys.
- Added Zotero local API delta path (`items?since=...`, `fulltext?since=...`) with SQLite fallback.
- Added vector store `delete_where` and safe delete cap for delta runs.
- Added Zotero fulltext-first extraction with fallback to local extraction when likely partial for large PDFs.
- Updated `config.example.yaml` and local `config.yaml` to include delta/retrieval/fulltext controls.

## Delta run status at stop

- User requested stop because run appeared too broad.
- Foreground shell is idle.
- `output/indexing_progress.json` was stable over repeated checks.
- Last observed long run behavior: a large Zotero delta set was discovered and processing started, but previous long command timed out before completion.

## Important known issue

- With Zotero closed, SQLite access is fast and reliable.
- With Zotero open, SQLite can lock and targeted fetch may fail.
- Current safe operational guidance for heavy ingest:
  - Close Zotero before running `scripts/index.py`.
  - Reopen Zotero only after run completion.

## Resume checklist (next session)

1. Confirm Zotero is closed.
2. Snapshot state files:
   - `output/indexing_progress.json`
   - `output/zotero_delta_state.json`
3. Run a bounded delta pass first (small target set) before any broad run.
4. Verify changed-key detection count is plausible before processing.
5. If the change set is unexpectedly huge, inspect delta watermark logic before continuing.
6. Run query smoke tests in both modes:
   - `python scripts/query.py "coaching" -k 3 --mode fast --no-rerank`
   - `python scripts/query.py "coaching" -k 3 --mode strict --no-rerank`

## Suggested next implementation slice

- Add an explicit `--max-delta-items` limit to `scripts/index.py` and pipeline run options.
- Add a dry-run mode that reports which Zotero item keys would be reprocessed/deleted.
- Make problematic PDF logging path configurable inside workspace (avoid permission issues).

## Files touched in code this session

- `config.example.yaml`
- `config.yaml` (local)
- `scripts/query.py`
- `src/indexing.py`
- `src/pipeline.py`
- `src/retrieval/filters.py`
- `src/sources/zotero.py`
- `src/storage/chroma.py`
