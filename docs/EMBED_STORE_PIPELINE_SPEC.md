# Embedding + Storage Overlap (Producer/Consumer) Spec

Status: Draft
Owner: Colin
Date: 2026-01-08

## Goal
Overlap embedding (GPU/CPU) and Chroma storage (network/IO) so large batches
start writing to Chroma before all embeddings finish, improving throughput and
time-to-first-write.

## Scope
Implement a producer/consumer pipeline inside `src/pipeline.py` for chunk
embedding and storage. Preserve existing ordering/determinism and progress
tracking. Keep config-first approach; default behavior can remain serial unless
enabled.

## Design Principles
- Stage isolation: chunking -> embedding -> storage remains explicit, but
  embedding and storage overlap in time via a bounded queue.
- Determinism: chunk IDs and metadata remain stable; ordering of writes is not
  guaranteed but IDs are deterministic so upserts are safe.
- Fail fast: if embedding fails, stop producing; if storage fails, halt and
  surface error.
- Backpressure: bounded queue prevents unbounded memory growth.

## Proposed Configuration
Add optional config in `config.yaml`:

```yaml
indexing:
  batch_size: 50
  embed_store_pipeline:
    enabled: true
    queue_max_items: 8      # max number of sub-batches in-flight
    embed_sub_batch_size: 2000
    store_sub_batch_size: 2000
```

Notes:
- `embed_sub_batch_size` controls producer granularity.
- `store_sub_batch_size` can match or be smaller than embed size (optional).

## Pipeline Flow (High Level)
1. Chunk documents as today (same).
2. Split chunks into sub-batches (`embed_sub_batch_size`).
3. Producer embeds each sub-batch and pushes to queue:
   `(texts, embeddings, metadatas, ids)`.
4. Consumer pulls from queue and writes to Chroma in `store_sub_batch_size`
   slices.
5. Shutdown: producer enqueues sentinel; consumer drains and exits.

## Error Handling
- Producer error: store exception, signal stop, drain queue, raise error.
- Consumer error: store exception, signal stop, raise error.
- Use an `Event` for cancellation and a shared exception container.

## Progress Updates
- Existing progress stages stay (CHUNKING -> EMBEDDING -> STORING).
- Add lightweight counters:
  - `embedded_chunks` incremented per sub-batch
  - `stored_chunks` incremented per write
- If needed, emit progress via existing `IndexingProgress` stats (optional).

## Concurrency Model
- One producer thread for embedding.
- One consumer thread for storage.
- Main thread orchestrates and waits.

## Pseudocode Sketch
```
queue = Queue(maxsize=queue_max_items)
stop_event = Event()
error = None

def producer():
    for sub_batch in embed_batches(chunks):
        if stop_event.is_set(): break
        embeddings = embed(sub_batch.texts)
        queue.put((sub_batch, embeddings))
    queue.put(None)  # sentinel

def consumer():
    while True:
        item = queue.get()
        if item is None: break
        sub_batch, embeddings = item
        store(sub_batch, embeddings)
        queue.task_done()

start producer/consumer threads
join producer
queue.join()
join consumer
if error: raise
```

## Testing Plan
- Unit: simulate embed/store with mocks; verify interleaving, queue bounds,
  and error propagation.
- Integration: ensure stored count matches embedded count; no missing docs.
- Regression: resume logic unchanged; force reset still works.

## Rollout Plan
- Ship with feature flag off by default.
- Enable in config for large corpora.
- Monitor for:
  - Chroma timeouts
  - Queue backpressure (slow store)
  - Memory usage

## Open Questions
- Should we default `enabled: true`?
- Should storage be a pool (multiple consumers) or single consumer?
- Do we want to coalesce metadata conversion once per sub-batch?

