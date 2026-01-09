# Objective

Add a **QualityFilterGuard** that drops obviously low-information / extraction-garbage chunks **after chunking, before embedding**, with logging and offender reporting. Must be config-driven and must not break the router/chunker architecture.

## Placement

Insert the guard in the pipeline at the point where we have the final list of `(chunk_text, chunk_metadata)` tuples **after router/chunking and stable ID creation** but **before embedding calls**.

Order in the pipeline should be:

1. chunking/router → produces `(text, metadata)`
2. **oversize guard** (existing) → may split
3. **quality filter guard** (new) → may drop
4. embedding
5. store to Chroma

(If oversize guard is currently implemented as part of chunking, then put quality guard immediately before embedding anyway.)

## Config (add to YAML)

Add a section:

```yaml
chunking:
  quality_filter:
    enabled: true

    # core heuristics
    min_alnum_chars: 40
    min_alnum_ratio: 0.20
    max_whitespace_ratio: 0.85
    min_token_est: 20

    # regexes applied to stripped text (optional but useful)
    drop_if_matches:
      - "^[0-9]{1,3}$"
      - "^\\*{3,}$"
      - "^(SUMMARY|ABSTRACT|CONTENTS)$"

    # don’t drop if any of these metadata keys exist (glue/context)
    keep_if_metadata_present:
      - "heading_path"
      - "zotero_key"

    # optional: never filter obsidian (start conservative)
    skip_source_types:
      - "obsidian"

    # reporting
    report:
      enabled: true
      top_n: 50
      output_path: "runs/{RUN_ID}/quality_report.json"
```

Notes:

* Keep defaults conservative (we can tighten later).
* Use `{RUN_ID}` if you already have it; otherwise write to `runs/latest/quality_report.json`.

## Implementation details

### 1) New file: `src/processing/quality_filter.py`

Implement:

* `def is_low_info(text: str, cfg: dict) -> tuple[bool, list[str]]`

  * compute:

    * total_chars
    * whitespace_chars
    * alnum_chars
    * alnum_ratio
    * whitespace_ratio
    * token_est = len(text)//4 (heuristic)
  * apply thresholds + regexes
  * return `(drop, reasons)` where reasons includes strings like:

    * `"min_alnum_chars"`
    * `"min_alnum_ratio"`
    * `"max_whitespace_ratio"`
    * `"min_token_est"`
    * `"regex:<pattern>"`

* `class QualityFilterGuard:`

  * `process(chunks: list[tuple[str, dict]]) -> list[tuple[str, dict]]`
  * respects:

    * `enabled`
    * `keep_if_metadata_present`
    * `skip_source_types`
  * when dropping, increment counters in an internal aggregator keyed by:

    * `source_id`
    * optionally `source_type`
    * optionally filename / zotero_key if present
  * add to dropped record stats: reasons counts

### 2) Reporting

If `report.enabled`:

* write JSON at end of each batch (or pipeline end) containing:

  * run_id
  * totals dropped/kept
  * top offenders by `source_id` with:

    * dropped_count
    * kept_count (if known)
    * reason breakdown
    * 2 short previews (first ~120 chars) of dropped examples

This is the “curation hit list” for later repair.

### 3) Logging (important for live runs)

When a chunk is dropped, log at DEBUG (not INFO to avoid spam):

* source_id
* chunk_level
* token_est
* reasons
* preview (first 80–120 chars, single-line)

Also log per-batch summary at INFO:

* kept_count
* dropped_count
* top 5 offenders + dropped counts

### 4) Safety constraints

* Do **not** drop chunks if metadata contains any key in `keep_if_metadata_present`.
* Initially skip source_type “obsidian” entirely (so we don’t accidentally throw away short notes).
* Preserve all other metadata unmodified on kept chunks.

## Tests (minimal)

Add unit tests:

* newline storm chunk gets dropped (high whitespace ratio)
* `"***"` gets dropped by regex
* chunk with `heading_path` is kept even if it would be dropped
* obsidian source_type is skipped (kept)

## Acceptance criteria

* After implementing, on a small run sample:

  * dropped_count > 0 for known offenders
  * embedder no longer receives pages of `\n\n\n\n...` junk
  * no change to stable IDs / parent_id logic
  * indexing still completes and stores kept chunks normally

