# Stage 1 Implementation Checklist — Query Reliability + Robust Reranking

Status: **SPEC ONLY**. Do not implement until approved.

This checklist turns `docs/QUERY_PIPELINE_PLAN.md` (Stage 1) into concrete tasks.

---

## 0) Behavioural contract
- Query must **not crash** if reranking fails.
- Reranking is **config-driven** (model specified in config).
- Rerank payload must be **bounded**.

---

## 1) Configuration schema additions

### Add to `config.example.yaml`
```yaml
retrieval:
  k_recall: 50
  rerank:
    enabled: true
    top_n: null
    max_candidates: 30              # cap for reranker input
    max_chars_per_candidate: 1200   # cap candidate snippet size
    llm:
      provider: lmstudio
      model: ibm/granite-4-micro
      max_tokens: 512
      temperature: 0.0
      timeout_seconds: 120
```

Notes:
- `top_n` remains as existing behaviour (limit post-rerank).
- `max_candidates` and `max_chars_per_candidate` only affect reranker *input*, not recall.

---

## 2) Code changes (planned touchpoints)

### A) Safe rerank fallback
**File:** `src/pipeline.py`
- In `query()` wrap `self.reranker.rerank(...)` in `try/except Exception as e`.
- On exception:
  - print a warning (or use a logger if introduced later)
  - continue with the original results (unreranked)

Acceptance:
- `scripts/query.py ...` returns results even if reranker throws.

### B) Bound rerank payload
**File:** `src/retrieval/rerank.py`
- Read `max_candidates` and `max_chars_per_candidate` from config.
- Use only first `max_candidates` from the recall list.
- For each candidate, truncate `text` to `max_chars_per_candidate`.

Acceptance:
- Rerank request size remains stable even with large `k_recall` and large chunk sizes.

### C) JSON robustness / salvage
**File:** `src/retrieval/rerank.py`
- Add helper `_parse_scores_json(response_text: str) -> dict`:
  1) attempt `json.loads` directly
  2) if fail: extract substring between first `{` and last `}` and retry
  3) if still fail: raise a custom exception or return None
- In `rerank()`, on parse failure, raise a controlled exception so pipeline can fallback.

Acceptance:
- Truncated wrappers around JSON (common in some LMs) don’t immediately break.

### D) Reranker model required when enabled
**File:** `src/retrieval/rerank.py`
- If `enabled: true` and provider=lmstudio and no `llm.model`, raise a clear error.

Acceptance:
- Avoid silent misconfiguration.

### E) CLI override to disable rerank
**File:** `scripts/query.py`
- Add `--no-rerank` flag.
- Implementation approach:
  - load config
  - if `--no-rerank`, set `config['retrieval']['rerank']['enabled']=False` before building pipeline

Acceptance:
- Debugging and quick recall works even when reranking is flaky.

---

## 3) Tests (lightweight)

### Add a minimal unit test suite (optional but recommended)
If the repo has no tests for retrieval:
- Add `tests/test_rerank_json.py`:
  - valid JSON parses
  - JSON embedded in extra text parses
  - truncated JSON fails in a controlled way

### Add a “smoke” script (manual)
- A short doc section describing how to run:
  - with rerank enabled
  - with `--no-rerank`

---

## 4) Validation runbook (manual)

1) Enable rerank and use a small chat model.
2) Confirm:
   - results return
   - rerank scores appear in metadata (optional)
3) Force a rerank failure (bad model name) and confirm:
   - warning printed
   - unreranked results returned

---

## 5) Out-of-scope for Stage 1
- Diversity/dedupe (Stage 2)
- Extraction noise cleanup (Stage 3)
- UI changes / streamlit changes
