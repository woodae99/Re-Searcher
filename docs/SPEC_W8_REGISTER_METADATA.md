# Implementation Spec — W8 Register Selection Metadata + Annotation Refinements

**Status**: proposed (2026-06-17) · **Parent**: `SPEC_V0.6_REBUILD.md` §W2, §W8 · **Branch**: `v0.6-rebuild`

Two small, independent changes that came out of the 2026-06-17 review:

1. **Annotation refinements (W2)** — keep `atomic`, add a `has_comment` screening flag (+ optional
   `color`/`type`).
2. **Register selection metadata (W8)** — lift systematic-review filter fields into the register
   `sources` table so survey→filter→re-ask (§3d steps 1, 2, 4) can filter without a Chroma scan.

Both are additive. Neither changes chunk identity or the embedding plane, so neither forces a
rebuild — but the register columns are most complete on a fresh v0.6 build (see §4 Backfill).

> **Sequencing**: this work shares the `record_chunks`/registry surface with the index-ledger
> architecture (`docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`) and is scheduled as its **Phase 5**
> ride-along. Land the ledger foundation (P0–P3) first, then apply these column/flag additions on
> the same touched surface to avoid two migrations.

---

## 1. Annotation refinements (W2)

**Decision recap**: `atomic` is correct for annotations and stays. An annotation is one
human-curated unit (`an.text` highlight + optional `an.comment`); recursive splitting could sever
the highlight from the comment, and the only failure mode (a giant annotation truncated at the
embedder) is already backstopped by the oversize guard, which runs on *all* chunks after routing
(`pipeline.py:641-642`). No router or chunker change.

### 1.1 `has_comment` flag (do)

The combined `text\n\ncomment` blob hides whether Colin wrote a comment — his own coding signal.
Add a boolean so screening can filter "annotations with my commentary" vs bare highlights.

- **File**: `src/sources/zotero.py`, `_process_annotations` (~line 1262–1280).
- **Change**: the row already has `an.comment`; set
  `"has_comment": bool(annotation_comment.strip())` in the annotation metadata dict.
- No new SQL needed — `comment` is already selected.

### 1.2 `color` / annotation type — DROPPED (measured 2026-06-17)

Checked against the live `zotero.sqlite`: annotations are **a single type (1=highlight) and a single
color (`#ffd400`)**. Color is not a coding scheme in this corpus, so capturing `color`/`type` would
be dead metadata. **Do not implement.** Revisit only if Colin starts color-coding.

### 1.3 Acceptance (W2 annotations)

- A commented annotation indexes with `has_comment=true`; a bare highlight with `has_comment=false`.
- Oversize guard still splits a synthetic >`max_tokens_per_chunk` annotation (existing behaviour,
  add a regression test if not present).

---

## 2. Register selection metadata (W8)

The register is the **control/filter plane** for the systematic-review loop. The fields the loop
filters on must live in the `sources` table, not in Chroma chunk metadata (where filtering means a
collection scan). Today `sources` has only title/authors/year/backlink/collections.

### 2.1 New `sources` columns

| Column | Source field | Priority | Notes |
|---|---|---|---|
| `item_type` | Zotero item type name ("kind") | **1** | **Not currently extracted** — needs §2.2. The spec's §3b parity map already promises "kind". |
| `doi` | `DOI` | 2 | Canonical SR dedup + citation key. Already in chunk metadata via `**fields`. |
| `abstract` | `abstractNote` | 3 | Title/abstract screening = PRISMA stage 1. Larger; one row/source in SQLite is fine. |
| `tags` | item tags | 4 | Already extracted to metadata (`zotero.py:993-1002`) but not lifted. Store comma-joined like `collections`. |
| `venue` | `publicationTitle` | 5 | Inclusion/exclusion filter. Already in `**fields`. |
| `language` | `language` | 5 | "English only"-style filter. Already in `**fields`. |

`doi`/`abstract`/`venue`/`language` already arrive in `Document.metadata` via the `**fields` spread
in `_get_item_metadata` (`zotero.py:1027`); `tags` is already a metadata list. The work for these is
**lifting them into the register**, not new extraction. Only `item_type` needs an extraction change.

**Do not** dump all `**fields` into the register — keep ISBN/ISSN/pages/volume/issue in chunk
metadata. The register gets the SR filter set only.

### 2.2 Extraction change — resolve `item_type` (`src/sources/zotero.py`)

`_get_item_metadata` (line 952) queries field values and creators/tags/collections but never the
item's *type name* — only `itemTypeID` is read in `_get_all_items`. Add a lookup:

```sql
SELECT it.typeName
FROM items i
JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
WHERE i.itemID = ?
```

Set `"item_type": type_name` in the returned dict (alongside `zotero_key`, `title`, …). This flows
to chunk metadata and the register in the same step. `tags` is already in the dict; ensure it
survives to `record_chunks` (it does — `metadata` is passed whole).

### 2.3 Schema + migration (`src/registry.py`)

- Bump `SCHEMA_VERSION` to the next value. As of the ledger/P3 working tree, `SCHEMA_VERSION = 3`
  is already used for `index_units` plus child-key columns on `chunks`, so W8 selection metadata
  should bump to **4**.
- Add the six columns to the `CREATE TABLE sources` body (default `''`).
- Extend the existing additive-migration block to add each new column when absent — same
  `PRAGMA table_info(sources)` guard, one
  `ALTER TABLE sources ADD COLUMN … TEXT DEFAULT ''` per missing column. This keeps existing
  registries readable without a reset.

### 2.4 Write path (`record_chunks`, lines 165–302)

- In the per-chunk loop, extend the `attrs` accumulator dict to carry the six new fields, using the
  **same first-non-placeholder-wins** pattern already used for title/authors (`_PLACEHOLDER_*`).
  For `tags`, mirror the `collections` list-or-string handling (lines 224–233): join a list with
  `", "`, else take the string.
- In the `INSERT INTO sources(...) … ON CONFLICT … DO UPDATE` (lines 259–294), add the six columns
  to the column list, the values tuple, and the `DO UPDATE SET` clause with the same
  `CASE WHEN excluded.x != '' AND sources.x = '' THEN excluded.x ELSE sources.x END` guard.
- `refresh_sources` (line 338) derives only counts/types/freshness and uses
  `ON CONFLICT … DO NOTHING` for identity, so it does **not** clobber these columns — no change
  needed there.

### 2.5 Read path + filters (`list_sources_payload`, lines 523–589)

- Add optional kwargs `item_type`, `doi`, `language`, `tag` and corresponding `WHERE` clauses
  (reuse the `_like_escape` + `ESCAPE '\\'` pattern; for `tags` use the comma-wrapped `LIKE` trick
  already used for `source_types` at line 538).
- Add `item_type`, `doi`, `venue`, `language`, `tags`, and (optionally) `abstract` to each emitted
  source dict. Consider omitting `abstract` from the default list payload (size) and exposing it
  only in a single-source detail view.

### 2.6 Surface parity (MCP + CLI) — keep the parity rule

- `src/mcp_formatters/formatters.py`: include the new fields in the `list_sources` formatting (and
  a source-detail view if one exists). Keep `abstract` truncated in list output.
- `src/mcp_server.py` + `scripts/sources.py`: add the new filter args (`--item-type`, `--doi`,
  `--language`, `--tag`) so both surfaces run the same `list_sources_payload` code (CONVENTIONS:
  CLI/MCP parity). Thin wrappers only.

### 2.7 Acceptance (W8 metadata)

- Fresh v0.6 build: `sources` rows carry `item_type`, `doi`, `abstract`, `tags`, `venue`,
  `language` for Zotero items that have them.
- `sources.py list --item-type book` and `--language en` filter without scanning Chroma.
- Existing registry opens and serves after the additive migration (no reset required).
- A book and a journal article are distinguishable by `item_type` in `list_sources`.

---

## 3. Why these belong in the register, not chunk metadata

Loop steps 1 (survey), 2 (filter), 4 (re-ask) are register-scoped (`SPEC_V0.6_REBUILD.md` §3d).
Filtering "books in English with a DOI, tagged X" over chunk metadata would require a Chroma
collection scan; over the register it's an indexed SQL `WHERE`. This is the same rationale that
elevated the register from mirror to control plane.

---

## 4. Backfill note (existing collections)

`scripts/build_registry.py` lifts attributes from **existing Chroma chunk metadata**:

- `doi`, `abstract`/`abstractNote`, `venue`/`publicationTitle`, `language`, `tags` **are already in
  the current production collection's chunk metadata** (the `**fields` spread predates this work),
  so a re-run of the backfill *can* recover them for the old collection if `record_chunks` reads
  them.
- **`item_type` is NOT in existing chunk metadata** — it was never extracted. The backfill cannot
  invent it; old collections will have empty `item_type` until re-indexed. This is acceptable: the
  full v0.6 rebuild is the convergence point. (If `item_type` on the *current* collection is wanted
  before then, a one-off enrichment script could read `zotero.sqlite` by `zotero_key` and `UPDATE
  sources` — out of scope here.)

---

## 5. Work order

1. §2.2 extraction (`item_type`) + §1.1 `has_comment` — one `zotero.py` pass.
2. §2.3 schema/migration + §2.4 write path (`registry.py`).
3. §2.5 read/filters + §2.6 formatter/CLI/MCP parity.
4. Tests: extraction sets `item_type`/`has_comment`; migration is additive; `list_sources` filters;
   oversize-guard splits a giant annotation.
5. §1.2 `color`/`type` only if Colin confirms color-coding is part of his workflow.
