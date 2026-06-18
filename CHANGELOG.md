# Changelog

## 0.6.0 - 2026-06-18

### Added
- **vLLM embedding backend + managed lifecycle** (`src/embedding/vllm.py`,
  `src/embedding/vllm_server.py`, `scripts/vllm_service.py`, `docs/EMBEDDING_BACKEND.md`):
  v0.6 production embedder is Qwen3-Embedding-0.6B served by vLLM (~6× LM Studio
  throughput; see `docs/EMBEDDER_BAKEOFF.md`). `VLLMEmbedding` reuses the
  OpenAI-compatible client; `VLLMServer` + `managed_embedding_backend(config)` give a
  config-driven stand-up → process → stand-down lifecycle so vLLM never has to be
  hand-driven. Bulk jobs (`scripts/index.py`, `scripts/reindex.py`) auto start/stop the
  container; `scripts/vllm_service.py {start,stop,status}` runs a persistent server for
  interactive retrieval. Selectable via `embedding.provider: vllm`; LM Studio retained.
- **Cross-encoder reranker via vLLM `/rerank`** (`CrossEncoderReranker` in
  `src/retrieval/rerank.py`, `reranker_factory` `type: cross_encoder`): the v0.6
  production reranker runtime — scores (query, document) pairs directly through vLLM's
  native `/rerank` (default `BAAI/bge-reranker-v2-m3`), no LLM JSON to parse. Managed by
  the same vLLM lifecycle (`managed_reranker_backend`; `scripts/vllm_service.py` stands up
  embedder + reranker together for retrieval). The old LM Studio/LLM JSON reranker is retired.
- **Asymmetric query instruction** (`embedding.query_instruction`): prepended to queries
  in `embed_query` only (documents embedded raw) — required for Qwen3-Embedding's
  retrieval-quality edge; no-op/empty for symmetric models like bge-m3.
- **Embedder bake-off** (`scripts/eval_embedders.py`, `docs/EMBEDDER_BAKEOFF.md`):
  compares embedders on retrieval quality + cost (throughput, projected store size) with
  family-correct prompt prefixes; established Qwen3-Embedding-0.6B > bge-m3 at equal storage.
- **Passage-level chunking eval** (`src/passage_eval.py`, `scripts/eval_passage.py`,
  `docs/PASSAGE_EVAL.md`): sharpens the v0.6 chunk-*size* decision past the
  source-level eval's saturation. Scores passage retrieval (`passage_hit@k`,
  `passage_mrr`, strict ≥½-span variant) against real target texts + a large
  distractor pool, plus the completeness/density trade-off under read-time
  neighbour expansion — all deterministic span arithmetic (no LLM judge). Gold is
  generated once via a local LLM with a curation pass that snaps passages to
  sentence boundaries and screens out reference-list / front-matter junk. `--rerank`
  scores the production cross-encoder reranker path.
- **Reranker bake-off** (`scripts/eval_rerankers.py`, `docs/RERANKER_BAKEOFF.md`):
  holds grain + gold fixed and sweeps rerankers on speed *and* accuracy
  (`none` / HTTP cross-encoder). The v0.6 production path is the vLLM-served
  cross-encoder in `src/retrieval/rerank.py`.
- **`scripts/query.py --json`** — the CLI search/survey surface now has a
  machine-readable mode, closing the last CLI↔MCP parity gap. Search emits
  `{query, count, results}` (results via the shared `format_search_results`); survey
  emits the `survey_sources` payload — the same shapes the MCP `search_research_library`
  / `survey_research_sources` tools return, so parity holds by construction. Works on
  one-shot queries with or without `--survey`. (`scripts/sources.py` already had `--json`.)
- **Mission-surface validation suite** (`tests/integration/test_mission_surface_parity.py`):
  22 live-stack tests derived from the process-in-coaching mission spec — proves CLI/MCP
  availability + equivalence, single-grain retrieval (mid only, no `parent_id`),
  register-as-control-plane filtering + extraction provenance, the §5 screening
  enumeration (all mid chunks, deterministic stop), and survey/pinned-source retrieval.
  Service-guarded so it skips cleanly when the live stack is down.

### Changed
- **Status/diagnostics moved to stderr; stdout is the data channel.** The `[OK]`
  Chroma-connect and Zotero/Obsidian source banners (`src/storage/chroma.py`,
  `src/pipeline.py`) and the per-query `Query:`/`Survey query:`/`[TIMING]` lines now
  print to **stderr**. `--json` stdout is therefore pristine JSON that consumers parse
  directly — no leading-banner slicing. Humans still see the diagnostics on the terminal.
- **Chunking defaults → `recursive` 700/100** in `config.example.yaml` (top-level
  and `chunking.defaults`), replacing the stale `character`/2048 default. Settled
  by measured retrieval evals: `recursive` decisively beats `character`, and
  700/100 wins on passage-retrieval quality through the rerank stage while staying
  lean to read. See `docs/CHUNKING_EVAL.md` and `docs/PASSAGE_EVAL.md`.
  (Apply the same change to the live, gitignored `config.yaml`.)
- **Production cutover defaults are ledger-first and single-grain only.**
  `indexing.ledger.execute` defaults to true, ledger shadow mode defaults off,
  `legacy_router` is rejected by preflight/factory code, and status distinguishes
  expected no-fulltext coverage-null units from unexpected ledger drift.

### Fixed
- **Launcher Python selection is consistent.** `run_mcp.bat`,
  `run_mcp_http_lan.ps1`, and `scripts/start_prod_ingest.cmd` previously used
  three different conventions (system Python hardcoded / `.venv` hardcoded /
  `.venv`-preferred); they now all prefer the project `.venv` and fall back to
  system Python 3.13, matching `routine_update`/`warmup`. The repo `.venv` had
  drifted empty — repopulate from `requirements.txt`. For v0.6/Sparky,
  Chroma is pinned to 1.5.9 via `requirements.txt` and
  `constraints-sparky.txt`; see `docs/SPEC_V0.6_REBUILD.md` for the planned
  rebuild.

## 0.5.0

Trustworthy updates: routine index runs now converge the index to the truth.

### Fixed
- **Edited Obsidian notes are re-indexed.** New per-file vault delta: the
  registry keeps a (mtime, size) snapshot per note; each run diffs the vault
  against it, re-indexes changed notes (deleting their old chunks first) and
  removes chunks for deleted notes. Previously an edited note was never
  re-indexed and a deleted note's chunks lived forever. On first run after the
  registry backfill, the delta bootstraps from per-source `indexed_at` stamps.
- **Zotero deletions are detected while Zotero is open.** The API delta path
  now also queries `/deleted?since=`; previously deletions were only caught by
  the closed-Zotero SQLite path, whose watermark had usually already advanced
  past them. Parent-key resolution also retains keys purged from zotero.sqlite
  so their chunks can still be deleted.
- **Large delta runs no longer create duplicates.** The >500-key delete-skip is
  removed; old chunks for changed/deleted items are deleted in batched `$in`
  filters (`indexing.delta.delete_batch_size`, default 100) with no key limit.
  Pure-deletion runs (no documents to fetch) now actually apply the deletions.
- **Failed embedding batches no longer store zero vectors.** `embed_texts`
  raises so the pipeline marks the affected documents ERROR and retries next
  run; `embed_query` raises instead of silently searching with a zero vector.

### Changed
- **Version-keyed progress**: the progress checkpoint records each document's
  content version (file mtime/size for notes, `dateModified` for Zotero items);
  "already stored" now means "this version stored". Pre-upgrade records without
  a version are trusted, so the upgrade does not trigger a mass re-index.
- **Per-source change hashes**: `source_hash.txt` now stores separate config /
  Zotero / Obsidian hashes. An edited note no longer triggers a full Zotero
  re-fetch (the main cause of multi-hour "routine" updates), and a config edit
  alone warns instead of forcing a scan.
- `indexing.delta.max_delete_keys_per_run` is retired (no longer needed);
  `indexing.delta.delete_batch_size` controls delete batching.

### Deferred
- Metadata-only update path (re-write chunk metadata without re-embedding when
  only Zotero fields changed) — planned alongside Phase 3 throughput work.

## 0.4.0

Source registry: enumeration becomes a first-class, maintained system component.

### Added
- **Source registry** (`src/registry.py`): SQLite mirror of source/chunk identity
  (`output/registry.<collection>.sqlite`), updated by the indexing pipeline in the
  same code paths that write to and delete from ChromaDB. `list_sources` now reads
  it directly and responds immediately at any collection size.
- **Checkpointed backfill + integrity audit** (`scripts/build_registry.py`):
  one-time registry build for pre-registry collections. Commits its scan offset
  atomically with each batch, so it resumes after interruption. Finishes with an
  audit (`output/registry_audit.json`) diffing the index against Zotero SQLite and
  the Obsidian vault: ghosts, missing/stale notes, duplicate chunk slots, legacy
  IDs, optional zero-vector sampling (`--check-embeddings N`).
- **index_status MCP tool**: registry vs Chroma counts, drift flag, last run
  timestamps, server git SHA. Intended as a preflight check for systematic
  per-source missions.
- **CLI parity** (`scripts/sources.py`): `list`, `chunks`, `status` subcommands
  mirroring `list_sources` / `get_source_chunks` / `index_status` with shared
  logic and formatting (`src/enumeration.py`); `--json` for machine output.
- Server startup now logs the git SHA for deploy traceability.

### Changed
- `list_sources` no longer scans collection metadata at request time. The
  background cache machinery (`output/mcp_source_cache.json`, count-keyed
  invalidation, "cache is building" retry responses) is removed; an unbuilt
  registry returns instructions for the one-time backfill instead.
- `list_sources` `source_type` filter now matches membership (a source with any
  chunks of the requested type), fixing rows being hidden when their first-seen
  chunk had a different type.
- `src/mcp_http_server.py` no longer constructs a second server instance at
  import time.

## 0.3.1

Hierarchical chunk filtering + UX improvements.

### Added
- **Chunk-level filtering**: New `--chunk-level {coarse,mid,fine}` parameter for controlling result granularity
  - CLI: `--chunk-level coarse` for substantive content with broad context (~1500-2500 chars)
  - MCP: `chunk_level` parameter in `search_research_library` tool
  - Enables quick searches (fine) vs deep dives (coarse) using the hierarchical chunking infrastructure
- **Enhanced help text**: All CLI parameters now include detailed descriptions with concrete examples
- **Explicit MCP parameters**: All MCP tool parameters include comprehensive descriptions for better LLM usage
- **Interactive mode improvements**: New `:help` command and improved intro screen showing available features

### Fixed
- **Diversity auto-enable**: `--max-per-source` now auto-enables diversity filtering when set (previously had no effect if diversity was disabled in config)
- **Duplicate prevention**: Diversity filtering now properly prevents multiple chunks from same source

### Changed
- **CLI help output**: More descriptive parameter help for better discoverability by tools and agents
- **Interactive mode**: Added guidance on advanced filtering options available in CLI mode

## 0.3.0

Minor release (docs + pipeline quality-of-life).

- Retrieval pipeline improvements: metadata filters, diversity/dedupe controls, more robust reranking.
- CLI/MCP: added retrieval controls (e.g. diversity toggles, `k_recall` override).
- Documentation refresh: MkDocs home page + navigation, updated README.

## 0.2.0

- Initial public-ish cut of the indexing + retrieval pipeline with integrations and basic docs.
