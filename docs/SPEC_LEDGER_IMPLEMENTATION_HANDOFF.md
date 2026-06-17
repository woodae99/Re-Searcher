# Implementation Handoff — Index Ledger (W10) Phases P2–P5

**Audience**: an engineer/agent starting cold. **Branch**: `v0.6-rebuild`. **Date**: 2026-06-17.
**Read first, in order**: `docs/SPEC_V0.6_REBUILD.md` §3a/§3d/W10 → `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`
(architecture + phase summary) → this doc (line-level implementation) → `docs/SPEC_W8_REGISTER_METADATA.md`
(P5). `CLAUDE.md` has repo conventions; honor CLI/MCP parity and registry-sync rules.

This document specs the work **not yet done**. P0 and P1 are **complete, tested, on the working
tree** (see §0). Each phase is independently shippable; behavior only changes at **P3**. Run tests
with `.venv/bin/python -m pytest tests/ --ignore=tests/integration --ignore=tests/pipeline -q`
(known pre-existing failures: `test_rerank_json.py`, `test_resumable_indexing.py` — need an
OpenAI-compatible key; ignore them).

---

## 0. Current state (what P0/P1 already give you)

**P0 — `src/registry.py`** (`SourceRegistry`):
- Table `index_units(unit_id PK, identity_field, identity_value, unit_kind, source_fingerprint,
  indexed_grain, indexed_at, chunk_count)` + indexes. `SCHEMA_VERSION = 2`; migration is additive
  (CREATE IF NOT EXISTS on every init).
- Methods: `get_unit_states() -> {unit_id: fingerprint}`, `record_unit_states(units, meta_updates=None)`,
  `delete_units(unit_ids)`, `delete_units_for_source(identity_field, identity_value)`. Ledger is
  cleared/kept-consistent in `reset()`, `delete_source_chunks()`, `delete_sources_like()`.

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

**Tests**: `tests/unit/test_registry.py` (ledger), `tests/unit/test_enumerate_state.py` (P1).

---

## P2 — Reconciliation planner (parity gate)

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

---

## P3 — Granular execution + retire sidecar (the behavior change; the Jung win)

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
adding child keys to chunk metadata. Record the per-parent grain decision in logs.

### P3.2 Storage: surgical + metadata-only (`src/storage/chroma.py`)
- Confirm `delete_where` supports `{"zotero_key": K, "source_type": "zotero_fulltext",
  "attachment_key": A}` (Chroma `$and` of equality predicates — wrap multiple keys correctly; Chroma
  requires `{"$and":[{...},{...}]}` for >1 field). Add a helper if the current `delete_where` passes
  the dict through raw.
- Add `update_metadata(ids: list[str], metadatas: list[dict])` wrapping `collection.update(...)`
  (sanitize metadata the same way `add_documents` does). This is the no-re-embed path.

### P3.3 Source: partial fetch (`src/sources/zotero.py`)
Add a selective fetch so the pipeline can fetch a subset of an item's units. Extend `_process_item`
(and a thin public entry like `fetch_item_documents(item_key, *, kinds=None, attachment_keys=None)`)
with selectors:
- `kinds`: subset of `{"note","attachment","annotation"}` to emit.
- `attachment_keys`: when fetching attachments, restrict to these keys.
The existing `_process_notes`/`_process_attachments`/`_process_annotations` already key off the item;
gate them on the selector. Keep the full path (no selector) working for full rebuilds.

### P3.4 Pipeline orchestration (`src/pipeline.py`)
Replace the delta-collect→delete→reindex flow (currently ~lines 196–390, plus
`_collect_zotero_delta_changes`, `_collect_obsidian_delta_changes`, `_save/_load_delta_state`,
`_persist_vault_state`) with a plan-driven flow:

1. `plan = build_work_plan(self.sources, self.registry)` (skip in pure resume — checkpoint
   continuity wins, as today).
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

### P3.5 Retire the sidecar + decide the cursor
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
- **Add-note-to-large-item** (the headline): fixture parent with a fulltext attachment (hash
  unchanged) + add a note. Use a fake/counting embedder; assert embed is called for the note text
  only and the attachment's fulltext chunk ids are unchanged in Chroma.
- **Tag edit** (parent_meta only): assert **zero** embed calls, `update_metadata` called, registry
  `sources` row reflects new tags.
- **Attachment replaced** (storageHash changes): assert that attachment re-embedded, sibling
  attachments + notes untouched.
- **Deletion**: item removed from fixture → its chunks + units removed from Chroma + registry.
- **Resume after interruption**: simulate crash between Chroma upsert and `record_unit_states`;
  assert next run re-plans and converges (no duplicates — idempotent ids).
- **Obsidian**: new/changed/deleted file flows through the plan path equivalently to today.

### P3.8 Acceptance
Adding a note/annotation/tag to a 12,000-page item does **not** re-embed its fulltext; deletions are
detected from ledger-minus-world without `/deleted`; `zotero_delta_state.json` is gone; the §3b
functional profile does not regress; full delta run on the test corpora matches P2 shadow predictions.

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
  surface in `mcp_formatters` + `scripts/sources.py` (parity). Bump `SCHEMA_VERSION` (→3).
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

- **P2 before P3, always.** The shadow parity gate is the cheap insurance that the reconciler
  reproduces today's decisions before it's allowed to drive deletes/embeds.
- **P5 can land with P3** (shared migration) or after; it's independent of the reconciliation logic.
- **P4 backfill is optional for the fresh v0.6 build** — prioritize drift reporting over backfill.
- Keep every new knob in `config.example.yaml` with a default and print it in preflight (W6).
- Registry-sync rule (CLAUDE.md): any Chroma write/delete updates the registry **and now the ledger**
  in the same step.
