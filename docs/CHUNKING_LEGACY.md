# Legacy Chunking Plan

## Design statement

Obsidian notes are treated as Markdown with an additional semantic layer (frontmatter, tags, links). All such semantics must be parsed, preserved, and surfaced as metadata. Generic Markdown parsing is insufficient.

## Router rules

The chunking router selects a chunker based on content and metadata:

1. **Zotero annotations** (`source_type == "zotero_annotation"`) use the atomic chunker.
2. **Obsidian notes** or Markdown-looking content use the Markdown chunker.
3. **Huge documents** (token estimate above `chunking.huge_docs.huge_doc_tokens`) use the hierarchical chunker.
4. **Fallback** uses the default `TextChunker`.

## Chunk levels

Chunks include a `chunk_level` metadata field:

- `atomic`
- `coarse`
- `mid`
- `fine`

Hierarchical chunks include `parent_id` so fine chunks can resolve to mid, and mid can resolve to coarse when enabled.

### Parent ID scoping

Parent IDs are **document-scoped** to prevent cross-document collisions when processing batches. The lookup key is `(source_id, level, ordinal)` rather than just `(level, ordinal)`.

Key implementation details:
- `source_id` is added to chunk metadata during processing (`pipeline.py`)
- `attach_parent_ids()` in `id_utils.py` uses source_id for lookups
- This ensures a fine chunk from document A never references a mid chunk from document B

## Markdown semantics

Markdown chunking respects the author's structure:

- YAML frontmatter is parsed and preserved in metadata.
- Headings (`#`, `##`, `###`) define section boundaries.
- Code blocks are preserved and never split across chunk boundaries.
- Tags and wikilinks are extracted and stored in metadata (`tags`, `links_out`).
- `heading_path` captures section context (e.g., `H1 > H2`).

## Configuration knobs

See `config.example.yaml` for the full configuration:

- `chunking.router_enabled`
- `chunking.id_strategy`
- `chunking.defaults`
- `chunking.markdown`
- `chunking.huge_docs`

---

## Verification Test Plan

Use this plan to verify the hierarchical chunking strategy after making changes.

### Prerequisites

1. ChromaDB running on `localhost:8000`
2. LM Studio running on `localhost:1234` with BGE-M3 model loaded
3. Zotero with real PDFs configured in test config

### Test Config

The test config at `tests/fixtures/configs/config.pipeline.yaml` should have:

```yaml
chunking:
  router_enabled: true
  id_strategy: stable_hash
  debug_router: true  # Enable router debug output

  huge_docs:
    enabled: true
    huge_doc_tokens: 4000  # LOWERED for testing (production uses 25000)
```

### Running the Test

```bash
# Clear test collection (if needed)
python -c "
import chromadb
client = chromadb.HttpClient(host='localhost', port=8000)
try:
    client.delete_collection('test_pipeline_attachments')
    print('Deleted collection')
except Exception as e:
    print('Collection not found (OK)')
"

# Run test with real documents
python tests/pipeline/test_pipeline_with_attachments.py 100 100
```

### Verification Checks

Run these checks after the test completes:

#### 6.1 Chunk Level Distribution

```python
from collections import Counter
import chromadb

client = chromadb.HttpClient(host='localhost', port=8000)
collection = client.get_collection('test_pipeline_attachments')

results = collection.get(include=['metadatas'], limit=10000)
levels = Counter(m.get('chunk_level', 'unknown') for m in results['metadatas'])
print('Chunk levels:', dict(levels))

# Assertions
assert levels.get('fine', 0) > 0, "No fine chunks - hierarchical chunking may not have triggered"
assert levels.get('coarse', 0) > 0, "No coarse chunks - hierarchical chunking may not have triggered"
```

#### 6.2 Parent ID Integrity

```python
fine_chunks = [
    (meta, cid)
    for cid, meta in zip(results["ids"], results["metadatas"])
    if meta.get("chunk_level") == "fine" and meta.get("parent_id")
]

print(f"Fine chunks with parent_id: {len(fine_chunks)}")

for meta, chunk_id in fine_chunks[:5]:
    parent_id = meta['parent_id']
    parent = collection.get(ids=[parent_id], include=['metadatas'])

    assert parent['ids'], f"Parent {parent_id} not found"
    parent_meta = parent['metadatas'][0]
    assert parent_meta.get('chunk_level') == 'mid', "Parent should be mid level"
    assert parent_meta.get('source_id') == meta.get('source_id'), "Source ID mismatch!"
    print(f"OK: {chunk_id[:35]} -> {parent_id[:35]} (same source)")
```

#### 6.3 Obsidian Metadata

```python
obsidian_chunks = [m for m in results['metadatas'] if m.get('source_type') == 'obsidian']
print(f"Obsidian chunks: {len(obsidian_chunks)}")

has_heading = sum(1 for m in obsidian_chunks if m.get('heading_path'))
print(f"  with heading_path: {has_heading}")
```

#### 6.4 Stable ID Format

```python
sample_ids = results['ids'][:10]
for cid in sample_ids:
    assert '-chunk-' not in cid, f"Legacy ID found: {cid}"
print("All IDs use stable format")
```

#### 6.5 No Ballooning (Rerun Test)

```python
count_before = collection.count()
# Rerun the test with same args
# ...
count_after = collection.count()
assert count_after == count_before, "Collection count changed - stable IDs may be failing"
```

### Success Criteria

| Check | Required | Expected |
|-------|----------|----------|
| Test completes without errors | Yes | No exceptions |
| Router debug shows HierarchicalChunker | Yes | At least 1 doc |
| chunk_level present on all chunks | Yes | mid, coarse, fine, atomic |
| Parent IDs are document-scoped | Yes | Same source_id for child->parent |
| Parent integrity check passes | Yes | All parent fetches succeed |
| Obsidian has heading_path | Yes | Most obsidian chunks |
| No legacy IDs (-chunk-) | Yes | All stable format |
| Rerun doesn't balloon count | Yes | Upsert works (stable IDs) |

---

## Troubleshooting

### Router not selecting HierarchicalChunker

- Check `huge_doc_tokens` in config - may be too high
- Verify document token count exceeds threshold
- Enable `debug_router: true` to see router decisions

### Parent ID lookup failures

- Ensure `source_id` is being set in pipeline.py
- Check that `attach_parent_ids()` is called after all chunks are created
- Verify chunks have `chunk_level` and `chunk_index` metadata

### Legacy IDs appearing

- Check `id_strategy: stable_hash` is set in config
- Verify `stable_chunk_id()` is being used, not f-string formatting
- Look for any code using the old `{doc_id}-chunk-{idx}` pattern
