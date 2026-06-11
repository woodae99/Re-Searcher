# Changelog

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
