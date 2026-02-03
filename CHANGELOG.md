# Changelog

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
