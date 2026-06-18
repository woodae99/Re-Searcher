# Post-Mission Cutover Cleanup Spec

**Status**: in progress after mission-gate approval.  
**Window**: after the full mission rehearsal/final run, before production cutover.  
**Purpose**: remove v0.6 compatibility paths that were kept only to de-risk the first
real mission run, remove retired runtime options, and make the production status
surface describe coverage accurately.

## 1. Trigger

Start this work only when all of the following are true:

- The full process-in-coaching mission has completed against `research_test_v06`.
- The mission coverage audit reconciles: every frozen-register source has a
  record or an explicit no-indexed-text/null coverage row.
- Quote/backlink validation passes for the mission output.
- CLI/MCP mission-surface parity remains green.
- Colin approves moving from mission validation to production cutover cleanup.

Before that gate, leave compatibility paths alone. After approval, apply the
changes below as the pre-production cutover cleanup.

## 2. Decisions To Apply

### 2.1 Ledger becomes the only update planner

The register/index ledger becomes the source of truth for deciding update work:

- `enumerate_state()` provides current source units.
- `index_units` stores indexed unit fingerprints.
- `src/reconcile.py` builds the work plan.
- Chroma remains a follower: write/delete/update decisions come from the ledger
  plan, not from Zotero's delta sidecar or Obsidian's old cache.

### 2.2 Legacy sidecars are retired

Remove or migrate away from:

- `zotero_delta_state.json`
- old SQLite-delta execution branches in `src/pipeline.py`
- old Zotero `/deleted?since=` update dependence
- `vault_files` as an independent update-decision sidecar

It is acceptable to keep a one-time migration that reads old sidecars for
diagnostics, but production update decisions must not depend on them.

### 2.3 Legacy chunk routing is retired

The production chunking path is v0.6 single working grain:

- `chunking.mode: v0.6_single_grain`
- recursive `mid` chunks only
- no hierarchical `fine`/`mid`/`coarse` write path
- no `parent_id` navigation path for current indexing
- `legacy_router` removed from production config and tests, or retained only as
  an archived compatibility fixture that cannot be selected by normal config

The mission used mid chunks and source enumeration; after that proof, maintaining
the legacy router as a live option adds risk without production value.

### 2.4 Chunkless units are coverage accounting, not sync drift

Status reporting must distinguish:

- **orphan chunks**: chunks with no ledger/source identity; sync problem, red
  alert.
- **unexpected chunkless units**: indexable units that should have chunks but do
  not; sync or extraction problem, red alert.
- **expected chunkless units**: known no-fulltext/no-indexed-text attachment
  units, or sibling attachments where the source is represented by another
  indexed attachment; coverage accounting, not parity failure.

`index_units.chunk_count` is not authoritative in the current build. Either:

- maintain it transactionally everywhere chunks are written/deleted, or
- stop exposing it as meaningful and compute coverage from `chunks` joins.

Do not let a raw `chunk_count=0` query drive production readiness decisions.

### 2.5 Legacy LLM reranker is retired

The v0.6 production reranker is the vLLM-served cross-encoder:

- `retrieval.rerank.enabled: true`
- `retrieval.rerank.type: cross_encoder`
- `retrieval.rerank.cross_encoder.model: BAAI/bge-reranker-v2-m3`
- endpoint: `/v1/rerank` on the persistent vLLM reranker service

The old reranker path was a small LLM loaded through LM Studio and prompted to
return JSON scores. That stack is no longer part of v0.6 production and can be
removed with the other post-mission compatibility paths.

Retire:

- `LLMReranker`
- LM Studio reranker config blocks and docs
- JSON-score parser tests for LLM reranker output
- any tests that require an OpenAI-compatible API key only to construct the old
  LM Studio/OpenAI reranker client

Keep:

- `CrossEncoderReranker`
- `NoRerank` for debugging and explicit `--no-rerank` requests
- MCP/CLI `no_rerank` override
- live tests that prove the configured cross-encoder path works

## 3. Implementation Tasks

### P1 - Flip ledger execution and remove legacy delta branches

Files to inspect/edit:

- `config.example.yaml`
- `src/pipeline.py`
- `src/sources/zotero.py`
- `src/sources/obsidian.py`
- `src/reconcile.py`
- `src/registry.py`
- `scripts/index.py`
- tests under `tests/unit/test_ledger_execution.py`,
  `tests/unit/test_ledger_parity.py`, and any delta-specific tests

Required behavior:

- Default config uses `indexing.ledger.execute: true`.
- Legacy shadow logging is removed or inverted into an optional diagnostic that
  never drives execution.
- Resume always re-plans from the ledger.
- Deletions come from ledger-minus-world.
- Parent metadata-only updates continue to update metadata without embedding.
- New/changed attachments, notes, annotations, and Obsidian files remain
  granular.
- `zotero_delta_state.json` is no longer created during normal runs.

Acceptance:

```bash
.venv/bin/python -m pytest tests/unit/test_reconcile.py tests/unit/test_ledger_execution.py tests/unit/test_ledger_parity.py -q
.venv/bin/python -m pytest tests/unit --ignore=tests/unit/test_mcp_server.py -q
```

Then run two incremental updates on the full test corpus:

1. no source changes -> no work planned, no Chroma/registry drift
2. controlled fixture mutations -> expected granular work only

### P2 - Retire legacy chunk router as a live production path

Files to inspect/edit:

- `config.example.yaml`
- `scripts/index.py`
- `src/pipeline.py`
- `src/processing/router.py`
- `src/processing/chunkers/`
- `tests` that still assert hierarchical live behavior
- docs mentioning `legacy_router`, `coarse`, `fine`, or `parent_id` as current
  production behavior

Required behavior:

- New indexes use v0.6 single-grain recursive chunking.
- Config cannot accidentally select the legacy router in production.
- Tests that cover legacy chunking are either deleted, archived, or clearly
  marked as compatibility-only and excluded from the production acceptance path.
- Existing read/query surfaces continue to tolerate old metadata when pointed at
  an old collection, but no new production rebuild writes legacy hierarchy.

Acceptance:

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m pytest tests/integration/test_mission_surface_parity.py -q
```

Run a small fresh index and verify all new chunks have the v0.6 working grain and
no live `fine`/`coarse` hierarchy is produced.

### P3 - Classify chunkless-unit reporting

Files to inspect/edit:

- `src/registry.py`
- `src/mcp_formatters/formatters.py`
- `scripts/sources.py`
- `src/mcp_server.py`
- tests for status formatting and registry drift

Required behavior:

- `registry.status()["ledger_drift"]` separates expected and unexpected
  chunkless units.
- `ok` is false for orphan chunks or unexpected chunkless units.
- `ok` is not false merely because expected no-indexed-text units exist.
- Samples include enough reason fields for a human audit:
  `unit_id`, `unit_kind`, `identity_value`, `child_key`, `reason`, and source
  title where available.
- CLI and MCP status text explain coverage-null units without presenting them as
  sync drift.

Suggested payload shape:

```json
{
  "ledger_drift": {
    "unexpected_chunkless_unit_count": 0,
    "expected_chunkless_unit_count": 11,
    "orphan_identity_count": 0,
    "orphan_chunk_count": 0,
    "ok": true
  }
}
```

Acceptance:

```bash
.venv/bin/python -m pytest tests/unit/test_registry.py tests/test_mcp_formatters.py -q
.venv/bin/python scripts/sources.py status --json
```

The live test collection should report Chroma/registry drift `0`, orphan chunks
`0`, and classify the known 11 attachment units as expected coverage-null rows.

### P4 - Retire LM Studio / LLM reranker path

Files to inspect/edit:

- `src/retrieval/rerank.py`
- `src/factories/reranker_factory.py`
- `config.example.yaml`
- `docs/RERANKING.md`
- `docs/RERANKER_BAKEOFF.md`
- `docs/EMBEDDING_BACKEND.md`
- `docs/MCP_SERVER.md`
- `scripts/query.py` help text if it still says LLM reranking by default
- `tests/test_rerank_json.py`
- tests that mock or construct `LLMReranker`

Required behavior:

- `type: cross_encoder` is the only production reranker implementation.
- `type: llm` is removed or archived as non-production historical code.
- stale JSON parser tests are deleted or moved to archived tests.
- no normal test requires an API key to instantiate a retired reranker.
- `--no-rerank` and MCP `no_rerank=true` still work by selecting `NoRerank`.

Acceptance:

```bash
.venv/bin/python -m pytest tests/unit/test_cross_encoder_rerank.py -q
.venv/bin/python -m pytest tests/integration/test_mission_surface_parity.py -q
```

Also run one direct live query and confirm returned metadata includes
`rerank_score` from `CrossEncoderReranker`.

### P5 - Remove or maintain `index_units.chunk_count`

Choose one path:

1. **Remove/deprecate**: leave the column for schema compatibility but document it
   as deprecated, exclude it from status/readiness checks, and add a test proving
   status does not use raw `index_units.chunk_count`.
2. **Maintain**: update every ledger write/delete path so the column reflects the
   actual chunk count for that unit, including attachment/note/annotation child
   keys and Obsidian files.

Prefer path 1 unless production operations need a materialised unit count. The
join-based checks are more reliable and avoid subtle transactional skew.

### P6 - Documentation and configuration cleanup

Update:

- `docs/SPEC_V0.6_REBUILD.md`
- `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`
- `docs/SPEC_LEDGER_IMPLEMENTATION_HANDOFF.md`
- `docs/V0.6_REMAINING_ACTIONS_HANDOFF.md`
- `docs/USAGE_GUIDE.md`
- `docs/MCP_SERVER.md`
- `CHANGELOG.md`
- `AGENTS.md` / project-local agent guidance if present

Required docs outcome:

- Production update flow is ledger-first.
- Legacy sidecar is documented as retired.
- v0.6 single-grain chunking is the only production write path.
- vLLM cross-encoder reranking is documented as the production rerank path.
- LM Studio/LLM reranking is documented as retired, not a fallback to maintain.
- Status semantics distinguish sync drift from coverage-null units.
- Production cutover instructions say how to start Chroma/vLLM/MCP on Sparky and
  how to verify CLI/MCP parity.

## 4. Production Cutover Acceptance

Before rebuilding production:

```bash
.venv/bin/python -m pytest tests/unit --ignore=tests/unit/test_mcp_server.py -q
.venv/bin/python -m pytest tests/integration/test_mission_surface_parity.py -q
.venv/bin/python scripts/sources.py status --json
```

Then perform a disposable full-corpus dry run with production-intended config:

- ledger execution on
- legacy sidecar absent before and after the run
- single-grain chunking only
- vLLM cross-encoder reranking on
- old LLM/LM Studio reranker path absent from production config
- Chroma/registry drift `0`
- orphan chunks `0`
- unexpected chunkless units `0`
- expected coverage-null units accounted for in the run report
- second incremental pass plans no unexpected work

Only after this passes should production `research_library` be rebuilt on Sparky.

## 5. Non-Goals

- Do not change mission outputs or reinterpret mission records.
- Do not rebuild production as part of this cleanup.
- Do not add new extraction strategies unless the production dry run exposes a
  blocker.
- Do not preserve legacy routing as a normal user-facing mode unless Colin
  explicitly decides to support old collections as a compatibility product.
