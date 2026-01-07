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
    type: llm
    top_n: 8
    llm:
      provider: lmstudio
      model: null
      max_tokens: 256
      temperature: 0.0
  expand:
    include_parent: true
    max_parents: 1
```

## Switching providers

The reranker currently supports LM Studio via its OpenAI-compatible chat endpoint. Additional providers can be added behind the reranker factory without touching the pipeline.
