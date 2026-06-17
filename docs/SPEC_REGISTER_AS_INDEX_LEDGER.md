# Architecture Spec — Register as Index Ledger (Reconciliation Authority)

**Status**: proposed (2026-06-17) · **Parent**: `SPEC_V0.6_REBUILD.md` §3a, §3d · **Branch**: `v0.6-rebuild`
**Supersedes**: the standalone "delta sub-item precision" idea (now a *consequence* of this design, §6)
**Absorbs**: `SPEC_W8_REGISTER_METADATA.md` rides Phase 5 (shared `record_chunks`/registry surface)

---

## 1. Thesis

"What needs processing" is not an event we detect — it is a **diff between the world's current
state and the state we have recorded**. v0.6 already made the register the relational control
plane; this spec finishes the thought: **the register owns the index ledger and leads the update
run; ChromaDB strictly follows.**

Three responsibilities, cleanly separated:

| Responsibility | Home | Why |
|---|---|---|
| **Detect** — enumerate each source's current units + a per-unit fingerprint | source adapters (`zotero.py`, `obsidian.py`) | inherently source-coupled; only the adapter knows `storageHash` / `mtime` |
| **Decide** — diff world-state vs recorded-state → a work plan | register (ledger) + a thin source-agnostic planner | relational, durable, queryable; already authoritative for identity |
| **Execute** — embed the planned units; write vectors+ledger together | pipeline | the only stage that touches the embedder |

ChromaDB is a **follower**: it receives upserts and deletes as *outputs* of a decision made
against the register. It has no opinion about what is stale and is never consulted to find out.

## 2. Why this is the right home (and what it replaces)

- **The register is already doing this — for Obsidian.** `vault_files` (in the register) holds
  per-file `(mtime, size)`; the update is `diff(disk, vault_files)` → changed/deleted. That is the
  ledger model, already shipped and clean. This spec **generalizes the `vault_files` pattern to
  Zotero, at sub-item grain.**
- **Zotero is the laggard.** Its recorded state lives in a **sidecar file**,
  `output/zotero_delta_state.json` (library-version watermarks), and collapses every change to the
  parent `zotero_key`. That sidecar is a fossil of the pre-register architecture (Phase 2/3 had no
  relational plane to hold sync state). v0.6 has one now, so the sidecar is **retired**; its one
  useful field (the library version) becomes a *cursor* in the register `meta` table.
- **Dependency arrow flips for planning.** Today the register is a *mirror* written as a side
  effect of Chroma writes (`record_chunks` trails an upsert). Under this spec the register **leads
  planning** (it is read first to compute the plan); both planes are written on **execution**.
  Elevation: §3a's "source of truth for identity/genealogy/navigation/provenance" gains **and sync
  state**.

## 3. The ledger model

The register records, per **indexable unit**, the source-side fingerprint it was last indexed at.
A "unit" is the smallest thing that can change independently:

| Source | Unit kinds | Fingerprint (opaque to the register) |
|---|---|---|
| Zotero | `parent_meta`, `note`, `attachment`, `annotation` | `parent_meta`/`note`/`annotation` → item `dateModified`; `attachment` → `storageHash` (+ `storageModTime`, fulltext version) |
| Obsidian | `vault_file` | `mtime:size` (or content hash) |

Critically, **the register stores fingerprints as opaque strings and only ever compares them** —
it never parses a `storageHash` or understands what a `dateModified` means. That keeps the register
source-agnostic while still being the authority on "what we have, at what version."

The update run is then three source-agnostic steps:

1. **Enumerate** world state (adapters) → `{unit_id: UnitState(kind, parent_identity, fingerprint)}`.
   Cheap: a Zotero SQLite read, a vault stat. A library-version **cursor** (register `meta`) is an
   optional fast-path pre-filter so we don't enumerate all 8,000 items every run — but it is an
   optimization, not the source of truth.
2. **Reconcile** against the ledger → a `WorkPlan`:
   - in world, not in ledger → **create**
   - in both, fingerprint differs → **update** (carrying *which units*)
   - in ledger, not in world → **delete**
3. **Execute** (pipeline): extract→chunk→embed only created/updated units; apply upserts+deletes to
   Chroma **and** update the ledger fingerprints in the same durable step.

## 4. Schema (register)

Additive migration; bump `SCHEMA_VERSION`. New table (the ledger proper):

```sql
CREATE TABLE IF NOT EXISTS index_units (
    unit_id         TEXT PRIMARY KEY,   -- e.g. 'zotero-<itemKey>-attachment-<attID>', 'obsidian-<relpath>'
    identity_field  TEXT NOT NULL,      -- parent source identity (the §source-identity rule)
    identity_value  TEXT NOT NULL,
    unit_kind       TEXT NOT NULL,      -- parent_meta | note | attachment | annotation | vault_file
    source_fingerprint TEXT NOT NULL,   -- opaque; compared, never parsed
    indexed_grain   TEXT DEFAULT '',    -- chunk grain this unit was indexed at (future-proofs re-grain)
    indexed_at      TEXT DEFAULT '',
    chunk_count     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_units_identity ON index_units(identity_field, identity_value);
CREATE INDEX IF NOT EXISTS idx_units_kind ON index_units(unit_kind);
```

- `index_units` is the authority for reconciliation. `chunks`/`sources` remain (chunk identity +
  selection metadata); `vault_files` is **subsumed** by `index_units` where `unit_kind='vault_file'`
  (migrate, then drop, or keep as a view during transition).
- The library-version cursor moves to `meta`: `zotero_item_version`, `zotero_fulltext_version`.
- `zotero_delta_state.json` is deleted; a one-time migration seeds `meta` from it if present.

## 5. Interfaces (the seams)

```python
# src/sources/base.py
class DataSource:
    def enumerate_state(self) -> dict[str, UnitState]: ...
        # UnitState = (unit_kind, identity_field, identity_value, fingerprint)
        # Read-only; source-coupled; cheap. May use a cursor hint to prefilter.

# src/registry.py  (the ledger)
class SourceRegistry:
    def get_unit_states(self) -> dict[str, str]: ...          # unit_id -> fingerprint
    def record_unit_states(self, states, *, meta=None): ...    # upsert, durable, w/ cursor
    def delete_units(self, unit_ids): ...                      # surgical, by unit

# src/indexing/planner.py  (new; source-agnostic)
def reconcile(world: dict[str, UnitState], ledger: dict[str, str]) -> WorkPlan: ...
    # WorkPlan = {creates: [UnitState], updates: [UnitState], deletes: [unit_id]}
```

The planner is pure (no I/O) and unit-tested with synthetic states — the heart of correctness.

## 6. The sub-item precision win falls out for free

The earlier "don't re-embed 12,000 pages of Jung because I added a note" problem is **not a
special case** under this design — it is what the diff produces when the ledger is keyed at unit
grain:

- add a note → `note` unit new (create one note); `attachment` fingerprint unchanged (skip
  fulltext); `parent_meta` fingerprint may bump (metadata-only refresh, §7).
- edit an annotation comment → one `annotation` unit updates; attachment untouched.
- replace/re-OCR the PDF → `attachment` `storageHash`/fulltext-version differs → re-embed *that*
  attachment only.

No "classify why the parent changed" heuristic; the granularity is a property of the ledger key.

## 7. Metadata-only changes (tags, collections, fields)

A `parent_meta` fingerprint change with **no text-bearing unit change** means chunk *text* is
unchanged but chunk *metadata* (tags/collections/selection fields) may be stale. Two options,
config-gated:

- **Metadata-only update**: Chroma `collection.update(ids, metadatas=...)` + register `sources`
  refresh — **no re-embed**. Preferred.
- **Ignore until next text change**: cheapest; selection facets on that source lag. Acceptable
  fallback if the metadata-update path is deferred.

This is the third leg of the Jung win: a tag edit on a huge item costs a metadata `update`, not a
re-embed.

## 8. Correctness, durability, parity

- **Prove parity before granularity (Phase 2 gate).** The reconciler must first reproduce *today's*
  change set at parent grain on a known corpus, validated against the existing delta path, before
  granular execution is switched on. De-risks the cutover.
- **Durability**: ledger writes use the W5 `write_json_durable`/atomic-SQLite discipline; a unit is
  marked indexed only after its vectors are committed to Chroma (write order: embed → Chroma upsert
  → ledger record, same batch). Kill-9 mid-run leaves an un-recorded unit that the next reconcile
  re-plans — safe (idempotent upsert by stable id).
- **Deletion detection** is now first-class (ledger-minus-world), not dependent on Zotero's
  `/deleted` endpoint being reachable — the SQLite enumerate already sees absence.
- **Source-identity rule preserved**: units roll up to a parent identity for grouping; we refresh a
  *subset* of a parent's chunks, identity unchanged.

## 9. Phased plan (plan → implement → test, each phase shippable)

> Principle: every phase is additive and independently testable; behavior only changes at P3.

**P0 — Ledger foundation (no behavior change).**
- Add `index_units` table + migration (`SCHEMA_VERSION`++); add register `meta` cursor keys.
- Implement `get_unit_states` / `record_unit_states` / `delete_units`.
- *Test*: migration is additive on an existing registry; ledger round-trips; existing suite green.

**P1 — Adapter state enumeration.**
- `DataSource.enumerate_state()`; Zotero builds it from SQLite (parent `dateModified`, attachment
  `storageHash`+`storageModTime`, note/annotation `dateModified`); Obsidian adapts `get_file_states`.
- Keep `/items?since=` as a cursor prefilter (stored in `meta`).
- *Test*: enumerated unit set + fingerprints match fixtures for the `test_zotero`/`test_obsidian`
  corpora; a touched note changes exactly one fingerprint.

**P2 — Reconciliation planner (parity gate).**
- Pure `reconcile(world, ledger) -> WorkPlan` in `src/indexing/planner.py`.
- Run it in *shadow* alongside the existing delta path; assert the create/delete *parent* set
  matches today's `changed_item_keys` on the test corpora.
- *Test*: unit tests on synthetic states (create/update/delete/no-op); shadow-parity on fixtures.

**P3 — Granular execution (the Jung win) + retire sidecar.**
- Pipeline consumes `WorkPlan`: partial-item processing (only changed units), surgical
  `delete_units`, ledger recorded per unit with vectors.
- Attachment fingerprint short-circuit; metadata-only update path (§7).
- Delete `zotero_delta_state.json`; one-time seed of `meta` cursor from it; subsume `vault_files`.
- *Test*: "add note to large item" re-embeds only the note (assert fulltext chunk ids unchanged);
  tag edit triggers metadata-only update, zero embeds; delete is detected from absence; resume after
  kill-9 mid-plan re-plans cleanly.

**P4 — Observability & repair.**
- `index_status` / `sources.py status` surface ledger drift (units in ledger absent in Chroma and
  vice-versa); `build_registry.py` backfills `index_units` from existing chunks (fingerprints
  best-effort; `attachment` storageHash recoverable from Zotero by key).
- *Test*: drift is reported and repairable; backfill is resumable.

**P5 — W8 selection metadata + annotation refinements (ride-along).**
- Apply `SPEC_W8_REGISTER_METADATA.md` (item_type/doi/abstract/tags/venue/language + `has_comment`)
  on the same `record_chunks`/registry surface touched by P0–P3.
- *Test*: per that spec's acceptance.

## 10. Acceptance (whole change)

- An update run computes its work purely from `enumerate_state` + the register ledger; Chroma is
  never queried to decide what changed.
- Adding a note/annotation/tag to a 12,000-page item does **not** re-embed its fulltext.
- Deletions are detected from ledger-minus-world without relying on `/deleted`.
- `zotero_delta_state.json` no longer exists; the cursor lives in `meta`.
- Parity gate (P2) held before P3 cutover; the §3b functional profile does not regress.

## 11. Open questions

- ~~**`storageHash` availability/coverage**~~ **RESOLVED 2026-06-17** (measured on live
  `zotero.sqlite`, 544 attachments): `storageHash` covers only **~43%** (236/544); `storageModTime`
  has **identical** coverage (both null together), so it is *not* a usable fallback;
  `lastProcessedModificationTime` is unused. **Decision: the attachment fingerprint is composite —
  `storageHash` when present, else `mtime:size` from the resolved file** (the adapter already
  `stat()`s `storage:` files in `_collect_attachment_tasks`), mirroring the Obsidian `vault_file`
  pattern. This always yields a value for any attachment we actually index. Linked/remote items with
  no local file are not indexed, so they need no fingerprint.
- **`parent_meta` fingerprint = item `dateModified`** for P1 (parity with today's delta semantics,
  which the P2 parity gate checks against). **Known caveat to revisit in P3**: a Zotero
  *collection-membership-only* change may not bump `dateModified`; if collection facets must update
  on such edits, widen the `parent_meta` fingerprint to a hash of `(dateModified, sorted tags,
  sorted collections)`. Tag edits do bump `dateModified`, so tags are covered.
- **Cursor vs full-enumerate cost** on the full ~8,200-item library — measure; the cursor prefilter
  may be unnecessary if a full SQLite enumerate is already sub-second.
- **`vault_files` transition** — subsume into `index_units` outright vs keep as a compatibility view
  for one release.

### Unit-id scheme (decided 2026-06-17)

Stable across runs (uses permanent Zotero item keys, not rowids):
`zotero:{parent_key}:meta` · `:note:{note_key}` · `:attachment:{attachment_key}` ·
`:annotation:{annotation_key}`; Obsidian `obsidian:{relative_path}`. P3 must ensure note/annotation
chunks carry their child *key* (today they carry the rowid `note_id`/`annotation_id`) so units map
to chunks for surgical delete.
