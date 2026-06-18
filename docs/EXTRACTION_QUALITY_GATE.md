# Extraction Quality Gate — Design and First Corpus Results

**Date**: 2026-06-15
**Branch**: `v0.6-rebuild`
**Host**: Sparky
**Status**: Proposal + empirical results. Refines the routing recommendation in
`EXTRACTION_BAKEOFF_AND_ROUTING.md` using the whole local corpus instead of n=2.

This note delivers the "next implementation step" named at the end of
`EXTRACTION_BAKEOFF_AND_ROUTING.md` — *turn the harness quality counters into a
small `ExtractionQualityProfile` and make routing decisions config-driven* — and
then runs it over the full local Zotero corpus so the v0.6 routing decision is
made on a distribution, not two documents.

## 1. Why a computable score

The W1 bake-off ranked extractors by `searchable_chars` plus binary artifact
flags, then a human eyeballed the Markdown. Two problems make that unfit as a
gate:

1. **The headline metric ranked the wrong winner.** On Hudson, `searchable_chars`
   ordered pdfminer (689k) > Docling (667k) > Marker (594k). The human (correctly)
   picked Marker. So the number and the decision disagreed; the real driver was
   un-measured whitespace/flow quality.
2. **It does not scale.** You cannot eyeball 8,200 sources.

`src/extraction_quality.py` replaces the eyeball with a transparent, computable
`QualityProfile`. The keystone new signal is a **dictionary real-word ratio**:
OCR scramble like `ECUTI SIONALS PROFES VES` produces tokens in no dictionary,
which char count and the old flags missed entirely.

## 2. The profile

`profile_text(text) -> QualityProfile` computes:

- **Signals** (properties of the text): `real_word_ratio` (Latin-script tokens
  found in a multilingual wordlist), `short_token_ratio`, `double_space_ratio`,
  `hyphen_break_rate`, `single_word_line_ratio`, `repeated_line_ratio`,
  `replacement_char_rate`, `letter_spaced_rate`, `latin_token_ratio`, char/line
  counts.
- **Penalties** in [0, 1] derived from those signals via linear ramps between
  documented good/bad anchors in `QualityThresholds` (one object to tune).
- **overall_score** in [0, 1] — a weighted blend, for ranking/reporting.
- **action** — the routing decision, driven by *buckets* rather than the blend:

| Bucket | Penalties | Meaning | Action when high |
|---|---|---|---|
| **fundamental** | garbage, fragmentation, encoding, letter_spaced | the extractor produced genuinely bad text | **escalate** to a heavier extractor |
| **recoverable** | spacing, line_damage, boilerplate, single_word_lines | cosmetic noise a deterministic pass fixes | **clean** in place, then re-score |

This split is the most important design decision and it came directly from the
data (§4): Docling/pdfminer "lose" to Marker almost entirely on **double-spacing**,
which whitespace normalization fixes for free — that is not a reason to spend 163s
on a heavier extractor.

### Script awareness

`real_word_ratio` is only meaningful for scripts we have a wordlist for, so it is
computed over **Latin-script tokens only**, and when a document is not
Latin-dominant (`latin_token_ratio < 0.5`) the garbage penalty is **skipped**.
Without this, clean Cyrillic/Greek/CJK text scores as garbage — see the Antonova
false positive in §4.

## 3. Validation against the known bake-off outputs

Re-scoring the text already on disk from `EXTRACTION_BAKEOFF_AND_ROUTING.md`:

| Case | overall_score | action | fundamental | recoverable | Reading |
|---|---:|---|---:|---:|---|
| Hudson / Zotero FT | 0.994 | accept | 0.01 | 0.00 | Free cache already clean enough |
| Hudson / Marker | 1.000 | accept | 0.00 | 0.00 | Cleanest, as the human judged |
| Hudson / pdfminer | 0.853 | **clean** | 0.00 | 0.39 | Only double-spacing — fixable |
| Hudson / Docling | 0.880 | **clean** | 0.00 | 0.32 | Only double-spacing — fixable |
| Hudson / PyMuPDF4LLM | 0.000 | escalate | 1.00 | 0.00 | Genuinely empty (images dropped) |
| Stelter / Marker | 1.000 | accept | 0.00 | 0.00 | Born-digital, clean |
| Stelter / PyMuPDF4LLM | 0.994 | accept | 0.00 | 0.02 | Born-digital, clean |

The score now agrees with the human ranking **and** explains it: Marker's edge
over Docling/pdfminer on Hudson is cosmetic (recoverable), not fundamental.

And it is recoverable *for free*. Running `deterministic_clean` (de-hyphenate
line breaks, expand ligatures, NFKC, collapse intra-line whitespace) and
re-scoring:

| Case | raw score / action | cleaned score / action |
|---|---|---|
| Hudson / Docling | 0.880 / clean | **1.000 / accept** |
| Hudson / pdfminer | 0.853 / clean | **0.996 / accept** |

So cheap pdfminer + a millisecond cleanup pass reaches Marker-grade quality
(0.996–1.000) on the scanned book. Marker's 163 s buys nothing here.

## 4. Whole-corpus result (the actual quality gate over many items)

`scripts/profile_corpus.py` profiles every PDF under the local Zotero storage via
the **zero-cost** `.zotero-ft-cache`, then runs cheap pdfminer only on the
FT-failing subset.

**Corpus: 535 local Zotero PDFs. FT-cache profiling pass: ~11 s total.**

| Action from free Zotero FT cache | Count | % |
|---|---:|---:|
| `accept` (use as-is) | 532 | 99.4% |
| `clean` (deterministic cleanup, then accept) | 2 | 0.4% |
| `escalate` (needs more) | 1 | 0.2% |

Score quartiles: min 0.726, p25 0.989, **median 0.998**, max 1.000.

The single `escalate` was a **missing** `.zotero-ft-cache`; cheap pdfminer
extracted it cleanly → `accept`. **Net: 0 of 535 sources required a heavy
extractor (Marker/Docling).**

### The false positive that proves the metric is honest

Before the script-aware fix, Antonova & Naumtseva 2022 scored 0.648 / escalate.
Inspection showed it is **~45% Cyrillic** (clean Russian text) — flagged only
because there was no Russian wordlist. After computing the dictionary ratio over
Latin tokens and skipping it when non-Latin dominates, it scores **0.948 /
accept**. This is now covered by a regression test, and it is the reason the
gate must never treat "not in my dictionary" as "garbage" without a script check.

### Localized garbage needs a per-chunk gate

Hudson scores 0.994 at the document level, but profiling **only its scrambled
cover region** scores 0.0 / escalate. A whole-document score hides a small bad
region. **Therefore the production gate must run per-chunk**, not per-document —
which is exactly the altitude the acceptance harness (`src/acceptance_harness.py`)
already operates at.

## 5. Escalation path validated on derived hard scans

The real corpus produced **zero** genuine escalations, leaving the `escalate`
branch and the OCR payoff untested. Since the library is clean by construction,
hard cases were **derived** from clean PDFs (`scripts/make_hard_fixtures.py`):
each page rasterized into an **image-only PDF with no text layer**, at full and
low DPI plus a rotated+noisy worst case, keeping the original text layer as
**page-matched ground truth**. `scripts/compare_ocr_recovery.py` runs the cheap
path (pdfminer, no OCR) and the OCR escalation (Marker + surya, GPU) and scores
both against ground truth.

| Fixture | DPI | cheap (no-OCR) | OCR action | OCR score | token recall | OCR time |
|---|---:|---|---|---:|---:|---:|
| Stelter (EN) image_only | 200 | escalate (10 ch) | accept | 1.000 | 0.909 | 134 s |
| Stelter (EN) low-dpi | 120 | escalate | accept | 1.000 | 0.908 | 129 s |
| Stelter (EN) rotated+noisy | 120 | escalate | accept | 1.000 | 0.909 | 117 s |
| Antonova (RU) image_only | 200 | escalate | accept | 0.997 | 0.852 | 200 s |
| Antonova (RU) low-dpi | 120 | escalate | accept | 0.985 | 0.850 | 175 s |
| Antonova (RU) rotated+noisy | 120 | escalate | accept | 1.000 | 0.850 | 185 s |

- **The gate escalates correctly 6/6.** A vanished text layer reliably trips
  `escalate`; the cheap extractors return ~nothing.
- **OCR escalation pays off 6/6.** Marker+surya recovered `accept`-grade text at
  0.85–0.91 token recall (the ~10–15% gap is rare words / math / page furniture,
  which BGE-M3 retrieval tolerates).
- **Robust to degradation.** 200→120 DPI plus rotation+noise barely moved recall
  (Stelter 0.909→0.909). The OCR path does not need pristine input.
- **Cyrillic recovers and is accepted.** Script-awareness is now validated
  end-to-end on real OCR output — clean non-Latin is not mis-escalated.
- **But it is expensive:** ~117–200 s for *10 pages* on GPU → a full scanned book
  is ~an hour. Escalation must stay the rare tail (corpus says ~0%); it is a
  correctness backstop, not a throughput path.

## 6. What this means for v0.6 routing

The bake-off (n=2) proposed: *Zotero FT → pdfminer → **Marker (primary quality
path)** → Docling (tie-breaker) → Marker+LLM*. The corpus distribution refines
that:

- **Default = Zotero FT cache + the quality profile.** It clears the gate for
  ~99% of real sources at ~0.02 s each.
- **`clean` = deterministic cleanup** (collapse whitespace, de-hyphenate line
  breaks, strip repeated header/footer lines), then re-score. This — not a
  heavier extractor — is what Docling/pdfminer needed on Hudson.
- **`escalate` = cheap pdfminer first** (handles missing/empty caches), and only
  then **Marker+OCR** for the rare source that still fails the **per-chunk** gate.
  §5 confirms this works: Marker+surya recovered image-only EN and RU scans to
  `accept` (0.85–0.91 recall), robust to low DPI and noise.
- **Marker/OCR is a rare, opt-in escalation — not the primary path.** On this
  corpus its share is ~0%, and it costs ~117–200 s per 10 pages (≈ an hour for a
  full scanned book). Build the seam so it can be slotted in per-source with a
  page/time budget; do not budget the rebuild around running it at scale.

**Stack decision (2026-06-15):** the v0.6 extractor stack is **pdfminer + Marker**
(plus the zero-cost Zotero FT cache and PyMuPDF for rasterization). **Docling and
pymupdf4llm were uninstalled** — Docling was only pdfminer-grade whitespace noise
and its OCR backend wasn't even installed; pymupdf4llm dropped image pages without
OCR. Both stay re-addable behind the extractor seam (`requirements.txt` notes why).

### Throughput implication (the open question from the review)

Extraction is **not** the rebuild bottleneck on this corpus. Extrapolating the
measured rates to ~8,200 production items:

- FT-cache read + profile: ~8,200 × ~0.02 s ≈ **under 3 minutes** for the whole
  triage.
- pdfminer fallback on, say, 1–2% missing/empty caches: ~80–160 × ~5 s ≈ **7–14
  minutes**.
- Heavy extraction: ~0% → negligible.

The "days → hours" goal is therefore an **embedding-throughput** problem (9.85M
chunks), not an extraction problem. v0.6 W1 effort should go to the gate +
cleanup + per-chunk acceptance, not to a heavy-extractor router.

## 7. Definition of the quality gate (corpus acceptance)

"An actual quality gate over a larger number of items" = three layers:

1. **Per-source extraction gate** (this module, run on the chosen extractor's
   output): `accept` / `clean` / `escalate`, with the route + score + signals
   recorded in the registry (the provenance fields proposed in the bake-off doc).
2. **Per-chunk acceptance gate** (`src/acceptance_harness.py`, now with the
   restored `letter_spaced_words` detector and a tunable `--artifact-tolerance`):
   verbatim-quote verification, registry↔collection exactness, dedup, and a
   chunk-level artifact-rate scan. Run with a non-zero tolerance on real data.
3. **Corpus aggregate gate**: the distribution itself must pass thresholds, e.g.
   "≥ 95% of sources `accept`-or-`clean`", "0 sources silently empty where
   fulltext was expected" (coverage), and "escalation set is small enough to
   review by hand". `scripts/profile_corpus.py` produces this distribution.

## 8. Caveats

- **Local sample.** 535 PDFs under `/home/colin/Dev/Sources/Zotero/storage`. The
  full production library (~8,200 items) may include more scanned books; re-run
  `profile_corpus.py` against the production storage before the rebuild to
  confirm the distribution holds. The method scales (it ran in seconds).
- **Born-digital dominates this corpus.** That is *why* the free cache is so
  good. A scan-heavy corpus would shift the `escalate` share up — the gate would
  surface that, which is the point.
- **Thresholds are calibrated, not proven.** Anchors in `QualityThresholds` were
  set from this corpus + the two bake-off stress docs. They are config in one
  place; tune as new failure classes appear.
- **Heuristic, not ground truth.** The gate measures *smells* of bad extraction,
  not retrieval accuracy. The end-to-end check is still the acceptance harness's
  quote probes against the rebuilt collection.

## 9. Next steps

1. Wire `profile_text` into `scripts/extraction_bakeoff.py` so the bake-off
   reports `overall_score`/`action` alongside the raw counters.
2. Add the per-chunk quality scan to `run_registry_harness` (it already scans
   artifacts; have it also emit the score distribution + escalate list).
3. ~~Implement the deterministic `clean` pass and re-score~~ — **done**:
   `deterministic_clean` in `src/extraction_quality.py` (de-hyphenate, ligatures,
   NFKC, whitespace collapse), verified to promote the Hudson recoverable cases
   to `accept`. Next: have the router actually invoke it on `clean` actions and
   persist the cleaned text.
4. Land the routing config (`extraction.router.*`) with these thresholds as the
   documented defaults, per the config-first principle.
5. Wire the OCR escalation (Marker+surya, §5) into the router behind the seam,
   with a **per-source page/time budget** — it is ~117–200 s / 10 pages, so it
   must be opt-in for the rare `escalate` tail, never a default path.
6. Re-run `profile_corpus.py --source-root <production storage>` before cutover,
   and keep a few `make_hard_fixtures` cases in CI as an escalation regression.

## Reproduce

```bash
./.venv/bin/python -m pytest tests/unit/test_extraction_quality.py -q

# Whole-corpus distribution (cheap):
./.venv/bin/python scripts/profile_corpus.py --run-pdfminer-on-escalate
# -> output/corpus-quality-profile.json

# Derive hard scans from clean PDFs + measure the OCR escalate path:
./.venv/bin/python scripts/make_hard_fixtures.py "<clean.pdf>" --max-pages 10
./.venv/bin/python scripts/compare_ocr_recovery.py
# -> output/hard-fixtures/manifest.json, output/ocr-recovery.json
```
