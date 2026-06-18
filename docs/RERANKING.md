# Re-ranking

Re-ranking sits after vector recall and before optional parent-context expansion:

```
recall (k_recall) → rerank → truncate (k_return/top_n) → expand parent
```

## How it works

- `k_recall` controls the number of candidates retrieved from the vector store.
- When enabled, the reranker reorders candidates by relevance to the query.
- The final result list is truncated to `k` or `rerank.top_n` (if set).

## Configuration

Configure in `config.example.yaml`:

```yaml
retrieval:
  k_recall: 50
  rerank:
    enabled: true
    type: cross_encoder
    top_n: 8
    max_candidates: 30
    max_chars_per_candidate: 1200
    cross_encoder:
      base_url: "http://localhost:8005/v1"
      model: "BAAI/bge-reranker-v2-m3"
      timeout_seconds: 60
  expand:
    include_parent: true
    max_parents: 1
```

## Production backend

v0.6 production uses a vLLM-served cross-encoder via `/v1/rerank`.
The old LM Studio / small-LLM JSON reranker path has been retired.
