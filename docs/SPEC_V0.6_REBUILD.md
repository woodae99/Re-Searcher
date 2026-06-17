# Re-Searcher v0.6 — Clean Rebuild Spec & Plan

**Created**: 2026-06-13
**Status**: Proposed (spec for approval)
**Branch**: `v0.6-rebuild`
**Supersedes/extends**: the shipped Jan-2026 legacy feature effort (parallel extraction,
oversize guard, progress UI, chunking router, document-scoped parent IDs — all in
`main`). The throughput machinery (`EMBED_STORE_PIPELINE_SPEC.md`'s producer/consumer
overlap and concurrent embedding) is **already implemented** in `pipeline.py`; v0.6
enables/tunes it where it still helps, but the biggest measured throughput win is now
the vLLM/Qwen3 embedding backend rather than LM Studio concurrency alone.

> **Code-state note (verified 2026-06-13).** The older legacy feature docs and
> `PLAN_PARALLEL_PROGRESS.md` describe *completed, shipped* work; they are history,
> not constraints — this spec favours the present direction. Verified in `main`:
> `src/preflight.py`, `src/progress.py`, `src/processing/{router,oversize_guard,id_utils}.py`,
> and `src/processing/chunkers/{atomic,markdown,hierarchical}.py` all exist; the
> `embed_store_pipeline` (producer/consumer + bounded queue) and `max_concurrent_requests`
> parallel embedding are implemented and exposed in `config.example.yaml`
> (`queue_max_items`, `embed_sub_batch_size`, `store_sub_batch_size`; concurrency capped at 2).

> **Naming note.** The Jan-2026 planning docs have been renamed as legacy feature
> work because that effort is complete and now lives in `main`. This document is
> the next line of work — versioned **v0.6** to avoid the collision — culminating
> in a clean production rebuild.

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

## 1a. Current implementation status (2026-06-17)

This spec has evolved through evidence and implementation. The current branch is no
longer the 2026-06-13 proposal state.

**Implemented / substantially implemented**
- vLLM embedding backend with Qwen3-Embedding-0.6B, managed lifecycle, query
  instruction, and `context_length`/`max_model_len = 8192` (must remain above
  the 7000-token oversize guard). LM Studio remains an interactive/fallback
  provider, not the production default.
- vLLM cross-encoder reranking path.
- ChromaDB 1.5.9 dependency pin for the fresh rebuild path.
- Recursive 700/100 working grain as the measured `mid` default.
- Acceptance harness core for registry-vs-collection exactness, duplicate audit,
  artifact scan, and quote verification.
- Register-as-index-ledger foundation: `index_units`, source `enumerate_state()`,
  reconciler, shadow parity logging, gated granular execution, and ledger drift
  reporting.
- W8 systematic-review selection metadata: `item_type`, `doi`, `abstract`,
  `tags`, `venue`, `language`, filters on CLI/MCP list-source surfaces, and
  annotation `has_comment`.

**Still open before final v0.6**
- The W2 single-grain cutover is not complete: `router_enabled` + `huge_docs`
  can still route huge documents through the legacy hierarchical chunker and
  emit `coarse`/`fine`; `parent_id` logic is still live. This is an
  implementation gap, not merely stale text.
- The W8 survey/aggregate-by-source retrieval mode is not implemented.
- The W1 extraction seam/quality-gated router is researched but not wired into
  indexing; extraction provenance columns are not yet in the register.
- W5 fsync-durable JSON/state writes are not implemented.
- Ledger execution remains intentionally off by default pending a full parity
  proof; do not retire `zotero_delta_state.json` or `vault_files` until that
  suite passes.
- Structured failure reporting is still needed so failed
  extraction/chunk/embed/store events become both engineering telemetry and
  corpus-cleanup data.

For cold-start implementation details, see
`docs/V0.6_REMAINING_ACTIONS_HANDOFF.md`.

## 2. Goals / non-goals

**Goals**
1. **Two-plane architecture**: register as the control/navigation plane, Chroma as a
   single-working-grain retrieval plane — preserving the full functional profile (§3b).
2. **Single working grain (`mid`)**, structure-aware; `coarse` added only if the eval shows
   broad-survey recall needs it; `fine` retired; `parent_id`-navigation retired.
3. **High-quality extraction**: route among measured extractor candidates behind a
   swappable seam; prefer zero-cost Zotero full text when it passes quality gates, and
   reduce/clean artifacts at the source.
4. Throughput: raise embedding concurrency, enable embed/store overlap, batched upserts —
   bring a full rebuild from days toward hours.
5. Upgrade ChromaDB (free on an empty build; removes the 1.3.0 crash-loop fragility).
6. Durability: fsync all checkpoint/state writes; resumable as today.
7. A **test-corpus-driven** development loop: tune extraction/chunking on a small, fast,
   re-runnable corpus and compare quality objectively before any full run.
8. Keep it **modular, maintainable, configurable — no hard-coded configs.**
9. A clean production rebuild whose acceptance is the real process-in-coaching mission.

**Non-goals**
- Migrating/deduping the existing production collection (explicitly out — replaced).
- Re-litigating shipped legacy stages (preflight, progress, parallel extraction, oversize
  guard, registry, enumeration, delta updates) except where v0.6 changes them.
- New query/rerank behaviour (separate track).

## 3. Architecture v0.6 & guiding principles

### 3a. Two planes: register = control/navigation, Chroma = retrieval

The central reframe of v0.6. Previously the chunk hierarchy (`fine`/`mid`/`coarse` +
`parent_id`) did **two** jobs at once: (1) **genealogy/navigation** — which source a chunk
belongs to, its siblings, how to zoom from a hit out to the whole source; and (2) **retrieval
grain** — what vector size gives the best recall. The registry now owns job (1) authoritatively,
so v0.6 splits the system into two planes:

- **Retrieval plane (ChromaDB)** — "dumb": one **working grain** of vectors + text + *minimal*
  metadata (identity keys, `chunk_index` for ordering, `heading_path` for structure). No
  navigational genealogy baked in.
- **Control/navigation plane (registry)** — "smart": source identity, source↔chunk membership
  and ordering, selection metadata (title/authors/year/kind/collection/counts), per-source
  structure outline, extractor/quality provenance, **and sync/reconciliation state — the
  register is the *index ledger* that decides what needs (re)processing; Chroma follows** (see
  `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`).

The user-facing **functional profile is unchanged** ("broadly survey → select candidates →
drill in"); only the *means* change. `parent_id`-as-navigation is **retired** (the register is
the zoom-out); a second chunk level survives **only** if it earns its keep on *retrieval recall*,
not navigation (see W2).

### 3b. Functional-parity / no-regression map  *(the "handle with care" check)*

Every current capability must be delivered in the new model before cutover:

| Capability | Old mechanism (genealogy-in-chunks) | v0.6 mechanism (two-plane) | New support needed |
|---|---|---|---|
| Broad **survey** ("what does the corpus say about X") | semantic search over `coarse` vectors | search at `mid` → **aggregate hits by source via register**; optional `coarse` only if eval shows recall gain | survey/aggregate-by-source retrieval mode (W8) |
| **Select** candidate sources | inspect coarse hits | register source view (title/year/kind/hit-count/strength) | register already has the fields |
| **Drill into** a source | walk `parent_id` fine→mid→coarse | `get_source_chunks` (ordered enumeration) | exists |
| Drill into a **section** of a source | coarse chunk as section proxy | enumerate source filtered by `heading_path` | surface `heading_path` in enumeration (W8) |
| **Context** around a hit | fetch `parent_id` parent | `get_chunk_context` (neighbours by `chunk_index`) | exists |
| Source/chunk **membership & counts** | reconstruct from chunk metadata | register (authoritative) | exists |
| **Quote with backlink** | chunk id + text | unchanged (chunk id + verbatim text) | validator normalization (W7) |
| Know **how a source was extracted** | n/a | register `extractor` + `extract_quality` | provenance columns (W1/W8) |

If the eval (W2) shows broad survey needs `coarse`, we add exactly one coarse pass — never
`fine`, never `parent_id`-navigation. Nothing in the table may regress silently; this map is a
cutover gate.

### 3c. Guiding principles (carried forward, reaffirmed)

- **Config-first, no hard-coded behaviour.** Every new knob lands in `config.example.yaml`
  with a documented default; nothing magic in code. Preflight already prints the resolved
  config — extend it to print the v0.6 knobs.
- **Stage isolation behind seams**: extraction → cleaning → chunking → embedding → storage,
  clear boundaries, each independently testable and swappable. The extractor in particular sits
  behind a one-method seam (`extract(source) -> CleanText`) so a fallback can be slotted later
  without touching the pipeline (see W1).
- **Determinism & resumability**: stable IDs, reproducible ordering, checkpointed state
  (now fsync-durable). Extractor output must be deterministic — a precondition for stable IDs.
- **Registry is the source of truth** for source/chunk identity, genealogy, navigation,
  provenance, **and sync state** — now elevated from mirror to control plane. It is the **index
  ledger**: an update run computes its work as a diff between each source's current state
  (enumerated by the adapters) and the register's recorded per-unit fingerprints. Detection lives
  in the adapters, the decision lives in the register/planner, Chroma only executes the result
  (W10; `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`).
- **Modularity for maintenance**: a new chunker / cleaner / extractor / source must not require
  touching the pipeline core; register via config + a small interface.
- **Agile / evidence-led**: where a design choice is genuinely open (chunk grain, survey
  strategy, drill depth), it is a **hypothesis settled by the test-corpus loop**, not an
  up-front commitment. Build the smallest informed thing, test, iterate, evolve. The one-time
  rebuild is the *convergence point*, never the place we experiment.

### 3d. The iterative loop v0.6 must serve (and why grain is low-stakes)

The "systematic" capability is itself **adaptive** — a conversation with the corpus, not a
fixed pipeline:

1. **Survey** (recall-biased) — "who knows anything about X?"; even not-sure sources raise hands.
2. **Filter** — "those who did, step forward" (candidate sources).
3. **Classify** — "what do you know, and how?" (e.g. the senses of *process* in coaching).
4. **Feedback edge** — "does new info change the question?" If yes, **re-ask** (this is how
   sense **E** appeared — the corpus surfaced a use the taxonomy hadn't anticipated); if no, drill.
5. **Mine at the appropriate level** per pocket → evaluate.

Where each step lives — and why this *de-risks* the chunking decision:

- Steps 1, 2, 4 (survey, filter, re-ask) are **control-plane** operations (register-scoped
  search + aggregation + re-querying) — cheap to iterate, independent of chunk grain.
- Steps 3, 5 (classify, mine) read the **retrieval plane** (`mid` enumeration + `get_chunk_context`).
- "Appropriate level" can be an **adaptive runtime drill** — enumerate → context → *optionally
  re-chunk a hot source finer on demand* — rather than pre-baking every level into every source.

Because the loop lives mostly in the control plane, **chunk grain is a low-stakes, reversible
choice**: `mid` is an informed default; if the loop's recall (step 1) or drill depth (step 5)
proves it insufficient, we add exactly what's needed (a coarse pass, or on-demand finer drill)
— a re-chunk of the *test* corpus, not an architecture change. This is the answer to "do we
still need hierarchy in the chunks?": no — we need it in the *loop*, and the register provides it.

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
  The corpus is deliberately stocked with **extractor failure modes** so the measured router
  plan (W1) is actually tested: huge omnibus (perf/timeout), heavy-table
  empirical paper, OCR'd scan, multi-column, formula/math-heavy, non-English, reversed-text weird
  PDF, and a no-PDF item (coverage boundary).
- **Extractor bake-off**: the same corpus runs through Zotero FT cache, `pdfminer`,
  Marker, Docling, and PyMuPDF4LLM where relevant so W1 routing is measured, not assumed.
  See `docs/EXTRACTION_BAKEOFF_AND_ROUTING.md` for the first Sparky/Hudson results and
  the candidate routing strategy.
- **Re-runnable in minutes** into a throwaway `research_test` collection (separate from
  production), so an extraction/chunking variable change costs a quick re-run, not days.
  Run it **on Sparky** (the production host) so throughput/rebuild-time numbers are real — that's
  what decides grind-on-Sparky vs borrow-Bambino for the big rebuild (W9).
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

**Open hypotheses to settle by iteration (not decisions):**
- *Grain*: does `mid` + register-aggregation recover broad-survey recall (step 1), or does a
  `coarse` pass earn its keep? Does any pocket need finer-than-`mid` drill (step 5), and is that
  better served on-demand vs pre-baked?
- *Survey strategy*: mid-aggregate-by-source vs coarse-search vs hybrid; recall vs precision at
  the "raise your hand" stage (we *want* the unsure to surface).
- *Extractor*: which candidate clears the acceptance gates for each source class, and when
  should the router escalate from Zotero FT cache / `pdfminer` to Marker/OCR or a
  hybrid LLM pass?
- *Chunker*: ~~Docling HybridChunker vs our chunker~~ — **resolved (2026-06-15)**: Docling dropped;
  use a **recursive** splitter, `mid` 700/100, settled by the retrieval eval (`docs/CHUNKING_EVAL.md`).
Each has a harness experiment; we converge before the rebuild, and record what the evidence said.

## 5. Workstreams

### W1 — Extraction & cleaning: measured router behind a seam  *(updated 2026-06-15)*
**Decision (2026-06-15, settled):** a whole-corpus quality-gate run (535 PDFs) plus a
derived-hard-scan OCR test settled the extraction plan as a **quality-gated router over two
extractors**: Zotero's native `.zotero-ft-cache` is the zero-cost default (clears the gate
for ~99% of the corpus), `pdfminer` is the cheap fallback for missing/empty caches, a
deterministic cleanup pass handles the `clean` action, and **Marker+OCR** is the rare,
opt-in, budgeted `escalate` backstop (validated: recovers image-only EN/RU scans to
`accept`, but ~1 hr/book). **Docling and pymupdf4llm were evaluated and dropped** (Docling
only pdfminer-grade; pymupdf4llm failed scans without OCR) — re-addable behind the seam if a
fixture class needs them. Extraction is **not** the rebuild bottleneck (embedding is). See
`docs/EXTRACTION_QUALITY_GATE.md` (controlling) and `docs/EXTRACTION_BAKEOFF_AND_ROUTING.md`.

What makes the router safe:
- **Extractor seam.** A one-method interface `extract(source) -> CleanText`
  (`src/processing/extraction/`). Pluggable via config; candidates can be slotted without
  touching the pipeline.
- **Scope is PDFs only.** The extractor router handles the Zotero **PDF/fulltext** path.
  Obsidian markdown and Zotero
  notes/annotations keep their existing clean paths. Items with no accessible PDF are a coverage
  boundary, not an extractor failure — the register records "no fulltext" (the 5WPQDBL5 case).
- **Register-tracked provenance + leak flagging.** Add `extractor` and `extract_quality` to the
  register; a source that fails the acceptance gates is flagged, so leak-plugging is a per-source
  re-extraction (`reindex.py --zotero-keys`), never a re-architecture.
- **Residual cleaner (thin).** Run deterministic cleanup before LLM cleanup where possible:
  de-hyphenation, letter-spaced word repair, repeated header/footer stripping, and spacing
  normalization. Keep it config-toggled, reversible, and recorded in metadata.

**Acceptance criteria** (measured per extractor on the nasty `test_zotero`, via the W7 harness —
used to validate the quality-gated router and to characterise any leak):

| Criterion | Measure | Why it matters |
|---|---|---|
| Coverage | % sources with usable fulltext; count FT-absent | Zotero-FT has gaps; our extractors work on any accessible PDF |
| Artifact rate | artifact-scan per 1k tokens | drives cleaning effort + retrieval/quote quality |
| Reading order | sentence-continuity proxy + multi-column spot check | source of scrambled/reversed chunks |
| Quote verifiability | % sampled quotes verbatim-verify | the "take-to-the-bank" metric |
| Tables/figures | spot-check stats-table fixture | usable structure vs number-soup |
| Downstream retrieval | level-eval usable-evidence@k on mission queries | the ultimate arbiter |
| Determinism | same input → same text across runs; version-pinnable | stable IDs + registry sync depend on it |
| Throughput | pages/sec; projected full-rebuild time | one-time rebuild budget |
| Dependency/coupling | install weight; needs Zotero running? | long-term upkeep |

**Pass gate for the rebuild**: artifact ≈ 0, quote-verify ≈ 100%, reading order intact, tables
usable, deterministic, downstream usable-evidence@k ≥ current, rebuild-time within budget. The
**aftermath** = sources that fail, clustered by failure type — that, and only that, tells us
whether a backstop is needed and for what.

### W2 — Chunking: single working grain (hypothesis), register-driven navigation  *(amends `CHUNKING_LEGACY.md`)*
Now that the register owns genealogy (§3a), chunking is *only* a retrieval-grain decision —
and an **open one, settled by iteration** (§3d), not committed up front:
- **Working hypothesis: `mid` is the single working grain** (evidence/quote), `atomic` for
  annotations (unchanged — see annotation note below). `fine` retired (its cost/benefit failed in pilot-02).
  **`parent_id`-as-navigation retired** — keep `chunk_index` ordering and `heading_path`; the
  register + `get_source_chunks`/`get_chunk_context` provide all navigation. This is the
  *starting* configuration to test, not a final decision — the loop's recall/drill needs decide.
- **`coarse` is conditional, not assumed.** Add a coarse pass **only if** the level-quality eval
  shows it materially improves *broad/whole-corpus survey recall* over "mid + register
  aggregation" (W8). For the register-scoped mission (exhaustive per-source screening over mid),
  coarse is expected to add little. If added, it's one extra pass — never `fine`.
- **Structure-aware boundaries** — never split mid-word/mid-sentence. Since extraction is now
  plain-text-dominant (Zotero FT cache ~99%, not Docling structure), this means a **recursive**
  splitter (respects paragraph/sentence boundaries), not the `character` splitter.
- **First eval result (2026-06-15, `docs/CHUNKING_EVAL.md`):** measured over 40 sources / 20 gold
  probes through real BGE-M3 retrieval. **`recursive` strategy beats `character` decisively**
  (char only splits on blank lines → oversized chunks, worst hit@1/MRR). Retrieval is **flat
  across recursive sizes 500–1000**; ≥1200 dips. Decision: **`recursive`, `mid` = 700 chars /
  100 overlap** (Pareto: top retrieval at 26% fewer chunks than 500); `1000/150` if minimizing
  rebuild cost. The Docling HybridChunker option is dropped with Docling.
- **Caveat:** the eval is *source-level* and saturated (hit@5=1.0). It firmly settles
  strategy=recursive; the precise size needs a *passage-level*, larger-distractor eval to
  discriminate (W2 next step). Keep BGE-M3 precision in mind — bigger ≠ better for a single vector.
- **Acceptance**: eval confirms `mid` is the best quotable grain and that "mid + register
  aggregation" recovers survey (or that coarse is justified); no chunk exceeds the oversize
  guard; rerun doesn't balloon counts; no `fine`, no `parent_id`-navigation.
- **Annotation chunking decision *(confirmed 2026-06-17)*: keep `atomic`; do not fold into
  recursive.** An annotation is one human-curated unit — a highlight (`an.text`) plus optionally
  Colin's own comment (`an.comment`) — and is the highest-value text in the corpus for sense
  classification. Recursive splitting could sever a highlight from its comment; folding gains
  nothing for normal short annotations (each is its own `Document`, so recursive would only act
  *within* one). The lone risk — a giant annotation truncated at the embedder — is already covered:
  the oversize guard runs on *all* chunks post-router (`pipeline.py:641-642`) and splits anything
  over `max_tokens_per_chunk`. Atomic is a deliberate second *grain* (variable size, one unit), not
  a second hierarchy *level* — not the `parent_id` genealogy v0.6 retired. Two small refinements
  (see implementation spec): (a) add a **`has_comment`** flag so screening can filter annotations
  that carry Colin's commentary vs bare highlights; (b) optionally capture annotation **`color`/`type`**
  if color-coding is used as a coding scheme (currently the query pulls only `text, comment,
  sortIndex, pageLabel`).

### W3 — Throughput  *(enable + tune; machinery already exists)*
The infrastructure is built, just conservative — this is largely a config + validation task,
not new development:
- vLLM/Qwen3 is now the production embedding path and has already delivered the primary
  throughput improvement. Continue to tune `embedding.max_concurrent_requests`,
  `embedding.batch_size`, and vLLM managed-server settings against Sparky's actual ceiling.
  LM Studio remains a fallback/interactive backend rather than the performance target.
- Turn the **producer/consumer embed↔store overlap on by default** after validation
  (`indexing.embed_store_pipeline` is implemented in `pipeline.py`); tune `queue_max_items`,
  `embed_sub_batch_size`, `store_sub_batch_size`; bounded queue gives backpressure; determinism
  preserved via stable IDs.
- Confirm Chroma upserts honour the configured store batch size (verify whether
  `src/storage/chroma.py` still uses a fixed upsert size and wire it to config if so).
- **Acceptance**: full `test_*` rebuild throughput improves materially (target: project a full
  production rebuild to hours, not days); stored count == embedded count; resume still works.

### W4 — ChromaDB upgrade  *(free on empty build)*
- Build v0.6 on ChromaDB 1.5.9 for the first Sparky clean rebuild. The only blocker was that 1.5.x cannot read the
  1.3.0 on-disk format — irrelevant when starting from empty. Pin client == server via `requirements.txt` and `constraints-sparky.txt`.
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

### W8 — Register as control plane: navigation capabilities  *(new; from the §3b parity map)*
The support the two-plane model needs so nothing in the functional profile regresses:
- **Survey/aggregate-by-source retrieval mode**: a search variant (CLI + MCP, same shared code)
  that runs a `mid` search and returns hits **grouped and ranked by source** via the register
  (hit count + best score per source) — the "broad survey" step that `coarse` used to serve.
- **`heading_path` surfaced in enumeration**: `get_source_chunks` exposes section structure so
  "drill into section X" = enumerate-by-`heading_path`; optionally a per-source outline view.
- **Register provenance columns**: `extractor`, `extract_quality` (+ optional structure outline)
  — powers the W1 leak-tracking and per-source re-extraction.
- **Register selection metadata for systematic review** *(added 2026-06-17)*: the register is the
  **control/filter plane** for the loop's survey→filter→re-ask steps (§3d steps 1, 2, 4), so the
  fields those steps filter on must live *in the register*, not buried in Chroma chunk metadata
  (where filtering = a collection scan). The current `sources` table carries only
  title/authors/year/backlink/collections — under-representative for systematic review. Add, in
  priority order:
  1. **`item_type`** ("kind": book / article / chapter / thesis / report) — the spec's §3b parity
     map already promises "kind" as a register selection field. **Implemented 2026-06-17**:
     `_get_item_metadata` resolves Zotero `typeName`, and the register lifts it into
     `sources.item_type`.
  2. **`doi`** — canonical systematic-review dedup + citation key (PRISMA dedup runs on DOI).
  3. **`abstract`** (Zotero `abstractNote`) — title/abstract screening is PRISMA stage 1; directly
     powers the survey/filter steps. Larger than the rest, but one row per source in SQLite is fine.
  4. **`tags`** — Colin's own manual coding/screening labels; already extracted into metadata
     (`zotero.py`) but not lifted to the register. Store comma-joined, like `collections`.
  5. **`venue`** (`publicationTitle`) and **`language`** — standard inclusion/exclusion filters.

  All of (2)–(5) were already extracted into Chroma chunk metadata via the `**fields` spread in
  `_get_item_metadata`; **implemented 2026-06-17**: they are now lifted into the register
  `sources` table in `record_chunks` and exposed as list-source filters. **Do not** dump all
  `**fields` blindly — keep ISBN/ISSN/pages/volume in chunk metadata; the register gets the
  systematic-review filter set only. Full implementation spec:
  `docs/SPEC_W8_REGISTER_METADATA.md`.
- **Acceptance**: the §3b parity table is fully satisfiable with these in place; the mission's
  survey→select→drill runs end-to-end with no reliance on `coarse` or `parent_id`; a register
  source view can be filtered by `item_type`, `doi`, `language`, `tags` without a Chroma scan.

### W9 — Deployment & host: Sparky as home  *(new; decided 2026-06-13)*
**Re-Searcher v0.6 moves off Bambino (Windows / RTX 5090 workstation) to Sparky (NVIDIA DGX
Spark, ARM64 Linux / DGX OS, 128 GB unified memory, always-on, low-power).** Rationale: serving
wants *always-on + big memory + modest GPU* (Sparky), ingestion wants *raw GPU throughput*
(Bambino); only an always-on host can be "always-ready", and Sparky + Hermes-on-RocknRolla lets
missions run overnight with Bambino asleep.

- **Roles**: **Sparky = home** (Chroma + MCP serving + delta ingestion + dev, all `systemd`,
  always-ready). **Bambino = optional rebuild accelerator** — the one-time initial embed burst can
  borrow her 5090 by pointing `embedding.endpoint` at Bambino; deltas run on Sparky. **RocknRolla
  = always-on Hermes + canonical Zotero/Obsidian host.**
- **Inference server**: **vLLM is now the production inference path** for both embeddings
  and cross-encoder reranking (managed containers from config). LM Studio remains useful
  as an interactive/fallback OpenAI-compatible endpoint, but the v0.6 rebuild should be
  validated against vLLM on Sparky.
- **No data migration**: v0.6 builds a blank collection on Sparky's new ChromaDB — nothing to port.
- **Source-of-truth access (extractor backstops need raw PDFs, not only Zotero FT)**: Zotero is kept in sync across
  laptop/Bambino/RocknRolla by Zotero's own sync (all canonical); the Obsidian vault is synced
  Bambino↔RocknRolla by Syncthing (per-host `.obsidian/`). **Decision for Sparky: add Sparky as a
  sync target so it holds LOCAL copies** of (a) the Zotero **storage dir + `zotero.sqlite`** and
  (b) the vault — Re-Searcher reads *files* directly (PDF extractors on attachments, `zotero.sqlite` for
  metadata, `.md` for notes), so it needs the **data synced, not the GUI apps running**. Local
  copies beat the LAN/tailnet-to-RnR alternative for the heavy rebuild (1 gbps switched is the
  ceiling) and for overnight autonomy (no dependency on RnR serving mid-run). Caveat to handle:
  `zotero.sqlite` consistency while another host writes — snapshot/copy before a run, and the audit
  already tolerates a locked `zotero.sqlite`. (Alternative kept on file: reach RnR's Zotero/Obsidian
  local APIs over the tailnet — simpler, but LAN-limited and RnR-dependent.)
- **Portability port (absorb into the rebuild, cheapest now)**: verify ARM64 wheels for
  `chromadb` (+ `chromadb_rust_bindings`) and any optional heavy extractor dependencies on
  ARM64+Blackwell CUDA; replace the
  Windows launchers (NSSM, `.cmd`/`.ps1`) with `systemd` units + shell; make every path
  config-driven (no `C:\...` constants) — feeds W6.
- **Dev access**: run **Claude Code natively on Sparky** (Node CLI, ARM64 Linux) against the repo
  on the `v0.6-rebuild` branch — develop where it will run. **VS Code Remote-SSH into Sparky** is
  the equivalent alternative. (Bare SSH-from-elsewhere works but is clunky for iterative dev.)
- **Acceptance**: Chroma + MCP HTTP come up as always-ready `systemd` services on Sparky;
  Hermes-on-RocknRolla runs a mission end-to-end with Bambino powered off; a delta ingest runs
  fully on Sparky; the big-rebuild borrow-Bambino path works via config alone.

### W10 — Register as index ledger: reconciliation-driven updates  *(new; decided 2026-06-17)*
Finishes §3a's logic: the register owns **sync state**, not just identity/navigation. "What needs
processing" becomes a **diff between each source's current state and the register's recorded
per-unit fingerprints**, not an event tracked in a sidecar file. Detection stays in the adapters,
the decision moves to the register + a source-agnostic planner, Chroma strictly follows.

- **Ledger**: a register `index_units` table records, per smallest-independently-changing unit
  (Zotero `parent_meta`/`note`/`attachment`/`annotation`; Obsidian `vault_file`), the source-side
  fingerprint it was indexed at (`dateModified`, attachment `storageHash`/`storageModTime`,
  fulltext version, vault `mtime:size`). Fingerprints are **opaque to the register** — compared,
  never parsed — so the register stays source-agnostic.
- **Retire `zotero_delta_state.json`**: its library-version watermark becomes a fast-path *cursor*
  in register `meta`; the per-unit fingerprints are the correctness authority. `vault_files` (which
  already implements this pattern for Obsidian) is subsumed into `index_units`.
- **Granular updates fall out for free**: adding a note/annotation/tag to a 12,000-page item
  re-embeds only the changed unit, never the unchanged fulltext (the prior "sub-item precision"
  problem is a *consequence* of the ledger key, not a special case). Metadata-only changes
  (tags/collections) take a Chroma `update`, not a re-embed.
- **Parity gate**: the reconciler must reproduce today's change set (parent grain) in shadow on the
  test corpora before granular execution is switched on.
- **Acceptance**: an update run decides its work without querying Chroma; the Jung-note case skips
  the fulltext re-embed; deletions are detected from ledger-minus-world without `/deleted`; the
  sidecar file is gone. Full spec + phased plan: `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md`.

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
The move is also a **host migration** (Bambino→Sparky), which *helps* here — the new system is
built on a different box, so the old one keeps serving untouched until cutover.
1. Build v0.6 into a **fresh collection on Sparky's new ChromaDB** — Bambino's noisy
   `research_library` keeps serving Colin's day-to-day search throughout (no contention, no risk).
2. Validate the Sparky collection with the W7 harness + the mission acceptance scenario.
3. Point Hermes/MCP clients at Sparky's MCP endpoint; keep Bambino's stack until confidence is
   high; then retire it and reclaim space on Bambino (incl. the 121 GB `data_recovery_test`).

## 8. Phasing & gates (current order, 2026-06-17)
Phases are **iterative loops on the small test corpus**, not a waterfall — survey/classify/drill,
read what it shows, adjust, repeat. Each "gate" is a convergence check, not a one-shot. Only the
final rebuild commits to production, once the evidence has stopped surprising us.

The exact cold-start implementation checklist lives in
`docs/V0.6_REMAINING_ACTIONS_HANDOFF.md`. In short:
- **P1 — W2 single-grain chunking cutover.** Make the v0.6 default emit `mid` +
  `atomic` only; keep hierarchical chunking as legacy/experimental code, not the
  production path. Gate: fresh test-corpus build has no `fine` and no navigation
  dependence on `parent_id`.
- **P2 — W8 survey/aggregate-by-source retrieval mode.** Replace coarse survey
  behavior with mid-search grouped/ranked by source via the register. Gate: §3b
  broad-survey/select/drill parity works without `coarse`.
- **P3 — W1 extraction seam + provenance.** Wire the measured extractor plan
  behind a one-method seam, score output with the quality gate, and record
  extractor/quality provenance in the register. Gate: bad extraction is reportable
  per source and can feed a repair/re-extraction queue.
- **P4 — structured failure reporting.** Produce durable run reports for extraction,
  chunking, oversize, embedding, storage, registry, and ledger failures. Gate:
  "0 errors" cannot hide dropped documents/chunks, and the report doubles as corpus
  cleanup data.
- **P5 — W5 durability.** Add shared fsync-durable JSON/state writes. Gate:
  checkpoint/state files survive interruption as previous-valid or next-valid JSON.
- **P6 — W10 ledger parity suite.** Prove legacy-delta and ledger-execution parity
  on the full test Zotero + Obsidian corpora before flipping `ledger.execute` or
  retiring `zotero_delta_state.json` / `vault_files`.
- **P7 — Production rebuild + cutover (§7) + mission acceptance (§6).**

## 9. Risks & open questions
- **Embedding concurrency ceiling** is set by vLLM/Sparky GPU memory and request
  batching, not just config — sweep to find it; watch for OOM / throughput collapse.
- **ChromaDB version choice**: pick the latest stable that's proven to recover from an unclean
  WAL backlog; pin client==server; re-verify on the test build.
- **Cleaning aggressiveness**: reference-list/boilerplate stripping must be conservative —
  prefer flag-in-metadata over destructive removal where uncertain; the known-noisy corpus is
  the guardrail.
- **Coarse size vs embedding precision**: larger coarse aids framing but blurs the vector —
  the level-eval decides, not intuition.
- **Mission timing pressure** vs v0.6 timeline — the stopgap dedup is the release valve.
- **Extractor cost/coverage** — Zotero FT cache is cheap and usually passes; pdfminer
  is the cheap fallback; Marker/OCR is the rare expensive backstop. Measure on the
  test corpus before expanding the router. The seam means heavier extractors remain
  slottable for specific leak classes.
- **Determinism of the extractor** — stable IDs depend on stable text. Zotero FT is
  now accepted as the zero-cost first candidate when it passes quality gates, but
  provenance and quality reporting must make any drift/leak visible.
- **ARM64 build availability** — `chromadb_rust_bindings` and any optional heavy extractor
  stack must have working wheels; verify in the test build before betting the port on them
  (vLLM is now the production embedding/rerank path to validate on Sparky).
- **1 gbps source pull** — if sources aren't synced locally to Sparky, streaming the whole PDF
  corpus from RnR for the initial rebuild is LAN-capped (~100 MB/s); the local-sync decision (W9)
  avoids this. Deltas are small either way.
- **`zotero.sqlite` consistency** — syncing a live SQLite file across hosts can catch a mid-write
  state; snapshot before a run; the audit already tolerates a locked `zotero.sqlite`.

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
| 2026-06-13 | **Two-plane architecture**: register = control/navigation, Chroma = single-grain retrieval; genealogy leaves the chunks, lives in the register; functional profile preserved via §3b parity map |
| 2026-06-13 | **Retire `parent_id`-as-navigation**; single working grain `mid`; `coarse` only if the eval shows broad-survey recall needs it |
| 2026-06-13 | **Bet on Docling** as the single PDF extractor behind a one-method seam; do NOT pre-build a 3-tier stack; plug specific leaks (register-flagged) only where the nasty test corpus exposes them |
| 2026-06-13 | Superseded 2026-06-15: Zotero-FT was initially rejected as a default extractor because of drift/stable-ID concerns, coverage gaps, and lack of control |
| 2026-06-15 | **Extractor decision updated by Sparky bake-off**: route by quality gate instead of single Docling default. Zotero FT cache is the zero-cost first candidate; `pdfminer` is the cheap fallback/comparator; Marker/OCR is the rare expensive backstop for scans. Docling and PyMuPDF4LLM were evaluated and dropped from the active stack, but remain re-addable behind the future seam if a fixture class justifies them. See `docs/EXTRACTION_BAKEOFF_AND_ROUTING.md` |
| 2026-06-13 | Add register provenance (`extractor`, `extract_quality`) + survey-by-source mode + `heading_path` in enumeration (W8) to recover navigation without chunk hierarchy |
| 2026-06-13 | **Deployment moves to Sparky** (DGX Spark, ARM64 Linux, always-on, big unified memory) as Re-Searcher's home: Chroma + MCP serving + deltas + dev. Delivers "always-ready" + overnight missions with Bambino asleep |
| 2026-06-13 | **Bambino = optional rebuild accelerator** only (borrow her 5090 for the one-time embed burst via `embedding.endpoint`); **RocknRolla = always-on Hermes + canonical source host** |
| 2026-06-13 | Superseded 2026-06-17: LM Studio already headless/systemd on Sparky was initially treated as sufficient inference infrastructure. vLLM later replaced it as the production embedding/rerank backend for performance; LM Studio remains an interactive/fallback endpoint. Blank-DB rebuild → no data migration still stands |
| 2026-06-13 | **Sources synced locally to Sparky** (Zotero storage+`zotero.sqlite`, Obsidian vault) so PDF extractors read raw attachments locally (overnight-autonomous, not 1 gbps-limited); RnR-local-API access kept as the fallback |
| 2026-06-13 | Port to ARM64 Linux (systemd not NSSM; config-driven paths) absorbed into the v0.6 rebuild; dev via Claude Code native on Sparky (or VS Code Remote-SSH) |
| 2026-06-17 | **Annotations stay `atomic`** (not folded into recursive): one human-curated unit; oversize guard already backstops the truncation risk. Add `has_comment` flag (+ optional `color`/`type`) for screening. See W2 |
| 2026-06-17 | **Register gains systematic-review selection metadata** (W8): `item_type`/"kind" (needs new extraction — resolve `typeName`), `doi`, `abstract`, `tags`, `venue`, `language`. Register is the filter plane for survey→filter→re-ask; (2)–(5) already extracted, just lifted into `sources`. Spec: `docs/SPEC_W8_REGISTER_METADATA.md` |
| 2026-06-17 | **Register is the index ledger** (W10): "what needs processing" = diff(source current state, register per-unit fingerprints); detection in adapters, decision in register/planner, Chroma follows. Retire `zotero_delta_state.json` (version → `meta` cursor); subsume `vault_files`. Granular Zotero updates (the Jung-note case) become a property of the ledger key, not a special case. Spec + phased plan: `docs/SPEC_REGISTER_AS_INDEX_LEDGER.md` |
| 2026-06-17 | **vLLM becomes production inference**: Qwen3-Embedding-0.6B via vLLM is the production embedder; vLLM `/rerank` cross-encoder is the production rerank path. Keep `context_length`/`max_model_len` above the oversize guard. |
| 2026-06-17 | **Remaining v0.6 action sequence recorded**: single-grain chunking, survey-by-source mode, extraction seam/provenance, structured failure reporting, fsync-durable state writes, and ledger parity suite before sidecar retirement. See `docs/V0.6_REMAINING_ACTIONS_HANDOFF.md`. |
