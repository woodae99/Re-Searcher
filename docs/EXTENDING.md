# Extending Re-Searcher

## Add a new chunker

1. Create a new chunker in `src/processing/chunkers/` implementing `chunk_with_metadata`.
2. Add routing logic in `src/processing/router.py`.
3. Update tests in `tests/unit/` for routing and metadata expectations.

## Add a new reranker

1. Implement a class in `src/retrieval/rerank.py`.
2. Update `src/factories/reranker_factory.py` to select it by config.
3. Add unit tests to validate ordering logic.

## Add a new embedding provider

1. Implement `EmbeddingProvider` in `src/embedding/`.
2. Add a factory entry in `src/factories/embedding_factory.py`.
3. Add a config section in `config.example.yaml`.

## Switching embedding providers

Update `embedding.provider` and the provider-specific section in `config.example.yaml`:

```yaml
embedding:
  provider: openai
  openai:
    api_key: "${OPENAI_API_KEY}"
    model: "text-embedding-3-large"
```

## Provider isolation

All provider-specific code should live behind factories so the pipeline stays stable.
