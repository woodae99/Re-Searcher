# Implementation Handoff — Index Ledger (W10) Phases P2–P5

**Audience**: an engineer/agent starting cold. **Branch**: `v0.6-rebuild`. **Date**: 2026-06-17.
**Read first, in order**: `docs/SPEC_V0.6_REBUILD.md` §3a/§3d/W10 → `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`
(architecture + phase summary) → this doc (line-level implementation) → `docs/SPEC_W8_REGISTER_METADATA.md`
(P5). `CLAUDE.md` has repo conventions; honor CLI/MCP parity and registry-sync rules.

This document is now a **live implementation handoff**. P0–P2 are complete; **P3 core execution is
implemented behind `indexing.ledger.execute`**, with important cleanup/acceptance items still open
(sidecar retirement, `vault_files` transition, direct integration dry-run). Each phase should remain
independently shippable. Current focused command:
`.venv/bin/python -m pytest tests/unit --ignore=tests/unit/test_mcp_server.py -q`.

---

## ⮕ CODEX — START HERE (state as of 2026-06-17, commit `5339015`)

**Everything below is on `v0.6-rebuild` and committed.** Run the full check first to confirm a green
baseline (expect **221 passed**; the **only** acceptable failures are the 7 documented ones in
`tests/test_rerank_json.py` + `tests/test_resumable_indexing.py`, which need an OpenAI-compatible API
key — ignore them):
`.venv/bin/python -m pytest tests/ --ignore=tests/integration --ignore=tests/pipeline -q`

**Done & committed**
- P0 ledger schema + register methods; P1 adapter `enumerate_state`; P2 `src/reconcile.py` + shadow
  parity wired into `pipeline.py`; P3 granular execution behind `indexing.ledger.execute`.
- vLLM embedding backend + cross-encoder rerank are now integrated on this branch (was a separate
  branch; merged). Embedder `context_length`/vLLM `max_model_len` = 8192 (must exceed the 7000
  oversize guard — do not lower).
- Repo is consolidated: only `main` + `v0.6-rebuild` exist; stale `claude/*` branches and worktrees
  removed.

**Decisions already made (do not relitigate)**
- `indexing.ledger.execute` stays **`false`** (legacy delta is primary; the reconciler runs in
  shadow). The cutover to `true` + sidecar retirement happens **only after** the chapter-4 mission
  validates shadow parity — that is **not** a Codex task; leave the flag and the sidecar alone.
- Annotation `color`/`type` capture is **dropped** (single-colour corpus). Only `has_comment` (P5).

**Your tasks, in order: P5 then P4** (P5 is lower-risk and unblocks the chapter-4 metadata needs)
1. **P5 — W8 selection metadata + `has_comment`** (§ "P5" below + `docs/SPEC_W8_REGISTER_METADATA.md`,
   which is authoritative for the column/extraction/filter details). Note: `chunks` is already at
   `SCHEMA_VERSION = 3` with child-key columns, so the `sources` selection columns bump to **4**.
   This shares the `record_chunks`/`list_sources_payload` surface — keep CLI/MCP parity (add the
   same filters to `scripts/sources.py` and `src/mcp_server.py`, format in `src/mcp_formatters/`).
2. **P4 — ledger drift observability** (§ "P4" below). Prioritise the drift report in
   `index_status` / `scripts/sources.py status`; the `build_registry.py` backfill of `index_units`
   is **optional** (the fresh v0.6 build populates the ledger natively) — only do it if time allows.

**Hard rules (from `CLAUDE.md`)**
- Any Chroma write/delete updates the registry **and** the ledger in the same step.
- Every new config knob lands in `config.example.yaml` with a default and is printed by preflight.
- Thin MCP/CLI wrappers; business logic stays in pipeline/registry; keep CLI↔MCP parity.
- Add tests for each phase; do not mark a phase done with failing tests. Do not commit secrets or
  the gitignored `output/`/Chroma data. Commit message trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` is the house style for AI commits — use
  your own attribution as appropriate, but keep messages scoped per phase.

**When done**: leave the work committed on `v0.6-rebuild` with a clean tree and green suite; Colin
will return here for a review.

Known test caveats as of 2026-06-17:
- `tests/unit/test_mcp_server.py` times out independently after its first test in this workspace.
- Older broad-suite caveats still apply for API-key-dependent tests outside `tests/unit`
  (`tests/test_rerank_json.py`, `tests/test_resumable_indexing.py`).

---

## 0. Current state (what P0–P3 core already give you)

**P0 — `src/registry.py`** (`SourceRegistry`):
- Table `index_units(unit_id PK, identity_field, identity_value, unit_kind, source_fingerprint,
  indexed_grain, indexed_at, chunk_count)` + indexes.
- `SCHEMA_VERSION = 3` in the working tree. Version 3 currently covers the ledger table plus
  child-key columns on `chunks` (`zotero_key`, `attachment_key`, `note_key`, `annotation_key`).
  **Future P5 selection-metadata columns should bump to 4**, not reuse 3.
- Methods: `get_unit_states() -> {unit_id: fingerprint}`, `record_unit_states(units, meta_updates=None)`,
  `delete_units(unit_ids)`, `delete_units_for_source(identity_field, identity_value)`. Ledger is
  cleared/kept-consistent in `reset()`, `delete_source_chunks()`, `delete_sources_like()`.
- New P3 helpers: `chunk_records_for_source(...)`, `delete_chunks_matching(...)`.

**P1 — `src/sources/`**:
- `base.py`: `@dataclass(frozen=True) UnitState(unit_id, identity_field, identity_value, unit_kind,
  fingerprint)` and default `DataSource.enumerate_state() -> {}`.
- `obsidian.py`: `enumerate_state()` → one `vault_file` unit per note; `unit_id="obsidian:<relpath>"`,
  identity `("source_id","obsidian-<relpath>")`, fingerprint `mtime:size`.
- `zotero.py`: `enumerate_state()` → set-based queries producing `parent_meta` / `note` /
  `attachment` / `annotation` units, all rolled up to the parent `zotero_key`. Unit-id scheme:
  `zotero:<parentKey>:meta` · `:note:<childKey>` · `:attachment:<childKey>` · `:annotation:<childKey>`.
  Fingerprints: items → `mod:<dateModified>`; attachments → `_attachment_fingerprint()` = `hash:<storageHash>`
  if present else `mtime:<mtime>-<size>` of the resolved file (else the unit is omitted — not indexable).
- Verified on the live library: 1787 units; attachment fingerprints 235 `hash:` / 308 `mtime:`.

**P2 — `src/reconcile.py`**:
- `WorkPlan`, `reconcile(world, ledger)`, and `build_work_plan(sources, registry)` are implemented.
- Pipeline shadow logging exists via `indexing.ledger.shadow` (default true in `config.example.yaml`)
  when the legacy delta path is used.
- Verified on a temp copy of the disposable Zotero DB (1787 units): parent metadata edit + new note +
  attachment fingerprint change + top-level deletion produced exactly `creates=1`, `updates=2`,
  `deletes=1`, `unchanged=1784`; ledger touched parents matched SQLite delta modification parents,
  and ledger deletion matched SQLite deleted parent.

**P3 core — behavior-changing path**:
- `indexing.ledger.execute` is available and true in `config.example.yaml`. With this enabled,
  non-force update runs call `_run_ledger_work_plan()` before the old delta path.
- Resume with ledger execution enabled re-plans from the ledger and continues; legacy resume remains
  for `indexing.ledger.execute: false`.
- `src/storage/chroma.py`: `delete_where` normalizes multi-field equality filters into Chroma `$and`;
  `update_metadata(ids, metadatas)` added for no-re-embed parent metadata updates.
- `src/sources/zotero.py`: `fetch_item_documents(item_key, *, kinds=None, attachment_keys=None)` and
  selector plumbing added; emitted chunks now carry `note_key` / `annotation_key` where applicable.
- `src/pipeline.py`: core plan execution implemented:
  - changed/new attachment units delete and fetch only that `attachment_key`;
  - changed/new/deleted notes/annotations use parent kind-grain refresh for `zotero_note` +
    `zotero_annotation`;
  - parent-meta-only changes update Chroma metadata and registry chunks, with zero embeds;
  - parent deletes remove all parent chunks and ledger rows;
  - Obsidian create/update/delete paths route through the plan helpers (needs more direct tests).

**Tests added/updated**:
- `tests/unit/test_reconcile.py` (P2 pure diff + shadow parity fixture).
- `tests/unit/test_chroma_storage.py` (Chroma filter normalization + metadata sanitizer).
- `tests/unit/test_ledger_execution.py` (P3 headline behavior).
- `tests/unit/test_registry.py` (schema v3 + child-key chunk delete helpers).
- `tests/unit/test_trustworthy_updates.py` (partial Zotero fetch + child keys).

Latest verified command:
`timeout 180s .venv/bin/python -m pytest tests/unit --ignore=tests/unit/test_mcp_server.py -q`
→ **133 passed**.

---

## P2 — Reconciliation planner (parity gate)

**Status 2026-06-17: COMPLETE.** Keep this section as design reference; implementation lives in
`src/reconcile.py` (not `src/indexing/planner.py`).

**Goal**: a pure diff producing the work plan, plus a *shadow* parity check against today's delta
path before any behavior changes.

### P2.1 Module location (IMPORTANT correction)
`src/indexing.py` is a **file**, so `src/indexing/planner.py` (as the architecture spec sketched)
would shadow it. **Create `src/reconcile.py` instead.**

### P2.2 `WorkPlan` + `reconcile`
```python
# src/reconcile.py
from dataclasses import dataclass, field
from typing import Dict, List
from src.sources.base import UnitState

@dataclass
class WorkPlan:
    creates: List[UnitState] = field(default_factory=list)   # in world, not in ledger
    updates: List[UnitState] = field(default_factory=list)   # in both, fingerprint differs
    deletes: List[str]       = field(default_factory=list)   # unit_ids in ledger, not in world
    unchanged: int = 0

    def is_empty(self) -> bool:
        return not (self.creates or self.updates or self.deletes)

    def touched_identities(self) -> set[tuple[str, str]]:
        """(identity_field, identity_value) for every create/update — the sources to refresh."""
        return {(u.identity_field, u.identity_value) for u in (*self.creates, *self.updates)}

def reconcile(world: Dict[str, UnitState], ledger: Dict[str, str]) -> WorkPlan:
    plan = WorkPlan()
    for uid, state in world.items():
        prev = ledger.get(uid)
        if prev is None:
            plan.creates.append(state)
        elif prev != state.fingerprint:
            plan.updates.append(state)
        else:
            plan.unchanged += 1
    world_ids = set(world)
    plan.deletes = [uid for uid in ledger if uid not in world_ids]
    return plan
```
Pure, no I/O — this is the correctness core; unit-test it exhaustively.

### P2.3 Orchestration helper
```python
# src/reconcile.py
def build_work_plan(sources, registry) -> WorkPlan:
    """Merge each enabled source's enumerate_state() and reconcile against the ledger."""
    world = {}
    for s in sources:
        if s.is_enabled():
            world.update(s.enumerate_state())
    return reconcile(world, registry.get_unit_states())
```
Merging is safe because unit-id namespaces don't collide (`zotero:` vs `obsidian:`).

### P2.4 Shadow parity (the gate — do NOT skip)
Add a **read-only** path in the pipeline (behind a config flag, e.g. `indexing.ledger.shadow: true`,
default true for this phase) that, at the start of an update run, computes `build_work_plan(...)`
and **logs** the parent-grain change set alongside the existing delta path's `changed_item_keys`
without acting on either differently. Goal: confirm the reconciler reproduces today's decisions
before P3 lets it drive execution.

Expected relationship (document any divergence in the log):
- **Modifications/creates**: reconciler's `{identity_value for zotero creates+updates}` should equal
  the existing `_fetch_changed_parent_item_keys_sqlite` result for the same state.
- **Deletions**: reconciler may catch *more* (ledger-minus-world sees absence directly), which is an
  improvement, not a regression. Note these explicitly.

### P2.5 Tests
- `tests/unit/test_reconcile.py`: synthetic states — create-only, update-only, delete-only, no-op,
  mixed; `touched_identities()`; empty world vs full ledger (all deletes) and vice-versa (all creates).
- Shadow parity on the `test_enumerate_state` fixture: seed ledger from an initial `enumerate_state`,
  mutate the fixture (touch a note; delete an item), assert reconciler's parent change-set matches
  `_fetch_changed_parent_item_keys_sqlite` for modifications and is a superset for deletions.

### P2.6 Acceptance
Pure reconcile fully unit-tested; shadow parity logged and confirmed on the test corpora; **no
behavior change** (planner does not yet drive deletes/embeds).

**Acceptance evidence**:
- `tests/unit/test_reconcile.py` passes.
- Disposable Zotero-copy representative mutation run passed:
  - create: `zotero:<parent>:note:<newKey>`
  - updates: one `attachment`, one `parent_meta`
  - delete: one parent `meta`
  - ledger touched parent set == SQLite delta modification set; SQLite deleted set <= ledger deleted set.

---

## P3 — Granular execution + retire sidecar (the behavior change; the Jung win)

**Status 2026-06-17: CORE IMPLEMENTED, NOT FULLY CLOSED.**

Implemented:
- Ledger execution path behind `indexing.ledger.execute`.
- Storage metadata update and Chroma multi-field delete normalization.
- Zotero partial fetch selectors and child-key metadata.
- Registry child-key chunk columns/helpers.
- Unit tests for note/attachment/meta/delete headline behavior.

Still open before calling P3 fully shipped:
- Retire `zotero_delta_state.json` and remove/seed sidecar code.
- Migrate/subsume `vault_files` into `index_units`, or explicitly keep compatibility shims.
- Add direct Obsidian P3 execution tests.
- Add resume/crash test for interruption after Chroma upsert but before `record_unit_states`.
- Run an integration-style dry-run over the disposable Zotero/Obsidian test subset with a disposable
  Chroma collection.

**Goal**: the pipeline acts on the `WorkPlan` — only changed units are re-embedded, deletes are
surgical, metadata-only changes skip embedding, and `zotero_delta_state.json` is retired.

### P3.1 Execution-granularity rule (KEY DESIGN — read carefully)
Do **not** require per-note/per-annotation surgical deletes (their chunks carry rowids, not keys).
Use a **hybrid grain** that captures the entire benefit, because the only expensive unit is the
attachment/fulltext:

Group the plan's creates/updates/deletes by parent identity. For each touched parent:
- **`attachment` unit changed/created/deleted** → precise: delete that attachment's fulltext chunks
  by `attachment_key` (it IS in chunk metadata, `source_type="zotero_fulltext"`), then re-extract +
  re-embed *only that attachment*. Other attachments and all notes/annotations untouched.
- **`note` and/or `annotation` unit changed** → kind-grain: delete the parent's `zotero_note` +
  `zotero_annotation` chunks and re-index the parent's notes+annotations (cheap; small text).
- **only `parent_meta` changed** (tags/collections/fields; no text-bearing unit) → **metadata-only
  update, no embed** (§P3.4).

Rationale: precise where it's expensive (fulltext), kind-grain where it's cheap (children). Avoids
needing child-key surgical deletes for notes/annotations. **Implementation note**: P3 now stores
`note_key`/`annotation_key` in chunk metadata anyway so documents can map back to ledger units and
future surgical child deletes remain possible. Current execution still uses kind-grain for
notes/annotations.

### P3.2 Storage: surgical + metadata-only (`src/storage/chroma.py`)
**Status: implemented.**

- Confirm `delete_where` supports `{"zotero_key": K, "source_type": "zotero_fulltext",
  "attachment_key": A}` (Chroma `$and` of equality predicates — wrap multiple keys correctly; Chroma
  requires `{"$and":[{...},{...}]}` for >1 field). Add a helper if the current `delete_where` passes
  the dict through raw.
- Add `update_metadata(ids: list[str], metadatas: list[dict])` wrapping `collection.update(...)`
  (sanitize metadata the same way `add_documents` does). This is the no-re-embed path.

### P3.3 Source: partial fetch (`src/sources/zotero.py`)
**Status: implemented.**

Add a selective fetch so the pipeline can fetch a subset of an item's units. Extend `_process_item`
(and a thin public entry like `fetch_item_documents(item_key, *, kinds=None, attachment_keys=None)`)
with selectors:
- `kinds`: subset of `{"note","attachment","annotation"}` to emit.
- `attachment_keys`: when fetching attachments, restrict to these keys.
The existing `_process_notes`/`_process_attachments`/`_process_annotations` already key off the item;
gate them on the selector. Keep the full path (no selector) working for full rebuilds.

### P3.4 Pipeline orchestration (`src/pipeline.py`)
**Status: core implemented behind `indexing.ledger.execute`; legacy delta path remains for
`execute: false`.**

Replace the delta-collect→delete→reindex flow (currently ~lines 196–390, plus
`_collect_zotero_delta_changes`, `_collect_obsidian_delta_changes`, `_save/_load_delta_state`,
`_persist_vault_state`) with a plan-driven flow:

1. `plan = build_work_plan(self.sources, self.registry)`. With `indexing.ledger.execute: true`,
   resume re-plans from the ledger and relies on stable chunk IDs/progress to converge; the legacy
   "skip delta discovery on resume" behavior remains only for `execute: false`.
2. **Deletes**: for each delete unit_id, map to chunks and remove from Chroma + registry
   (`delete_units` + chunk delete by the unit's grain), batched.
3. **Creates/updates**: group by parent; for each parent fetch only the needed docs (§P3.3), chunk,
   embed, store; then `record_chunks(...)` **and** `record_unit_states([...])` with the new
   fingerprints — fingerprint recorded **only after** vectors are committed to Chroma (durability).
4. **Metadata-only parents**: fetch fresh metadata, `chroma.update_metadata(ids, metadatas)` for the
   parent's chunk ids (from the registry `chunks` table), refresh registry `sources`, and
   `record_unit_states` to bump the `parent_meta` fingerprint — **no embed call**.
5. Obsidian rides the same flow: `vault_file` create/update → fetch+store changed paths; delete →
   remove. This subsumes `_collect_obsidian_delta_changes`.

Write order per unit (durability, W5): embed → Chroma upsert → `record_chunks` → `record_unit_states`.
A kill-9 between upsert and `record_unit_states` leaves an un-recorded unit that the next reconcile
re-plans; upserts are idempotent by stable id, so re-doing is safe.

Implementation caveat:
- `_record_ledger_unit_states()` records unit fingerprints after `_process_batches()` completes.
  The intended durability property holds for completed runs, but the explicit crash/resume test is
  still open. Verify that a kill between Chroma upsert and ledger record re-plans and converges with
  stable IDs and no duplicate registry rows.

### P3.5 Retire the sidecar + decide the cursor
**Status: not done.**

- Delete `zotero_delta_state.json` usage: remove `_load_delta_state`/`_save_delta_state` and the
  `state_file` config. One-time migration: if the file exists on first P3 run, seed register `meta`
  (`zotero_item_version`, `zotero_fulltext_version`) from it, then ignore it.
- Subsume `vault_files`: migrate existing rows into `index_units` (kind `vault_file`) on first run;
  keep `get_vault_state`/`set_vault_state_entries` as thin shims over `index_units` for one release,
  or remove their callers entirely.
- **Cursor decision**: full `enumerate_state()` of the live library was effectively instant (1787
  units). **Measure on the full ~8,200-item library**; if sub-second, **drop the `/items?since=`
  cursor entirely** — full-enumerate every run is the simplest correct thing. Keep the cursor only
  if enumeration proves materially slow; if kept, it is a prefilter, never the source of truth.

### P3.6 `parent_meta` fingerprint caveat (carry from spec §11)
P1 uses `mod:<dateModified>`. Tag edits bump `dateModified` (caught), but a *collection-membership-only*
change may not. If chapter-4 testing shows collection facets going stale, widen the `parent_meta`
fingerprint in `zotero.py` to a hash of `(dateModified, sorted(tags), sorted(collections))`. Leave a
TODO; don't pre-build unless the test surfaces it.

### P3.7 Tests
- **Add-note-to-large-item** (the headline): implemented in `tests/unit/test_ledger_execution.py`.
  Fixture parent with a fulltext attachment (hash
  unchanged) + add a note. Use a fake/counting embedder; assert embed is called for the note text
  only and the attachment's fulltext chunk ids are unchanged in Chroma.
- **Tag edit / parent_meta only**: implemented in `tests/unit/test_ledger_execution.py`; asserts
  **zero** embed calls, `update_metadata` called, registry
  `sources` row reflects new tags.
- **Attachment replaced** (storageHash changes): implemented in `tests/unit/test_ledger_execution.py`;
  asserts changed attachment only is fetched/embedded and sibling
  attachments + notes untouched.
- **Deletion**: implemented in `tests/unit/test_ledger_execution.py`; item removed from fixture →
  chunks + units removed from Chroma + registry.
- **Resume after interruption**: TODO. Simulate crash between Chroma upsert and `record_unit_states`;
  assert next run re-plans and converges (no duplicates — idempotent ids).
- **Obsidian**: TODO direct P3 execution tests. The plan helpers route vault create/update/delete,
  but this needs explicit test coverage.

### P3.8 Acceptance
Adding a note/annotation/tag to a 12,000-page item does **not** re-embed its fulltext; deletions are
detected from ledger-minus-world without `/deleted`; `zotero_delta_state.json` is gone; the §3b
functional profile does not regress; full delta run on the test corpora matches P2 shadow predictions.

**Current acceptance state**:
- Unit-level Jung win is covered.
- Sidecar removal is not complete.
- Full disposable-collection run has not yet been performed after P3 execution wiring.

---

## P4 — Observability & repair

**Goal**: make ledger health visible and repairable.

### P4.1 Drift reporting (`src/registry.py` + `index_status` + `scripts/sources.py status`)
Add a ledger-vs-chunks consistency check, surfaced on both MCP and CLI (parity rule):
- **Chunkless units**: `index_units` rows of a text-bearing kind (`note`/`attachment`/`annotation`)
  with no chunks in `chunks` for that identity → under-indexed.
- **Orphan chunks**: chunks whose identity has no `index_units` row → ledger gap (pre-ledger data).
- Optionally compare `index_units` count/identities to Chroma (the existing status already drift-checks
  registry vs Chroma; extend the same surface).
Report counts + a sample; never scan the collection for enumeration (registry-first rule).

### P4.2 Backfill (`scripts/build_registry.py` / `src/registry.py`)
Needed **only** for applying the ledger to a pre-ledger collection (e.g. current production). For the
**v0.6 fresh build the ledger is populated natively at index time, so backfill is optional.** If
implemented: scan existing chunks → derive `index_units` (identity from chunk metadata; unit_kind
from `source_type`; attachment_key→attachment unit). Fingerprints best-effort: read current Zotero
`storageHash`/`dateModified` by key so the first post-backfill delta reconciles cleanly. Resumable
(reuse the existing checkpointed-offset pattern).

### P4.3 Tests
Drift report flags a synthetic chunkless unit and an orphan chunk; backfill (if built) populates
`index_units` and is resumable.

### P4.4 Acceptance
`sources.py status` / `index_status` report ledger drift; (if built) backfill populates the ledger
and resumes after interruption.

---

## P5 — W8 selection metadata + annotation refinements (ride-along)

**Goal**: apply `docs/SPEC_W8_REGISTER_METADATA.md` on the same `record_chunks`/registry surface
touched by P0–P3, so there's one migration, not two.

- **Register columns** (additive migration, same pattern as P0): `item_type`, `doi`, `abstract`,
  `tags`, `venue`, `language` on `sources`; lift in `record_chunks`; filter in `list_sources_payload`;
  surface in `mcp_formatters` + `scripts/sources.py` (parity). **Bump `SCHEMA_VERSION` to 4**,
  because P3 already uses 3 for child-key chunk columns.
- **Extraction** (`src/sources/zotero.py` `_get_item_metadata`): resolve `typeName` → `item_type`
  (only field not already in `**fields`); the rest already arrive via the `**fields` spread; ensure
  `tags` survive to `record_chunks`.
- **Annotation `has_comment`** (`_process_annotations`): set `"has_comment": bool(comment.strip())`.
- **`color`/`type`: DROPPED** — measured single-color/single-type corpus (spec §1.2).
- Tests + acceptance per `SPEC_W8_REGISTER_METADATA.md` §2.7/§1.3.

---

## Operational considerations (decide "as we go"; do not hardcode)

These are flagged for the dev→production transition. The handoff agent should **not** bake in paths
or hosts; everything stays config-driven (W6).

1. **What to commit before this dev phase** — see §git below. The ledger P0/P1 work + these specs
   are a clean base. Unrelated pre-existing working-tree changes (MCP formatters, README/CHANGELOG,
   VNEXT→LEGACY doc renames) predate this session and should be reviewed/committed separately.
   `tests/fixtures/corpora/` (the dev/test mission data) is untracked — decide whether to commit the
   manifest while gitignoring any copyrighted PDFs.
2. **ChromaDB location** — the `chromadb` **client library** in the venv is correct (a pip dep);
   keep it. The **server process + persistent data** must live **outside the repo** in production.
   Dev data currently sits at the gitignored `output/sparky-test/chroma` (fine for dev). Production
   (W9): run Chroma as a `systemd` service on Sparky with a stable data dir (e.g.
   `~/.local/share/re-searcher/chroma` or `/var/lib/re-searcher/chroma`), reached via the
   config-driven `storage.endpoint`. The repo never holds production vectors.
3. **Dev→prod cutover** — the acceptance run is the **chapter-4 process-research mission** over the
   Zotero/Obsidian test subset (`docs/SPEC_V0.6_REBUILD.md` §6). On success, retire the dev Chroma
   (scrub or point `storage.collection_name`/`endpoint` at a fresh production instance — blank-build,
   no migration, per W9).
4. **Production source access** — the ledger's `enumerate_state()` is built on **local file reads**
   (`zotero.sqlite` + storage dir; vault files), which matches the W9 decision to **sync the data
   locally to Sparky** (overnight-autonomous, not LAN-capped). Keep that path. The REST/API-to-live
   alternative would require an API-backed `enumerate_state` variant and is LAN/host-dependent — only
   pursue if the local-sync route is abandoned. Caveat (already in W9): snapshot `zotero.sqlite`
   before a run to avoid reading a mid-write state; `enumerate_state` opens it read-only.

---

## Sequencing & risk notes

- **P2 is complete.** Keep the shadow parity path available while P3 beds in, especially for
  disposable/full-corpus dry runs.
- **P5 can land with P3** (shared migration) or after; it's independent of the reconciliation logic.
- **P4 backfill is optional for the fresh v0.6 build** — prioritize drift reporting over backfill.
- Keep every new knob in `config.example.yaml` with a default and print it in preflight (W6).
- Registry-sync rule (CLAUDE.md): any Chroma write/delete updates the registry **and now the ledger**
  in the same step.
- `test_mcp_server.py` timeout should be investigated separately; do not treat it as a P3 regression
  unless it starts failing differently after MCP changes.
