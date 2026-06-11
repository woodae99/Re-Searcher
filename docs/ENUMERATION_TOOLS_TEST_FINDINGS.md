# Test findings — enumeration tools (live MCP, 2026-06-10)

Tested by Claude against the production index via MCP. Spec: SPEC_ENUMERATION_TOOLS.md.

## Working as specified
- `get_source_chunks` single-filter (zotero_key=KRFY3VQI): correct header
  (Total 13,162; offset/limit/returned), documented ordering
  ("chunk_index (id tie-break)"), census mode (include_text=false) works.
- Pagination stable: offset=3,limit=2 reproduced exactly the items at
  positions 4-5 of the offset=0,limit=5 page.
- Freshness field present, "unknown" on pre-existing chunks (acceptable per
  spec; stamps appear only for newly indexed chunks).

## Bug 1 (blocking): multi-condition where
`get_source_chunks(zotero_key=..., chunk_level="mid")` →
`ValueError: Expected where to have exactly one operator, got
{'zotero_key': ..., 'chunk_level': 'mid'} in get.`
Chroma requires `{"$and": [{...}, {...}]}` for multiple conditions; the
handler builds a flat dict. **This blocks the mission workflow** ("enumerate
all mid chunks of source X" is the core call). Fix + add a test for the
two-filter path (spec listed level filter + key as a required test case).

## Bug 2 (high): list_sources cold scan exceeds MCP client timeout
`list_sources(title_contains="coaching", limit=3)` → MCP -32001 timeout, while
Chroma itself was warm (search returned instantly). The full-metadata
aggregation scan takes longer than the client request timeout, so the cache
never gets served. Options (pick one): build the cache at server start
(background task); persist the aggregate to disk keyed on collection.count();
or stream/partial-scan with a `scan_budget` + resume token. Re-test after.

## Note
- Section heading shows "unknown" for these zotero_fulltext chunks — if
  heading_path simply isn't present for PDF-extracted chunks, fine; confirm
  the field passes through when it exists (Obsidian sources).
