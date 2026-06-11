# SPEC: Enumeration tools for the MCP server

**Status:** ready for implementation (delegated)
**Date:** 2026-06-10
**Requested by:** Colin (via Claude design session)
**Scope:** `src/mcp_server.py`, `src/mcp_formatters/formatters.py`, tests, docs. No pipeline/indexing changes except the optional `indexed_at` metadata field (Part 3).

## Why

The MCP surface is currently query-only (`search_research_library`, `get_chunk_context`).
Systematic missions (per-source screening/extraction over a closed corpus) need to
**enumerate** a source's chunks and **list** what is in the index — plain metadata
`get`s, no embedding, no similarity ranking. Top-k search cannot prove absence;
enumeration can. These tools were already anticipated in `docs/MCP_SERVER.md`
("Future Enhancements": `list_sources`, `get_siblings`).

## Constraints (repo principles — keep)

- Thin wrapper: handlers delegate to the store; no reimplementation of pipeline logic.
- Formatting isolated in `src/mcp_formatters/formatters.py`; use `.get()` for all
  optional metadata fields.
- Config-driven; lazy pipeline init (`await self._initialize_pipeline()`) as in
  existing handlers.
- Follow the documented add-a-tool pattern in `docs/MCP_SERVER.md`: tool definition
  in `list_tools()`, handler method, formatter, tests.
- Access the store the same way `_get_chunk_context` does:
  `self.pipeline.vector_store.collection` (Chroma collection; supports
  `collection.get(where=..., limit=..., offset=..., include=[...])`).

## Part 1 — `get_source_chunks`

Return ALL chunks belonging to one source document, by metadata, in stable order.

Input schema:
- `zotero_key` (string, optional) — exact Zotero item key.
- `source_path` (string, optional) — exact source path/identifier for Obsidian/local
  sources. Use whatever metadata field the indexer writes for note identity (inspect a
  sample chunk's metadata to confirm the field name; do not guess — document the
  field chosen in the tool description).
- Exactly one of `zotero_key` / `source_path` is required; error otherwise.
- `chunk_level` (string, optional): `coarse` | `mid` | `fine` | `atomic`.
- `include_text` (bool, default true). When false return ids + metadata only
  (cheap census mode).
- `limit` (int, default 50, max 200) and `offset` (int, default 0) — pagination via
  Chroma `get(limit=, offset=)`.

Behaviour:
- Build a Chroma `where` dict from the given filters (AND semantics), call
  `collection.get` with `include=["metadatas"]` (+ `"documents"` when
  `include_text`), paginate.
- **Ordering:** if chunk metadata carries an ordinal (e.g. `chunk_index`,
  `position`, `start_line` — inspect and use what exists), sort by it within level;
  otherwise return store order and say so in the response header. Deterministic
  order matters: callers traverse "all mid chunks of source X" and must not miss or
  duplicate across pages. If no ordinal exists, sort by chunk id as a tiebreak.
- Response header must include: source identity, total matching count (use
  `collection.get` result length with a count-only call, or paginate fully —
  document the cost), page info (`offset`, `limit`, `returned`), and per-chunk:
  `chunk_id`, `chunk_level`, `parent_id`, `heading_path`/section if present, then
  text when requested.
- Errors mirror existing handlers (`format_error_response`).

## Part 2 — `list_sources`

Return the distinct sources present in the index, with chunk counts.

Input schema:
- `source_type` (string, optional): `zotero` | `zotero_fulltext` | `zotero_note` |
  `zotero_annotation` | `obsidian`.
- `title_contains` (string, optional, case-insensitive post-filter).
- `author` (string, optional, case-insensitive post-filter).
- `limit` (int, default 100, max 500) and `offset` (int, default 0) — applied to the
  aggregated, sorted source list (sort by title, then key).

Behaviour:
- Aggregate by scanning collection metadatas in batches
  (`collection.get(include=["metadatas"], limit=batch, offset=...)`, batch ~2000)
  and grouping by source identity (`zotero_key` for Zotero types; the
  Obsidian/local identity field for the rest). Per source report: identity, title,
  authors, year, source_type, chunk counts per level, total chunks.
- This is a full-metadata scan; cache the aggregate in memory on the server
  instance with a simple invalidation rule (timestamp + collection count — if
  `collection.count()` changed, rebuild). Document the cold cost in the tool
  description.
- Per-source identity rule must EXACTLY match what `get_source_chunks` filters on,
  so a `list_sources` row can be fed straight into `get_source_chunks`. State the
  field names in both tool descriptions.

## Part 3 — Freshness stamp (smallest viable version)

Goal: a consumer must be able to tell whether index content is stale relative to
the source file.

- If chunk metadata already records a source mtime/hash at ingest (inspect a sample
  chunk; check `src/indexing.py` / `src/storage/`), surface it: include the field in
  `get_source_chunks` and `list_sources` responses and in
  `format_search_results` output. Name it clearly (e.g. `source_mtime`,
  `indexed_at`).
- If nothing exists, add `indexed_at` (UTC ISO-8601) to chunk metadata at write time
  in the indexing path — additive only, no re-index required; absent on old chunks
  is fine (`.get()` everywhere, display `unknown`).
- Do NOT build mtime-vs-index comparison logic into the server; just surface the
  stamp. The caller owns the staleness decision.

## Tests (required, follow `tests/test_mcp_formatters.py` style)

1. Formatters: source-chunks and list-sources formatters handle missing optional
   fields, empty results, and pagination headers.
2. Handlers with a mocked `collection`:
   - `get_source_chunks`: zotero_key filter, level filter, pagination windows are
     disjoint and complete, include_text=false omits documents, error when both/
     neither identity params given.
   - `list_sources`: aggregation groups correctly across batches; cache rebuilds
     when `collection.count()` changes; post-filters apply.
3. Freshness: stamp present when metadata has it, `unknown` when absent.
4. Integration smoke (optional, behind the existing integration-test convention):
   run against the dev Chroma collection and verify one known source enumerates
   with stable ordering across two paginated calls.

## Docs to update

- `docs/MCP_SERVER.md`: add both tools to Available Tools (move them out of Future
  Enhancements); document the source-identity field names and the list_sources
  cold-scan cost.
- `docs/AGENT_COOKBOOK.md`: add **Pattern E — Systematic per-source mining**:
  build/freeze a source register via `list_sources` → for each source,
  `get_source_chunks` (mid level) → bounded per-source question with explicit
  nulls → records to disk → coverage audit → synthesis. Note explicitly: search is
  for identification only; enumeration is what makes absence claims valid.

## Acceptance criteria

- Both tools callable over stdio and HTTP servers (`src/mcp_http_server.py` reuses
  the same registration — verify, don't assume).
- A full traversal of one real source (every mid chunk exactly once, in stable
  order) is demonstrated in the integration smoke or a documented manual check.
- `pytest` green; no changes to existing tool behaviour or response formats.
- No new dependencies.

## Out of scope

- Semantic/classification logic, mission orchestration, register file formats
  (these live in the thesis vault management layer, not in this repo).
- `get_siblings` (nice-to-have; only if trivial after Part 1).
