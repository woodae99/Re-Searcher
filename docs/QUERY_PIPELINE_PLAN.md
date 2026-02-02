# Query Pipeline Improvement Plan (Stage 1–3)

Status: **SPEC / DESIGN ONLY** (no behavioural changes unless explicitly approved)

This repo already implements a strong retrieval architecture (layered chunking, rich metadata, Chroma, LM Studio embeddings). The observed pain points are **reliability** (reranker failures) and **result quality presentation** (duplicates, low-signal snippets), plus a known backlog of **index noise** (mojibake/PDF extraction garbage).

This document is the source of truth for planned changes.

---

## Context / Desired Behaviour

We want the system to support “power queries” where the user might:

1) start with an *abstract/conceptual intent* (e.g. Deleuze’s “intensities”) that requires broad semantic recall,
2) narrow down via mid-level chunks to confirm conceptual alignment,
3) land on fine chunks for quotable evidence.

So “quality” is not just top‑k similarity; it’s:
- **coverage** across likely relevant sources,
- **confirmation** via medium granularity,
- **evidence** via fine granularity,
- and a workflow that doesn’t break when reranking fails.

---

## Stage 1 (Now): Query Reliability + Robust Reranking

### Goals
- Query should **never hard-fail** because the reranker model is unavailable, returns invalid JSON, or times out.
- Reranker model selection should be **config-driven** (explicit).
- Rerank payload should be bounded so it doesn’t exceed model context and cause truncation.

### Observed failures (from live test)
- LM Studio 500 Internal Server Error when rerank chat model isn’t available/loaded or endpoint errors.
- Truncated/invalid JSON returned by small chat model → `json.loads` failure.

### Proposed changes (spec)

#### 1. Add “safe rerank” fallback
**Where:** `src/pipeline.py` (query path) + `src/retrieval/rerank.py`

**Design:**
- Wrap rerank in `try/except`.
- If rerank fails:
  - log a warning (include exception message),
  - return the un‑reranked top‑k (or top_n) rather than raising.

**Rationale:** Reliability > perfect ordering.

#### 2. Constrain rerank input payload
**Where:** `src/retrieval/rerank.py`

**Design:**
- Introduce config options:
  - `retrieval.rerank.max_chars_per_candidate` (default e.g. 1200)
  - `retrieval.rerank.max_candidates` (default min(k_recall, 30))
- Send only a **snippet** (first N chars) rather than full chunk text.

**Rationale:** Prevent truncation, speed up rerank, reduce costs.

#### 3. Improve JSON robustness
**Where:** `src/retrieval/rerank.py`

**Design:**
- Add a “best-effort JSON extraction” step:
  - attempt `json.loads` directly
  - if it fails, try to extract substring between first `{` and last `}` and parse again
  - if still fails → fallback to no rerank

**Rationale:** Small models and some endpoints often wrap/garble JSON.

#### 4. Explicit reranker model config + defaults
**Where:** docs + config example

**Design:**
- Ensure config contains:

```yaml
retrieval:
  k_recall: 50
  rerank:
    enabled: true
    llm:
      provider: lmstudio
      model: ibm/granite-4-micro
      max_tokens: 512
      temperature: 0.0
```

- If `enabled: true` but no model is specified, fail early with a helpful error (or choose a safe default).

**Rationale:** Avoid ambiguous behaviour.

#### 5. Add a `--no-rerank` CLI flag
**Where:** `scripts/query.py`

**Design:**
- Allow disabling rerank per query run without editing config.

**Rationale:** Useful for debugging and when you want raw recall.

---

## Stage 2 (Next): Diversity + De-duplication in Results

### Goal
Avoid returning multiple near-identical chunks (often from the same source) in the final top‑k.

### Proposed changes (spec)

#### 1. Post-retrieval grouping
**Where:** `src/pipeline.py` after recall (and after rerank if enabled)

**Design options:**
- `diversity.max_per_source_id` (default 2)
- group by `metadata.source_id` (preferred) or `zotero_key`

**Rationale:** Improves human usefulness of results.

#### 2. Optional MMR selection
**Where:** new helper in `src/retrieval/`

**Design:**
- Implement Max Marginal Relevance using embeddings already available (or use distance proxy).

**Rationale:** Better coverage across concepts/sources.

---

## Stage 3 (Later): Index Noise Reduction (Extraction + Filtering)

Stage 3 is acknowledged as important but deferred for discussion because it can change corpus characteristics.

### Known problems
- mojibake
- PDF extraction artifacts (headers/footers/page numbers)
- extremely short/meaningless chunks

### Candidate interventions (spec only)
- min-length chunk filter (characters/tokens)
- unicode cleanup + mojibake heuristics
- header/footer pattern detection
- near-duplicate removal within-document using hashing
- structured PDF extraction improvements

---

## Acceptance Criteria (Stage 1)
- Running `python scripts/query.py ...` should not crash if rerank fails.
- When rerank fails, results are still returned.
- Rerank payload size is bounded and configurable.
- Rerank model is explicitly configurable.

---

## Notes
- The existing layered chunking strategy is aligned with advanced research workflows; Stage 1 changes aim to make the reranker reliable and controllable, not to remove layering.
