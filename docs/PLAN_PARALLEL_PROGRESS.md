# Implementation Plan: Parallel Extraction + Rich Progress Display

**Created**: 2026-01-08
**Status**: Draft - awaiting refinements
**Branch**: vnext-features

---

## Problem Statement

### Issue 1: Slow Sequential Extraction

The initial document extraction phase is very slow. During overnight indexing:
- CPU usage is only 4-7% during extraction
- Each PDF is extracted sequentially (one at a time)
- The config setting `max_extraction_threads: 4` exists but is **completely unused**
- `ThreadPoolExecutor` is imported in `src/sources/zotero.py` but never instantiated

**Evidence from code** (`src/sources/zotero.py` line 23):
```python
self.max_extraction_threads = self.zotero_config.get("max_extraction_threads", 4)
# This variable is loaded but NEVER referenced anywhere else
```

### Issue 2: Poor Progress Visibility

Current progress output is minimal:
```
[1/4] Fetching documents from sources...
  Extracting: Whitehead.pdf (0.1MB)... OK (36979 chars)
  Extracting: NextFile.pdf (0.4MB)... OK (52370 chars)
  ...
```

Missing information:
- Overall progress (x of y items)
- Time elapsed / ETA
- Counts by status (new, updated, skipped, errors)
- Current stage in pipeline
- Projections based on average processing time

---

## Current Architecture

### Extraction Flow

```
ZoteroSource.fetch_documents()
    └── _process_item() for each item
        └── _process_attachments() ← SEQUENTIAL, bottleneck here
            └── extract_text() ← subprocess with 60s timeout
```

### Key Files

| File | Purpose |
|------|---------|
| `src/sources/zotero.py` | Zotero data source, PDF extraction |
| `src/sources/obsidian.py` | Obsidian vault source |
| `src/pipeline.py` | Main orchestration |
| `src/indexing.py` | Progress tracking (JSON persistence) |
| `src/extract_text.py` | Subprocess-based PDF extraction |

### Thread Safety Analysis

1. **PDF extraction is subprocess-based**: Each `extract_text()` call spawns an isolated subprocess - inherently thread-safe
2. **Chunking is decoupled**: Happens after all documents fetched, operates on Document objects
3. **No shared mutable state**: Each extraction operates on different file paths
4. **Chunkers are stateless**: Safe for any ordering of documents

---

## Implementation Plan

### Phase 1: Progress Display Infrastructure

**New file: `src/progress.py`**

Create a progress display system using the `rich` library (already in requirements.txt):

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional
import threading

class IndexingStage(Enum):
    INITIALIZING = "Initializing"
    FETCHING = "Fetching documents"
    CHUNKING = "Chunking documents"
    EMBEDDING = "Generating embeddings"
    STORING = "Storing in vector database"
    COMPLETE = "Complete"

@dataclass
class SourceStats:
    total: int = 0
    processed: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0

@dataclass
class TimingTracker:
    started_at: datetime = field(default_factory=datetime.now)
    item_times: list = field(default_factory=list)
    max_samples: int = 100  # Rolling window

    def record_item(self, elapsed: float):
        self.item_times.append(elapsed)
        if len(self.item_times) > self.max_samples:
            self.item_times.pop(0)

    def average_time(self) -> float:
        return sum(self.item_times) / len(self.item_times) if self.item_times else 0

    def estimate_remaining(self, items_left: int) -> float:
        return self.average_time() * items_left

class ProgressDisplay:
    """Thread-safe progress display using rich.Live."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.stage = IndexingStage.INITIALIZING
        self.stage_number = 0
        self.total_stages = 4
        self.sources: Dict[str, SourceStats] = {}
        self.timing = TimingTracker()
        self.current_activity = ""
        self._lock = threading.Lock()
        self._live = None

    def start(self): ...
    def stop(self): ...
    def set_stage(self, stage, num, total): ...
    def update_source(self, name, **kwargs): ...
    def set_activity(self, msg): ...
```

**Display format:**
```
Re-Searcher Indexing Pipeline
================================================================================
Stage 2/4: Fetching documents

  Zotero    [====================--------]  127/180 items    00:05:32
  Obsidian  [============================]  720/720 notes    00:00:04

  Current: Extracting Whitehead_ProcessAndReality.pdf (45MB)

  Stats: 45 new | 12 updated | 68 skipped | 2 errors

  Time: 00:05:36 elapsed | ETA: ~00:02:15 remaining
================================================================================
```

---

### Phase 2: Parallel PDF Extraction

**File: `src/sources/zotero.py`**

1. **Add dataclasses for task/result** (after imports):

```python
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

@dataclass
class ExtractionTask:
    file_path: Path
    attachment_id: int
    attachment_key: str
    filename: str
    content_type: str
    file_size_mb: float

@dataclass
class ExtractionResult:
    task: ExtractionTask
    text: Optional[str]
    error: Optional[str]
    elapsed_seconds: float
```

2. **Add thread-safe extraction method**:

```python
def _extract_single_attachment(self, task: ExtractionTask) -> ExtractionResult:
    """Thread-safe extraction of a single attachment."""
    import time
    start = time.time()
    try:
        text = extract_text(task.file_path)
        return ExtractionResult(
            task=task,
            text=text if text and text.strip() else None,
            error=None,
            elapsed_seconds=time.time() - start
        )
    except Exception as e:
        return ExtractionResult(
            task=task,
            text=None,
            error=str(e),
            elapsed_seconds=time.time() - start
        )
```

3. **Refactor `_process_attachments()`**:

```python
def _process_attachments(self, conn, item_id, metadata_base) -> Iterator[Document]:
    """Process attachments with parallel extraction."""
    tasks = self._collect_attachment_tasks(conn, item_id)

    if not tasks:
        return

    with ThreadPoolExecutor(max_workers=self.max_extraction_threads) as executor:
        futures = {
            executor.submit(self._extract_single_attachment, task): task
            for task in tasks
        }

        for future in as_completed(futures):
            result = future.result()

            # Progress callback (optional)
            if self.progress_callback:
                self.progress_callback(result)

            # Yield document if extraction succeeded
            if result.text:
                yield self._create_document_from_result(result, metadata_base)
```

4. **Add progress callback to constructor**:

```python
def __init__(self, config: Dict[str, Any], progress_callback: Optional[Callable] = None):
    super().__init__(config)
    self.progress_callback = progress_callback
    # ... rest of init
```

---

### Phase 3: Pipeline Integration

**File: `src/pipeline.py`**

1. **Add ProgressDisplay to pipeline**:

```python
from .progress import ProgressDisplay, IndexingStage

class ResearchRAGPipeline:
    def __init__(self, config_path: Path, show_progress: bool = True):
        # ... existing init ...
        self.show_progress = show_progress
        self.progress = ProgressDisplay(enabled=show_progress)
```

2. **Pass callbacks to sources**:

```python
def _initialize_sources(self) -> List:
    sources = []

    zotero = ZoteroSource(
        self.config,
        progress_callback=self._on_extraction_progress
    )
    # ... etc

def _on_extraction_progress(self, result):
    """Handle extraction progress updates."""
    source = "Zotero"
    if result.error:
        self.progress.update_source(source, errors=1)
    else:
        self.progress.update_source(source, processed=1)
    self.progress.set_activity(f"Extracted: {result.task.filename}")
```

3. **Update run() with stages**:

```python
def run(self, force_reindex: bool = False):
    self.progress.start()
    try:
        self.progress.set_stage(IndexingStage.FETCHING, 1, 4)
        documents = self._fetch_all_documents()

        self.progress.set_stage(IndexingStage.CHUNKING, 2, 4)
        self._process_batches(documents)

        self.progress.set_stage(IndexingStage.COMPLETE, 4, 4)
    finally:
        self.progress.stop()
```

---

### Phase 4: CLI Updates

**File: `scripts/index.py`**

Add `--quiet` flag for non-interactive/CI mode:

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--force", action="store_true")
parser.add_argument("--quiet", action="store_true", help="Disable rich progress display")
args = parser.parse_args()

pipeline = ResearchRAGPipeline(config_path, show_progress=not args.quiet)
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/progress.py` | **NEW** - Progress display and timing classes |
| `src/sources/zotero.py` | Add parallel extraction, dataclasses, callback |
| `src/sources/base.py` | Add optional `progress_callback` to base class |
| `src/pipeline.py` | Integrate ProgressDisplay, wire callbacks |
| `scripts/index.py` | Add `--quiet` flag |

---

## Design Principles Preserved

1. **Chunking unaffected**: Parallel extraction happens before chunking; Document objects are passed to chunker as before
2. **No shared mutable state**: Each extraction task operates on a different file
3. **Subprocess isolation**: PDF extraction uses subprocess with timeout - inherently thread-safe
4. **Resumability preserved**: Existing `IndexingProgress` JSON persistence unchanged
5. **Modular callbacks**: Progress reporting is optional and decoupled from extraction logic

---

## Configuration

The existing config is sufficient:

```yaml
zotero:
  max_extraction_threads: 4  # NOW ACTUALLY USED
```

Optional additions for progress display:
```yaml
progress:
  enabled: true
  refresh_rate: 2  # updates per second
```

---

## Testing Strategy

1. **Unit tests for ProgressDisplay**: Mock `rich.Live`, verify thread-safe updates
2. **Unit tests for parallel extraction**: Mock `extract_text`, verify ThreadPoolExecutor usage
3. **Integration test**: Small corpus (10-20 docs) with parallel extraction
4. **Verify resumability**: Interrupt and resume, check no duplicates

---

## Open Questions for Refinement

1. Should we add parallel processing for Obsidian notes too? (Currently fast enough, may not be needed)
2. What should the default thread count be? (Currently 4, could be CPU-count based)
3. Should we add a log file option for the detailed extraction output?
4. Do we want to show individual file progress bars for large PDFs?

---

## Session Notes

This plan was created during a session where:
- Overnight indexing completed with 585,234 chunks from 12,874 documents
- But hierarchical chunking (vNext) was not enabled in config - all chunks were "mid" level only
- Config was updated to add `router_enabled: true` and `id_strategy: stable_hash`
- Database was cleared for re-indexing
- User requested parallel extraction and progress display before running the re-index
