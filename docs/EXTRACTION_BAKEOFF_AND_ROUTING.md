# Extraction Bake-off and Routing Strategy

**Date**: 2026-06-15
**Branch**: `v0.6-rebuild`
**Host**: Sparky
**Status**: Current v0.6 extraction decision record

This note records the PDF extraction tests run during the v0.6 setup pass and
the resulting candidate routing strategy. It supersedes the earlier working
hypothesis that Docling should be the single default PDF extractor.

## Candidates Tested

| Candidate | Role tested | Local/cloud | Notes |
|---|---|---|---|
| Zotero `.zotero-ft-cache` | Zero-cost source text | Local | Native Zotero full-text cache, already produced by Zotero indexing. |
| `pdfminer.six` via `src/extract_text.py` | Existing fast extractor | Local | Current Re-Searcher PDF text path. |
| Docling `2.102.2` | Heavy structure-aware extractor | Local | Tested with CUDA visible outside the Codex sandbox. |
| PyMuPDF4LLM `1.27.2.3` | Lightweight Markdown extractor | Local | Fast on born-digital article; failed on Hudson scan without OCR. |
| Marker `1.10.2` | Heavy document-intelligence extractor | Local | Best local heavy-output shape on Hudson so far. |

The reusable harness lives at:

```bash
./.venv/bin/python scripts/extraction_bakeoff.py --help
```

The earlier Docling-only harness remains at `scripts/docling_bakeoff.py`, but
new comparisons should use `scripts/extraction_bakeoff.py`.

## Environment Notes

- GPU-sensitive runs must execute outside the Codex sandbox; the sandbox hides
  `nvidia-smi` and CUDA from PyTorch.
- Docling should be run with `--image-export-mode placeholder` for extraction
  comparisons. The default embedded mode writes base64 images into Markdown and
  inflates character counts.
- Marker first use can be slow because model artifacts and runtime state are
  initialized. Marker also pulled dependency versions that differ from the
  Docling-only environment; keep package versions pinned and smoke-tested.
- Focused sanity tests after the added packages:

```bash
./.venv/bin/python -m pytest tests/unit/test_acceptance_harness.py tests/test_extract_text.py -q
```

Result: `9 passed`.

## Test Documents

### Small Born-digital Article

Path:

```text
/home/colin/Dev/Sources/Zotero/storage/B563JSDN/Stelter - 2009 - Coaching as a reflective space in a society of growing diversity - towards a narrative, postmodern p.pdf
```

Command:

```bash
./.venv/bin/python scripts/extraction_bakeoff.py \
  "/home/colin/Dev/Sources/Zotero/storage/B563JSDN/Stelter - 2009 - Coaching as a reflective space in a society of growing diversity - towards a narrative, postmodern p.pdf" \
  --extractors pymupdf4llm marker \
  --timeout 300 \
  --keep-output output/extraction-bakeoff/smoke \
  --output-json output/extraction-bakeoff/smoke-pymupdf-marker.json
```

Results:

| Extractor | Time | Searchable chars | Artifact flags | Initial read |
|---|---:|---:|---|---|
| PyMuPDF4LLM | `1.54s` | `41,677` | line hyphenation | Fast, usable Markdown, includes image omission markers. |
| Marker | `86.07s` | `39,866` | none detected | Cleaner flow, much slower. |

## Hudson Stress Test

Zotero citekey:

```text
hudsonHandbookCoachingComprehensive1999
```

Attachment path:

```text
/home/colin/Dev/Sources/Zotero/storage/4DD5DLE8/Hudson - 1999 - The Handbook of Coaching A Comprehensive Resource Guide.pdf
```

Document profile:

| Property | Value |
|---|---|
| Size | `14.106 MB` |
| Pages | `296` |
| Source | Internet Archive PDF stored by Zotero |
| Risk | Scanned/OCR-derived book; likely noisy text layer |

### Hudson Results

Primary outputs:

```text
output/extraction-bakeoff/hudson/
output/extraction-bakeoff/hudson-pymupdf-marker.json
output/extraction-bakeoff/hudson-zotero-pdfminer-docling-placeholder.json
```

| Extractor | Time | Searchable chars | Nonempty lines | Artifact flags | Result |
|---|---:|---:|---:|---|---|
| Zotero FT cache | `0.001s` | `596,939` | `3,705` | letter-spaced words | Fastest, good default candidate when cache passes quality gates. |
| `pdfminer` | `5.337s` | `689,653` | `10,728` | line hyphenation | Fast, broad coverage, rough line and paragraph flow. |
| PyMuPDF4LLM | `91.12s` | `2,305` | `229` | none detected | Failed this scanned/OCR-derived PDF without OCR. |
| Marker | `163.154s` | `594,269` | `3,519` | line hyphenation | Best local heavy-output shape on Hudson. |
| Docling placeholder | `182.375s` | `666,934` | `4,008` | line hyphenation | Good structure, no base64 payload, but more OCR spacing noise than Marker. |

Older Docling embedded-image run:

| Extractor | Time | Raw chars | Searchable chars after stripping image lines | Image lines |
|---|---:|---:|---:|---:|
| Docling embedded images | `265.685s` | `3,089,523` | about `666k` | `74` |

Conclusion: embedded images must be disabled or filtered before any Docling
output is considered for chunking.

### Qualitative Observations

- Zotero FT cache is close to the Internet Archive OCR layer and costs nothing.
  It removes some spacing noise but can contain severe letter-spaced runs, for
  example one character per word position across a paragraph.
- `pdfminer` is very fast and extracts plenty of text, but preserves many
  line-break artifacts and fragmented page layout.
- Marker produced the cleanest local Markdown-style prose flow on Hudson. It
  also converted contents-like material into Markdown tables, which may help or
  hurt depending on downstream chunking.
- Docling with placeholder image mode produced useful Markdown structure and no
  base64 image payload, but preserved more spacing weirdness than Marker.
- PyMuPDF4LLM should not be treated as a general replacement. It may still be
  valuable for born-digital PDFs, but with OCR disabled it was unusable on the
  Hudson scan.

## Candidate Routing Strategy

The v0.6 extraction path should be score-based, not a single hard-coded
extractor choice. The router should choose the cheapest candidate that passes
quality gates, escalating only when needed.

### Stage 1: Cheap Triage

1. Check for Zotero `.zotero-ft-cache`.
2. Compute a quality profile.
3. If it passes, index it.
4. If it is borderline, run deterministic cleanup and re-score.
5. If it fails or is missing, run `pdfminer` and compare.

Suggested cheap quality signals:

| Signal | Examples |
|---|---|
| `text_present` | chars per page, nonempty page ratio, empty/near-empty output |
| `ocr_noise` | replacement chars, weird character ratio, long consonant-heavy tokens |
| `spacing_noise` | letter-spaced words, excessive double spaces, single-letter lines |
| `line_damage` | hyphenated line breaks, too many very short lines, single-word lines |
| `structure` | heading density, paragraph length distribution, table/list preservation |
| `duplication` | repeated headers/footers, repeated page boilerplate |
| `coverage` | char count relative to Zotero FT, `pdfminer`, and/or Marker peers |

### Stage 2: Quality Escalation

Run Marker without LLM when the cheap path fails quality gates, especially for:

- scanned books;
- noisy OCR;
- contents-heavy or heading-heavy sources;
- multi-column or layout-sensitive PDFs;
- sources selected as mission-critical evidence.

Use Docling as a second quality path or tie-breaker when Marker fails, times out,
or performs worse on a fixture class.

Use PyMuPDF4LLM only for classes where the harness shows it earns its keep
(likely born-digital PDFs). It should not be a scanned-book fallback without a
separate OCR test.

### Stage 3: Hybrid/LLM Escalation

Marker has a `--use_llm` hybrid mode. It may help with:

- table repair and table merging across pages;
- form-like pages;
- inline math;
- image/page-region interpretation;
- custom correction prompts for known document classes.

It should not be a default route because it will be slower and requires a
vision-capable LLM backend. LM Studio can serve vision models, so this is a
realistic local option on Sparky, but it needs a separate page-range bake-off
before full-document use.

Suggested first test:

```bash
lms get google/gemma-4-e4b
```

Then benchmark Marker hybrid on selected Hudson page ranges rather than the
whole book. Compare normal Marker vs Marker hybrid on:

- title/frontmatter pages;
- contents pages;
- a prose chapter section;
- a table/form-like fixture from another PDF.

## Router Output and Provenance

Every source should record its route decision in the registry:

| Field | Purpose |
|---|---|
| `extractor` | Winning extractor name, e.g. `zotero-ft-cache`, `pdfminer`, `marker`. |
| `extractor_version` | Package/tool version where available. |
| `extract_quality` | Structured score or grade used by the router. |
| `extract_route` | Short reason, e.g. `zotero_ft_passed`, `marker_after_zotero_noise`. |
| `extract_artifacts` | JSON-ish artifact counters for audit/tuning. |
| `extract_elapsed_seconds` | Runtime for throughput projection. |
| `extract_fallbacks` | Ordered list of candidates attempted and why they failed or lost. |

This makes extraction auditable and tunable. Bad cases become threshold updates
or per-source re-extractions, not a pipeline rewrite.

## Current Recommendation

> **Update (2026-06-15, later that day).** A whole-corpus quality-gate run
> (535 PDFs) and a derived-hard-scan OCR test superseded the n=2 picture below.
> See `docs/EXTRACTION_QUALITY_GATE.md` for the controlling decision. In short:
> the free Zotero FT cache clears ~99% of the corpus, so **Docling and
> pymupdf4llm were dropped** (Docling was only pdfminer-grade whitespace noise;
> pymupdf4llm failed scans without OCR). The stack is now **pdfminer + Marker**.

Default route for v0.6 development:

```text
Zotero FT cache  (quality gate: accept ~99%)
  -> deterministic cleanup + re-score   (the 'clean' action; fixes whitespace/hyphenation)
  -> pdfminer                           (cheap fallback for missing/empty caches)
  -> Marker + OCR                       (rare 'escalate' backstop; ~1 hr/book, opt-in & budgeted)
```

Docling and pymupdf4llm remain re-addable behind the extractor seam if a future
fixture class demands them, but are not installed by default.
