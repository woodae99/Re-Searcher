# Re-Searcher v0.6 — Clean Rebuild Spec & Plan

**Created**: 2026-06-13
**Status**: Proposed (spec for approval)
**Branch**: `v0.6-rebuild`
**Supersedes/extends**: the shipped Jan-2026 "vNext" effort (parallel extraction,
oversize guard, progress UI, chunking router, document-scoped parent IDs — all in
`main`). The throughput machinery (`EMBED_STORE_PIPELINE_SPEC.md`'s producer/consumer
overlap and concurrent embedding) is **already implemented** in `pipeline.py` /
`embedding/lmstudio.py`, just conservatively configured — so v0.6 enables and tunes it
rather than building it.

> **Code-state note (verified 2026-06-13).** The older `docs/*VNEXT*` and
> `PLAN_PARALLEL_PROGRESS.md` describe *completed, shipped* work; they are history,
> not constraints — this spec favours the present direction. Verified in `main`:
> `src/preflight.py`, `src/progress.py`, `src/processing/{router,oversize_guard,id_utils}.py`,
> and `src/processing/chunkers/{atomic,markdown,hierarchical}.py` all exist; the
> `embed_store_pipeline` (producer/consumer + bounded queue) and `max_concurrent_requests`
> parallel embedding are implemented and exposed in `config.example.yaml`
> (`queue_max_items`, `embed_sub_batch_size`, `store_sub_batch_size`; concurrency capped at 2).

> **Naming note.** "vNext" in `docs/IMPLEMENTATION_PLAN_VNEXT.md`,
> `CHUNKING_VNEXT.md`, `VNEXT_TEST_RESULTS.md` refers to a *completed* Jan-2026
> effort, now in `main`. This document is the *next* line of work — versioned
> **v0.6** to avoid the collision — culminating in a clean production rebuild.

---

## 1. Why v0.6 exists

The June 2026 capability proof ([[pilot-trial-02-findings]] in the vault) showed the
register-backed mission machinery is **technically sound** — enumeration matches the
registry exactly and fabricated quotes are mechanically catchable — but the *current
production data is messy* in ways only a clean rebuild fixes:

- **~10.6% duplicate chunks** (1.1M rows, Zotero-only, ~1,056 sources): old chunk
  generations never deleted when chunking changed. Search-driven passes double-count.
- **`fine` chunks don't earn their cost**: on the one clean multi-level source, `fine`
  (760 chunks, ~148 chars) returns sub-quotable fragments at ~3.3× the storage of `mid`;
  `mid` (~488 chars) is the quotable/codeable working grain; `coarse` (~1869 chars) is
  best for framing. Most of the corpus is already `mid`-only — the inconsistency is itself
  an artifact of the chunking change that created the duplication.
- **PDF text artifacts** pollute retrieval and break verbatim quoting: line-break
  hyphenation (`"actu-\nally"`), ligature+space (`"ﬂ uid"`), repeated running headers,
  occasional garbled reversed-text chunks.
- **Throughput**: full rebuild ~4 days; embedding concurrency (`max_concurrent_requests: 2`)
  is the bottleneck. The producer/consumer embed↔store overlap is *implemented* in
  `pipeline.py` but conservatively configured — it needs enabling/tuning, not building.
- **Durability**: a power-crash left blank checkpoint files (tmp+rename without fsync) and
  ChromaDB 1.3.0 crash-loops replaying a large WAL backlog after an unclean stop.

Decision (Colin, 2026-06-13): **leave the current noisy DB as-is** (usable, not great) and
fix all of the above in v0.6 with a **clean rebuild against empty databases** for
production going forward. Cleaning the existing DB is throwaway work and is not done unless
mission pressure forces it before v0.6 lands.

## 2. Goals / non-goals

**Goals**
1. Two-level chunking (`mid` + `coarse`), structure-aware, no `fine`.
2. A text-cleaning stage that removes the artifact classes above, validated against
   known-noisy items.
3. Throughput: raise embedding concurrency, enable embed/store overlap, parallel/ batched
   upserts — bring a full rebuild from days toward hours.
4. Upgrade ChromaDB (free on an empty build; removes the 1.3.0 crash-loop fragility).
5. Durability: fsync all checkpoint/state writes; resumable as today.
6. A **test-corpus-driven** development loop: tune chunking/cleaning variables on a small,
   fast, re-runnable corpus and compare quality objectively before committing to a full run.
7. Keep it **modular, maintainable, configurable — no hard-coded configs.**
8. A clean production rebuild whose acceptance is the real process-in-coaching mission.

**Non-goals**
- Migrating/deduping the existing production collection (explicitly out — replaced).
- Re-litigating shipped vNext stages (preflight, progress, parallel extraction, oversize
  guard, registry, enumeration, delta updates) except where v0.6 changes them.
- New query/rerank behaviour (separate track).

## 3. Guiding principles (carried from vNext, reaffirmed)

- **Config-first, no hard-coded behaviour.** Every new knob lands in `config.example.yaml`
  with a documented default; nothing magic in code. Preflight already prints the resolved
  config — extend it to print the v0.6 knobs.
- **Stage isolation**: extraction → cleaning → chunking → embedding → storage, clear
  boundaries, each independently testable and swappable.
- **Determinism & resumability**: stable IDs, reproducible ordering, checkpointed state
  (now fsync-durable).
- **Registry is the source of truth** for source/chunk identity and drift — unchanged.
- **Modularity for maintenance**: a new chunker / cleaner / source must not require touching
  the pipeline core; register via config + a small interface.

## 4. Test-corpus-driven methodology (the core working loop)

The expensive mistake is tuning on the full corpus. v0.6 develops against a **small,
representative, fast-to-rebuild** corpus so variables can be swept cheaply.

- **`test_zotero` corpus**: a hand-picked Zotero collection (~20–40 items) spanning the
  kinds that matter — clean journal PDFs, a book/handbook (chapter-level), a notes/annotations
  item — **plus deliberately known-noisy items**: a multi-column PDF, an OCR'd/garbled scan,
  a heavy running-header journal, a huge omnibus (the Nietzsche Delphi case), a metadata-only
  /no-fulltext item. Reuse the `Research Tasks\Process` register where possible so the test
  set doubles as mission input.
- **`test_obsidian` vault**: a small folder of notes — frontmatter, tags, wikilinks, code
  blocks, a very long note, a near-empty note, a conversation-log dump (the chatgpt-export
  style that dominated the crash backlog).
- **Re-runnable in minutes** into a throwaway `research_test` collection (separate from
  production), so a chunking/cleaning variable change costs a quick re-run, not days.
- **Objective quality comparison harness** (formalize this session's `output/mission_*.py`):
  - enumeration ↔ registry exact-match (per source);
  - quote validator (chunk-id exists + belongs-to-source + verbatim substring after
    NFKC/de-hyphenation normalization);
  - duplication scan (`total rows` vs `distinct logkey`);
  - **level-quality eval**: for a fixed query set, retrieve at each level and score
    usable-evidence (the A2-ii method) — the instrument for choosing chunk sizes;
  - text-artifact scan (hyphenation, ligature+space, header repetition, reversed-text).
- **Pick parameters from the harness, not a priori.** Starting points below are seeds for
  the sweep, not decisions.

## 5. Workstreams

### W1 — Text extraction & cleaning  *(new)*
Add a **cleaning stage between extraction and chunking** (`src/processing/cleaning/`,
config `cleaning:`). Operates on `(text, metadata)`; composable cleaners, each toggleable:
- de-hyphenate line-break splits (`"foo-\nbar"` → `"foobar"`); normalize ligatures (NFKC);
- strip repeated running headers/footers (detect lines recurring across N pages/chunks);
- drop reference-list / boilerplate noise where detectable (configurable, conservative);
- detect & quarantine garbled/reversed-text blocks (vertical-text PDF artifacts) — flag in
  metadata rather than silently dropping.
- **Acceptance**: on the known-noisy `test_zotero` items, the artifact-scan count drops to
  ~0 and quote-validator pass-rate on sampled quotes is ~100% *without* downstream
  de-hyphenation hacks. Cleaning is reversible/auditable (record what was removed in metadata).

### W2 — Chunking: two levels, structure-aware  *(amends `CHUNKING_VNEXT.md`)*
- Retire `fine`. Levels become **`coarse`** (framing/context) and **`mid`** (evidence/quote),
  plus `atomic` for annotations (unchanged). Hierarchical chunker emits coarse→mid with
  `parent_id` (mid→coarse); markdown semantics (headings, code blocks, frontmatter, tags,
  links, `heading_path`) preserved.
- **Structure-aware boundaries everywhere** — never split mid-word/mid-sentence (the source of
  the hyphenation fragments). Extend the markdown chunker's structure-respect to PDF text
  (paragraph/sentence boundaries, section headings where detectable).
- **Size seeds for the sweep**: `mid` ≈ 200–350 tokens (a complete, quotable paragraph, ~15%
  overlap); `coarse` ≈ 600–900 tokens (a sub-section). Final sizes chosen by the W4-eval on
  the test corpus. Keep BGE-M3 precision in mind — bigger ≠ better for a single vector.
- **Acceptance**: level-quality eval shows `mid` is the best quotable grain and `coarse`
  best for framing; no chunk exceeds the oversize guard; rerun doesn't balloon counts;
  no `fine` level present.

### W3 — Throughput  *(enable + tune; machinery already exists)*
The infrastructure is built, just conservative — this is largely a config + validation task,
not new development:
- Raise embedding concurrency (`embedding.max_concurrent_requests`, currently capped at 2) to
  a value swept against the local LM Studio / 5090 ceiling (`lmstudio.py` already runs batches
  in a thread pool sized by this knob).
- Turn the **producer/consumer embed↔store overlap on by default** after validation
  (`indexing.embed_store_pipeline` is implemented in `pipeline.py`); tune `queue_max_items`,
  `embed_sub_batch_size`, `store_sub_batch_size`; bounded queue gives backpressure; determinism
  preserved via stable IDs.
- Confirm Chroma upserts honour the configured store batch size (verify whether
  `src/storage/chroma.py` still uses a fixed upsert size and wire it to config if so).
- **Acceptance**: full `test_*` rebuild throughput improves materially (target: project a full
  production rebuild to hours, not days); stored count == embedded count; resume still works.

### W4 — ChromaDB upgrade  *(free on empty build)*
- Build v0.6 on a current ChromaDB (≥1.5.x). The only blocker was that 1.5.x can't read the
  1.3.0 on-disk format — irrelevant when starting from empty. Pin client == server.
- Confirm the new version replays a WAL backlog after an unclean stop without segfaulting
  (1.5.9 errored gracefully in this session's test vs 1.3.0's crash-loop).
- **Acceptance**: fresh `research_test` collection builds and serves on the new version;
  an unclean-stop + restart recovers without a crash-loop.

### W5 — Durability  *(folds in the spawned fsync task)*
- All checkpoint/state writers use tmp-file → flush → `os.fsync` → atomic replace (a shared
  `write_json_durable` helper): `IndexingProgress._save` (`src/indexing.py`), the dashboard
  snapshot, the Zotero delta-state writer, `source_hash.txt`.
- **Acceptance**: kill -9 mid-run leaves valid (not blank) checkpoint files; resume continues.

### W6 — Config & modularity  *(cross-cutting)*
- Every W1–W5 knob in `config.example.yaml` with defaults; preflight prints them.
- Cleaners and chunkers are registry/config-selected behind small interfaces; adding one is
  config + a class, no core edits. Audit for any remaining hard-coded paths/values.
- **Acceptance**: a reviewer can change chunk sizes, toggle a cleaner, or change concurrency
  entirely via config; `grep` shows no hard-coded corpus/host/path constants in the new code.

### W7 — Acceptance harness  *(formalize this session's tools)*
- Promote `output/mission_*.py` into `scripts/` or `tests/` as first-class, config-driven
  tools (enumeration↔registry, quote validator, duplication scan, level-quality eval,
  artifact scan). These are the **gate** for both the test-corpus sweeps and the final
  production rebuild.

## 6. Acceptance scenario — the process-in-coaching mission

v0.6 is "done for production" when, on a **freshly rebuilt clean collection**:
- the W7 harness passes (zero duplication, zero artifacts, exact enumeration, verbatim quotes);
- the real process-in-coaching mission ([[mission-spec]], 551-source `Research Tasks\Process`)
  runs end-to-end — screening + extraction per source, frontier review of flags — and its
  coverage audit reconciles with no duplicate-hit inflation;
- pilot-trial-02's records re-generate cleanly against the new DB (regression check).

The mission can wait for v0.6 but **not indefinitely**; if pressure mounts before v0.6 lands,
reconsider a one-off delete-only dedup of the 1,056 Zotero sources as a stopgap (see
[[re-searcher-zotero-chunk-duplication]]).

## 7. Cutover plan (no big-bang risk)
1. Build v0.6 into a **new collection** (`research_library_v06`) alongside the live one — the
   noisy `research_library` keeps serving Colin's day-to-day search throughout.
2. Validate the new collection with the W7 harness + the mission acceptance scenario.
3. Flip config/MCP to the new collection; keep the old one until confidence is high; then
   retire it and reclaim space (incl. the 121 GB `data_recovery_test` backup).

## 8. Phasing & gates (suggested order)
- **P0 — Test harness + test corpora (W7, W4-eval, §4).** Nothing tunes well without these.
  Gate: harness runs against the current DB and reproduces this session's findings.
- **P1 — Cleaning + 2-level chunking (W1, W2)** on `test_*`, swept with the harness.
  Gate: artifacts ~0, level-eval picks sizes, quotes verify verbatim.
- **P2 — ChromaDB upgrade + durability (W4, W5)** on `test_*`.
  Gate: fresh build serves on new version; unclean-stop recovers; checkpoints survive kill.
- **P3 — Throughput (W3).** Gate: rebuild-time projection acceptable; counts reconcile.
- **P4 — Config/modularity hardening (W6)** continuous; final audit before rebuild.
- **P5 — Production rebuild + cutover (§7) + mission acceptance (§6).**

## 9. Risks & open questions
- **Embedding concurrency ceiling** is set by LM Studio / the 5090, not just config — sweep to
  find it; watch for OOM / throughput collapse.
- **ChromaDB version choice**: pick the latest stable that's proven to recover from an unclean
  WAL backlog; pin client==server; re-verify on the test build.
- **Cleaning aggressiveness**: reference-list/boilerplate stripping must be conservative —
  prefer flag-in-metadata over destructive removal where uncertain; the known-noisy corpus is
  the guardrail.
- **Coarse size vs embedding precision**: larger coarse aids framing but blurs the vector —
  the level-eval decides, not intuition.
- **Mission timing pressure** vs v0.6 timeline — the stopgap dedup is the release valve.

## 10. Decisions log
| Date | Decision |
|---|---|
| 2026-06-13 | Leave current production DB noisy; fix via clean rebuild in v0.6, not in-place dedup |
| 2026-06-13 | Retire `fine`; two levels `mid`+`coarse` (+`atomic` annotations); sizes chosen empirically on the test corpus |
| 2026-06-13 | Add a cleaning stage (de-hyphenation, ligatures, headers, garbled-text), pressure-tested on known-noisy items |
| 2026-06-13 | Upgrade ChromaDB on the fresh build (free; removes 1.3.0 crash-loop); pin client==server |
| 2026-06-13 | Develop against small re-runnable `test_zotero` + `test_obsidian` corpora with an objective quality harness |
| 2026-06-13 | The process-in-coaching mission is v0.6's end-to-end acceptance scenario |
| 2026-06-13 | Cutover via a parallel new collection, not in-place; old DB serves until cutover |
