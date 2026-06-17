# Re-Searcher Legacy Feature Implementation Plan

**Created**: 2026-01-08
**Updated**: 2026-01-08
**Status**: Active
**Branch**: legacy-feature-work

---

## Overview

This plan consolidates the parallel extraction, progress display, and safety rails work into a single incremental implementation guide. Designed for multi-session work with clear checkpoints.

### Goals (Priority Order)

1. **Prevent silent misconfiguration** — No more overnight runs with wrong settings
2. **Eliminate monster chunks** — Hard caps + split policy before embedding (prevents corrupt embeddings)
3. **Improve operator visibility** — Rich progress UI with stages, ETA, error counts
4. **Speed up extraction** — Parallel PDF extraction using CPU-count workers
5. **Obsidian metadata completeness** — Tags, links, frontmatter propagation
6. **Minimal test coverage** — Catch regressions without overbuilding

### Recommended Execution Order

**Critical path** (do these first to prevent wasted runs):
1. **Stage 0** — Preflight validation (prevents misconfigured overnight runs)
2. **Stage 3** — Oversize guard (prevents corrupt embeddings)

**Performance/UX** (then these):
3. **Stage 1** — Progress UI
4. **Stage 2** — Parallel extraction

**Polish** (finally):
5. **Stage 4** — Obsidian metadata
6. **Stage 5** — Tests

### Design Principles

- **Stage isolation**: extraction → chunking → embedding → storage (clear boundaries)
- **Config-first**: No hard-coded behaviours; defaults allowed but overrideable
- **Fail fast**: Abort on safety violations (router disabled, truncation risk)
- **Determinism**: Stable IDs, reproducible ordering, resumable indexing
- **Composable**: Adding a chunker/source/step won't break parallel extraction or progress

### Open Questions Resolved

| Question | Decision |
|----------|----------|
| Parallelize Obsidian? | No — already fast enough |
| Default thread count | `os.cpu_count()` with config override |
| Individual PDF progress bars | Yes — for large files |
| Log file for detailed output | Deferred to future enhancement |

---

## Implementation Stages

Each stage is self-contained and can be completed in one session. Mark checkboxes as you complete items.

---

## Stage 0: Operational Discipline (Preflight + Verification)

**Purpose**: Prevent another overnight run with wrong config.
**Estimated scope**: ~2 hours
**Dependencies**: None

### 0.1 Config validation at startup

**File**: `src/preflight.py` (new)

- [x] Add `validate_config(config: dict, config_path: Path)` function
- [x] **Print resolved config path** — show exactly which file is being used
- [x] **Schema validation**: If config has no `chunking:` block at all → abort (unsafe default)
  - This catches "config exists but doesn't include new section → silently defaults"
- [x] Log "effective settings header" at startup:
  ```
  ═══════════════════════════════════════════════════════════════
  Re-Searcher Configuration
  ═══════════════════════════════════════════════════════════════
  Config path: C:/path/to/config.yaml (resolved)
  Git commit:  abc1234

  Chunking:
    router_enabled:       true        ← VALIDATED
    id_strategy:          stable_hash
    chunk_size:           512 / overlap: 50
    max_tokens_per_chunk: 7000
    oversize_policy:      split
    token_estimator:      heuristic

  Extraction:
    parallel: true
    workers:  8 (auto = CPU count)

  Embedding:
    provider:       lmstudio
    model:          bge-m3
    context_length: 8192

  Storage:
    collection: research_library
    host:       localhost:8000
  ═══════════════════════════════════════════════════════════════
  ```
- [x] **Abort conditions** (non-test runs):
  - No `chunking:` block in config → abort unless `--allow-default-config`
  - `router_enabled: false` without `--allow-legacy-chunking` → abort
  - `max_tokens_per_chunk` not set and embedder context unknown → warn loudly
- [x] Add CLI flags to `scripts/index.py`:
  - `--allow-legacy-chunking` — bypass router requirement
  - `--allow-default-config` — allow missing chunking block / implicit config path
  - `--dry-run` — validate config and print header without running

### 0.2 Post-run verification script

**File**: `scripts/verify_run.py` (new)

- [x] Connect to Chroma collection
- [x] Print chunk_level distribution (fine/mid/coarse counts)
- [x] Sample fine chunks → fetch parent → verify:
  - Parent exists
  - `parent.chunk_level == mid`
  - `parent.source_id == child.source_id`
- [x] Check Obsidian metadata coverage:
  - Count chunks with `heading_path`
  - Count chunks with `contains_code`
  - Count chunks with `zotero_key` (if applicable)
- [x] Assert no IDs contain `-chunk-` (legacy format)
- [x] Print PASS/FAIL summary

### Stage 0 Checkpoint

```bash
# Test preflight validation
python scripts/index.py --dry-run  # Should show config header

# Verify against current collection
python scripts/verify_run.py
```

---

## Stage 1: Rich Progress Display

**Purpose**: Operator visibility with thread-safe progress updates.
**Estimated scope**: ~3 hours
**Dependencies**: Stage 0 (optional but recommended)

### 1.1 Progress module

**File**: `src/progress.py` (new)

- [x] Create `IndexingStage` enum:
  ```python
  class IndexingStage(Enum):
      INITIALIZING = "Initializing"
      FETCHING = "Fetching documents"
      CHUNKING = "Chunking documents"
      EMBEDDING = "Generating embeddings"
      STORING = "Storing vectors"
      COMPLETE = "Complete"
  ```

- [x] Create `SourceStats` dataclass:
  ```python
  @dataclass
  class SourceStats:
      name: str
      total: int = 0
      processed: int = 0
      new: int = 0
      updated: int = 0
      skipped: int = 0
      errors: int = 0
  ```

- [x] Create `TimingTracker` class:
  - `started_at: datetime`
  - `item_times: deque` (rolling window of last 100)
  - `record_item(elapsed: float)`
  - `average_time() -> float`
  - `estimate_remaining(items_left: int) -> timedelta`

- [x] Create `ProgressDisplay` class:
  - Thread-safe with `threading.Lock`
  - Uses `rich.Live` for dynamic display
  - Methods: `start()`, `stop()`, `set_stage()`, `update_source()`, `set_activity()`
  - **Individual file progress**: For PDFs > 10MB, show a sub-progress bar

- [x] Display format (interactive TTY):
  ```
  Re-Searcher Indexing Pipeline
  ══════════════════════════════════════════════════════════════
  Stage 2/4: Fetching documents

    Zotero    ████████████░░░░░░░░  127/180 items    00:05:32
    Obsidian  ████████████████████  720/720 notes    00:00:04

    Current: Extracting Whitehead_ProcessAndReality.pdf (45MB)
             ████████░░░░░░░░░░░░  35%

    Stats: 45 new │ 12 updated │ 68 skipped │ 2 errors

    Time: 00:05:36 elapsed │ ETA: ~00:02:15 remaining
  ══════════════════════════════════════════════════════════════
  ```

### 1.2 Non-interactive fallback (CI/redirected logs)

**Important**: `rich.Live` breaks non-interactive logging.

- [x] **Auto-detect TTY**: If `sys.stdout.isatty()` is False → auto-disable Live
- [x] Create `PlainProgressDisplay` class:
  - Prints periodic single-line updates (every N seconds or N items)
  - Format: `[Stage 2/4] Zotero: 127/180 (70%) | 45 new, 2 errors | ETA: 00:02:15`
  - No cursor movement, no escape codes
- [x] Add `--plain-progress` CLI flag to force plain mode even on TTY
- [x] Factory function to select display type:
  ```python
  def create_progress_display(mode: str = "auto") -> ProgressDisplay:
      if mode == "plain" or (mode == "auto" and not sys.stdout.isatty()):
          return PlainProgressDisplay()
      return RichProgressDisplay()
  ```

### 1.3 Pipeline integration

**File**: `src/pipeline.py`

- [x] Add `progress_mode: str = "auto"` parameter to `__init__` (auto/rich/plain/quiet)
- [x] Initialize appropriate ProgressDisplay based on mode
- [x] Add stage transitions in `run()`:
  - `INITIALIZING` → `FETCHING` → `CHUNKING` → `EMBEDDING` → `STORING` → `COMPLETE`
- [x] Wrap run in try/finally to ensure `progress.stop()` is called

### 1.4 Source callback interface

**File**: `src/sources/base.py`

- [x] Add optional `progress_callback: Callable[[dict], None]` to base class
- [x] Define callback signature: `{"event": str, "source": str, ...}`

**File**: `src/sources/zotero.py`

- [x] Wire progress callback for extraction events

**File**: `src/sources/obsidian.py`

- [x] Wire progress callback for note processing events

### 1.5 CLI updates

**File**: `scripts/index.py`

- [x] Add `--quiet` flag to disable all progress output
- [x] Add `--plain-progress` flag to force plain mode
- [x] Pass appropriate `progress_mode` to pipeline

### Stage 1 Checkpoint

```bash
# Test with small corpus (interactive)
python scripts/index.py --limit 20

# Verify plain mode works
python scripts/index.py --limit 20 --plain-progress

# Verify quiet mode works
python scripts/index.py --limit 20 --quiet

# Verify auto-detection (pipe to file)
python scripts/index.py --limit 20 > output.log 2>&1
# Check output.log has plain format, no escape codes
```

---

## Stage 2: Parallel PDF Extraction

**Purpose**: Utilize multiple CPU cores for PDF extraction.
**Estimated scope**: ~3 hours
**Dependencies**: Stage 1 (for progress callbacks)

### 2.1 Configuration

**File**: `config.yaml`

- [x] Add extraction config section:
  ```yaml
  extraction:
    parallel: true
    workers: auto  # "auto" = os.cpu_count(), or explicit int
    mode: thread   # thread | process (thread recommended)
  ```

- [x] Deprecate `zotero.max_extraction_threads` (map to `extraction.workers` for backwards compat)

### 2.2 Extraction dataclasses

**File**: `src/sources/zotero.py`

- [x] Add dataclasses:
  ```python
  @dataclass
  class ExtractionTask:
      file_path: Path
      attachment_id: int
      attachment_key: str
      filename: str
      content_type: str
      file_size_mb: float
      zotero_item_key: str
      index: int  # For deterministic ordering

  @dataclass
  class ExtractionResult:
      task: ExtractionTask
      text: Optional[str]
      error: Optional[str]
      elapsed_seconds: float
  ```

### 2.3 Parallel extraction implementation

**File**: `src/sources/zotero.py`

- [x] Implement `_extract_single_attachment(task: ExtractionTask) -> ExtractionResult`:
  - Stateless, thread-safe
  - Calls existing subprocess extraction
  - Handles exceptions gracefully

- [x] Refactor `_process_attachments()`:
  - **Sort tasks by stable key FIRST** (attachment_key or file_path) before assigning indices
    - This ensures "same run, same IDs, same ordering" assumption holds
  - Execute with `ThreadPoolExecutor(max_workers=workers)`
  - **Deterministic output**: Store results in dict keyed by `task.index`, yield in order
  - Call `progress_callback` as futures complete

  ```python
  def _process_attachments(self, conn, item_id, metadata_base) -> Iterator[Document]:
      # 1. Collect all tasks
      tasks = list(self._collect_attachment_tasks(conn, item_id))

      # 2. Sort by stable key BEFORE assigning indices
      tasks.sort(key=lambda t: t.attachment_key)  # or t.file_path
      for i, task in enumerate(tasks):
          task.index = i

      # 3. Execute in parallel
      results = {}
      with ThreadPoolExecutor(max_workers=self._get_worker_count()) as executor:
          futures = {executor.submit(self._extract_single, t): t for t in tasks}
          for future in as_completed(futures):
              result = future.result()
              results[result.task.index] = result
              self._emit_progress(result)

      # 4. Yield in deterministic order
      for i in range(len(tasks)):
          if results[i].text:
              yield self._create_document(results[i], metadata_base)
  ```

- [x] Get worker count:
  ```python
  def _get_worker_count(self) -> int:
      config_workers = self.extraction_config.get("workers", "auto")
      if config_workers == "auto":
          return os.cpu_count() or 4
      return int(config_workers)
  ```

### 2.4 Progress integration

- [x] Update progress display with:
  - Current file being extracted
  - Per-file progress bar for large PDFs (> 10MB) — based on elapsed vs. estimated time
  - Error count updates in real-time

### Stage 2 Checkpoint

```bash
# Run extraction on representative sample
python scripts/index.py --limit 50

# Success criteria (NOTE: CPU may stay low if I/O-bound):
# - Extraction throughput improves 2-4× vs sequential baseline
# - Multiple workers active simultaneously (visible in progress events)
# - Documents yielded in deterministic order (run twice, compare order)
# - Progress updates show parallel activity (multiple "extracting" events close together)

# To verify determinism:
python scripts/index.py --limit 20 --dry-run > run1.txt
python scripts/index.py --limit 20 --dry-run > run2.txt
diff run1.txt run2.txt  # Should be identical
```

---

## Stage 3: Monster Chunk Elimination

**Purpose**: Prevent embedder truncation and ensure chunk size bounds.
**Estimated scope**: ~4 hours
**Dependencies**: None (can be done in parallel with Stage 1-2)

> **CRITICAL**: This stage prevents corrupt embeddings. Run this BEFORE any full corpus indexing.

### 3.1 Configuration

**File**: `config.yaml`

- [x] Add safety config:
  ```yaml
  chunking:
    max_tokens_per_chunk: 7000       # Leave margin for 8192 context
    oversize_policy: split           # split | truncate | skip
    token_estimator: heuristic       # heuristic | model_tokenizer

  embedding:
    context_length: 8192             # BGE-M3 context window
  ```

- [x] **Safety margin calculation**: When using heuristic estimator, effective max = `context_length * 0.85`
  - This accounts for tokenizer variance (chars/4 is approximate)
  - For 8192 context → max ~6963 tokens → round to 7000

### 3.2 Token estimation utility

**File**: `src/processing/token_utils.py` (new)

- [x] Add configurable token estimation:
  ```python
  def create_token_estimator(method: str = "heuristic") -> Callable[[str], int]:
      if method == "heuristic":
          return lambda text: len(text) // 4  # Conservative estimate
      elif method == "model_tokenizer":
          # Optional: use LM Studio token count endpoint if available
          return _model_tokenizer_estimate
      else:
          raise ValueError(f"Unknown token estimator: {method}")
  ```

- [x] If LM Studio exposes a token-count endpoint, implement `_model_tokenizer_estimate` (deferred - falls back to heuristic)

### 3.3 Oversize guard

**File**: `src/processing/oversize_guard.py` (new)

**IMPORTANT**: Operate on existing types (`text: str`, `metadata: dict`), NOT a new `Chunk` type.
This avoids introducing a new type late in the refactor.

- [x] Implement `OversizeGuard` class:
  ```python
  class OversizeGuard:
      def __init__(self, max_tokens: int, policy: str, estimator: Callable[[str], int]):
          self.max_tokens = max_tokens
          self.policy = policy  # split | truncate | skip
          self.estimate_tokens = estimator
          self.stats = {"passed": 0, "split": 0, "truncated": 0, "skipped": 0}

      def process(self, chunks: List[Tuple[str, dict]]) -> List[Tuple[str, dict]]:
          """Process (text, metadata) tuples, handling oversize chunks."""
          result = []
          for text, metadata in chunks:
              tokens = self.estimate_tokens(text)
              if tokens <= self.max_tokens:
                  self.stats["passed"] += 1
                  result.append((text, metadata))
              else:
                  result.extend(self._handle_oversize(text, metadata, tokens))
          return result
  ```

- [x] Implement `_handle_oversize()`:
  ```python
  def _handle_oversize(self, text: str, metadata: dict, tokens: int) -> List[Tuple[str, dict]]:
      if self.policy == "split":
          self.stats["split"] += 1
          return self._recursive_split(text, metadata)
      elif self.policy == "truncate":
          self.stats["truncated"] += 1
          logger.warning(f"Truncating chunk: {tokens} tokens → {self.max_tokens}")
          return [(self._truncate(text), {**metadata, "truncated": True})]
      else:  # skip
          self.stats["skipped"] += 1
          logger.warning(f"Skipping oversize chunk: {tokens} tokens")
          return []
  ```

- [x] Implement `_recursive_split()`:
  - Split on paragraph boundaries (`\n\n`) first
  - Fall back to sentence boundaries (`. `, `? `, `! `)
  - Last resort: fixed character window (max_tokens * 3 chars)
  - Preserve metadata on children
  - Add `oversize_split: true` and `split_from_chunk_id` metadata

### 3.4 Pipeline integration

**File**: `src/pipeline.py`

- [x] **CRITICAL PLACEMENT**: Guard runs AFTER all routing/chunking, BEFORE embedding
  - This catches output from ALL chunkers (Atomic/Markdown/Hierarchical/Text)
  - The guard is the **last line of defence**

  ```python
  # Existing flow: documents → chunk_with_metadata → ids → embed
  # Insert guard at the exact point where chunks are finalised:

  chunks = self.chunk_with_metadata(documents)  # All chunkers, router, etc.
  chunks = self.oversize_guard.process(chunks)  # NEW - last defence
  chunks = self.assign_ids(chunks)
  embeddings = self.embed(chunks)
  ```

- [x] Log oversize handling statistics at end of run:
  ```
  Oversize Guard: 45,231 passed | 12 split | 0 truncated | 0 skipped
  ```

### 3.5 Root cause logging (find WHY monster chunks appear)

**File**: `src/processing/router.py`

- [x] When any chunk exceeds `chunk_size` by > 20%, log detailed info:
  ```python
  if estimated_tokens > chunk_size * 1.2:
      logger.warning(
          f"Oversize chunk created: "
          f"source_id={source_id}, "
          f"source_type={source_type}, "
          f"selected_chunker={chunker_name}, "
          f"estimated_tokens={estimated_tokens}, "
          f"text_preview={text[:120]!r}, "
          f"reason={why_split_failed}"  # e.g., "no separators found"
      )
  ```

- [x] This logging will immediately reveal:
  - PDF extraction producing unbroken text
  - Markdown sections treated as single slabs
  - Router not being used
  - Missing separator characters

### 3.6 Root cause fixes in chunkers

**Files**: `src/processing/chunkers/*.py`

- [ ] Review recursive splitter — ensure fallback to fixed window when no separators found (deferred - guard handles this)
- [ ] Review markdown chunker — ensure `max_section_tokens` is enforced (deferred - guard handles this)
- [ ] Review hierarchical chunker — ensure fine/mid/coarse all respect bounds (deferred - guard handles this)

**Goal**: Chunkers should aim to NEVER emit pathological sizes. The guard is backup only.

### Stage 3 Checkpoint

```bash
# Run with verbose logging
python scripts/index.py --limit 100 --verbose

# Verify:
# - No "Created a chunk of size X longer than Y" warnings
# - No embedder truncation warnings
# - Check logs for any oversize guard activations
# - Review any root-cause warning logs to identify problematic sources

# Acceptance: ZERO embedder truncation warnings in a full run
```

---

## Stage 4: Obsidian Metadata Completeness

**Purpose**: Ensure rich metadata propagates to all Obsidian chunks.
**Estimated scope**: ~2 hours
**Dependencies**: None

### 4.1 Frontmatter parsing

**File**: `src/sources/obsidian.py`

- [x] Parse YAML frontmatter into metadata dict
- [x] Handle common fields: `tags`, `aliases`, `zotero_key`, `date`, custom fields

### 4.2 Tag extraction

- [x] Extract YAML tags: `tags: [tag1, tag2]`
- [x] Extract inline tags: `#tag-name`
- [x] Merge into `metadata.tags` list

### 4.3 Link extraction

- [x] Extract wikilinks: `[[Page Name]]`, `[[Page Name|Display]]`
- [x] Store in `metadata.links_out` list
- [x] Compute backlinks if feasible (may require two-pass) — deferred, wikilinks stored instead

### 4.4 Code block handling

- [x] Set `metadata.contains_code: true` if note has fenced code blocks
- [x] Ensure chunker doesn't split inside code blocks — handled by markdown chunker

### 4.5 Zotero key propagation

- [x] If frontmatter contains `zotero_key`, propagate to all chunks from that note

### Stage 4 Checkpoint

```bash
# Verify metadata on Obsidian chunks
python scripts/verify_run.py

# Check output includes:
# - heading_path coverage
# - contains_code coverage
# - tags/links extraction
```

---

## Stage 5: Minimal Test Coverage

**Purpose**: Catch regressions without overbuilding.
**Estimated scope**: ~2 hours
**Dependencies**: Stages 0-4 complete

### 5.1 Unit tests

**File**: `tests/unit/test_chunk_id_stability.py`

- [x] `test_stable_chunk_id_is_deterministic` — same input → same ID
- [x] `test_stable_chunk_id_changes_with_text` — different input → different ID
- [x] `test_attach_parent_ids_scoping_respects_source_id` — respects source_id boundaries
- [x] `test_attach_parent_ids_no_overwrite` — doesn't clobber existing parent_id

**File**: `tests/unit/test_router_routing.py`

- [x] `test_router_routes_zotero_annotations_to_atomic`
- [x] `test_router_routes_obsidian_to_markdown`
- [x] `test_router_routes_huge_docs_to_hierarchical`
- [x] `test_router_fallback_to_default`

**File**: `tests/unit/test_oversize_guard.py`

- [x] `test_splits_large_chunk`
- [x] `test_passes_small_chunk`
- [x] `test_truncate_policy`
- [x] `test_skip_policy`

### 5.2 Integration tests

**File**: `tests/integration/test_parent_fetch.py`

- [x] `test_chroma_parent_fetch` — get_by_ids works for parent lookup
- [x] `test_reindex_stable_count` — rerunning doesn't balloon collection

### 5.3 Smoke test

**File**: `tests/test_smoke.py`

- [x] `test_small_run_with_router_produces_hierarchical_chunks` — router enabled, hierarchical chunks

### Stage 5 Checkpoint

```bash
pytest tests/ -v

# All tests should pass
```

---

## Progress Tracking

Use this section to track multi-session progress.

### Session Log

| Date | Session | Stages Worked | Commits | Notes |
|------|---------|---------------|---------|-------|
| 2026-01-08 | 1 | Planning | — | Created implementation plan |
| 2026-01-08 | 2 | Stage 0 | 0ac6156 | Preflight validation + verify_run.py complete |
| 2026-01-08 | 2 | Stage 3 | d8899d6 | Oversize guard + root cause logging complete |
| 2026-01-08 | 3 | Stage 1 | — | Rich progress display, PlainProgressDisplay, CLI flags |
| 2026-01-08 | 3 | Stage 2 | — | Parallel PDF extraction with ThreadPoolExecutor |
| 2026-01-08 | 3 | Stage 4 | 3d1a4b6 | Obsidian metadata: aliases, contains_code, heading_path |
| 2026-01-08 | 4 | Stage 5 | — | Unit tests for ID, router, oversize guard; integration + smoke tests |

### Stage Status

| Stage | Status | Started | Completed |
|-------|--------|---------|-----------|
| 0 - Preflight | ✅ Complete | 2026-01-08 | 2026-01-08 |
| 1 - Progress UI | ✅ Complete | 2026-01-08 | 2026-01-08 |
| 2 - Parallel Extraction | ✅ Complete | 2026-01-08 | 2026-01-08 |
| 3 - Oversize Guard | ✅ Complete | 2026-01-08 | 2026-01-08 |
| 4 - Obsidian Metadata | ✅ Complete | 2026-01-08 | 2026-01-08 |
| 5 - Tests | ✅ Complete | 2026-01-08 | 2026-01-08 |

Legend: ⬜ Not started | 🟡 In progress | ✅ Complete

---

## Acceptance Criteria (Definition of Done)

### Stage 0 — Preflight
- [ ] Full corpus run refuses to start if `chunking:` block missing (unless `--allow-default-config`)
- [ ] Full corpus run refuses to start if router disabled (unless `--allow-legacy-chunking`)
- [ ] Config header printed at startup showing resolved path and key settings
- [ ] `verify_run.py` passes on indexed collection

### Stage 1 — Progress UI
- [ ] Progress UI shows stages, per-source counts, current item, elapsed, ETA, errors
- [ ] Plain mode works when stdout is not a TTY (no escape codes in redirected logs)
- [ ] `--quiet` flag disables all progress output

### Stage 2 — Parallel Extraction
- [ ] Extraction throughput improves 2-4× on representative sample
- [ ] Multiple workers active simultaneously (visible in progress events)
- [ ] Documents yielded in deterministic order (identical across runs)

### Stage 3 — Oversize Guard
- [ ] No "Created a chunk of size … longer than …" warnings (or handled by guard)
- [ ] No embedder truncation warnings during embedding
- [ ] Oversize guard stats logged at end of run
- [ ] Root-cause logging identifies problematic sources

### Stage 4 — Obsidian Metadata
- [ ] `verify_run.py` shows good coverage for `heading_path`, `contains_code`, `tags`

### Stage 5 — Tests
- [ ] All unit and integration tests pass

### Overall
- [ ] Parent integrity checks pass (child/parent same source_id)
- [ ] Rerunning without changes does not balloon Chroma collection count

---

## Quick Reference

### Files to Create

| File | Stage | Purpose |
|------|-------|---------|
| `src/preflight.py` | 0 | Config validation, schema checks |
| `scripts/verify_run.py` | 0 | Post-run verification |
| `src/progress.py` | 1 | Rich + plain progress display |
| `src/chunking/oversize_guard.py` | 3 | Chunk size enforcement |
| `src/chunking/utils.py` | 3 | Token estimation utilities |
| `tests/test_chunking.py` | 5 | Chunking unit tests |
| `tests/test_router.py` | 5 | Router unit tests |
| `tests/test_oversize_guard.py` | 5 | Oversize guard tests |
| `tests/test_integration.py` | 5 | Integration tests |
| `tests/test_smoke.py` | 5 | Smoke tests |

### Files to Modify

| File | Stages | Changes |
|------|--------|---------|
| `src/pipeline.py` | 0,1,3 | Preflight call, progress integration, oversize guard |
| `src/sources/zotero.py` | 1,2 | Progress callbacks, parallel extraction |
| `src/sources/obsidian.py` | 1,4 | Progress callbacks, metadata extraction |
| `src/sources/base.py` | 1 | Callback interface |
| `src/chunking/*.py` | 3 | Root-cause logging for oversize chunks |
| `scripts/index.py` | 0,1 | CLI flags (--dry-run, --quiet, --plain-progress, etc.) |
| `config.yaml` | 0,2,3 | New config sections |

### Config Changes Summary

```yaml
# New sections to add to config.yaml:

extraction:
  parallel: true
  workers: auto          # "auto" = os.cpu_count(), or int

chunking:
  max_tokens_per_chunk: 7000
  oversize_policy: split    # split | truncate | skip
  token_estimator: heuristic  # heuristic | model_tokenizer

embedding:
  context_length: 8192      # BGE-M3 context window
```
