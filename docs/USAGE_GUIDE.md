# Usage Guide: Resumable Batch-Concurrent Indexing

## Quick Start

### 1. Standard Indexing (With Automatic Resume)

```bash
# First run
python src/main.py

# If interrupted (Ctrl+C), just run again
# It will automatically resume from the last checkpoint
python src/main.py

# Progress is tracked in: output/indexing_progress.json
```

### 2. Configuration

Add optional batch size to `config.yaml`:

```yaml
# ... existing config ...

# Indexing configuration (optional, defaults shown)
indexing:
  batch_size: 50 # Documents to process per batch
```

**Batch size tuning**:

- **Smaller (10-25)**: Better error recovery, slower overall
- **Default (50)**: Good balance
- **Larger (100+)**: Faster, higher memory usage, longer recovery on failure

### 3. Monitoring Progress

While indexing is running, check progress:

```bash
# View current progress (Linux/Mac)
cat output/indexing_progress.json | jq .

# View current progress (Windows PowerShell)
Get-Content output/indexing_progress.json | ConvertFrom-Json | ConvertTo-Json

# Watch progress in real-time (Linux/Mac)
watch -n 5 "cat output/indexing_progress.json | jq '.stats'"

# Watch progress in real-time (Windows PowerShell)
while ($true) {
  Clear-Host
  Get-Content output/indexing_progress.json | ConvertFrom-Json |
    Select-Object -ExpandProperty stats
  Start-Sleep -Seconds 5
}
```

### 4. Resume After Failure

If indexing is interrupted (crash, power loss, etc.):

```bash
# Just run indexing again - it automatically resumes
python src/main.py

# Console output will show:
# "Processing 950/1000 pending documents..."
# "Batch 1: Processing 48/50 pending documents..."
# etc.
```

### 5. Force Fresh Indexing

If you want to rebuild the entire index from scratch:

```bash
# Method 1: Delete progress file
rm output/indexing_progress.json
python src/main.py

# Method 2: Use force flag (when implemented)
python src/main.py --force
```

## Progress File Format

The `output/indexing_progress.json` file looks like:

```json
{
  "started_at": "2025-01-05T14:23:00.123456Z",
  "updated_at": "2025-01-05T14:45:32.654321Z",
  "documents": {
    "zotero_item_12345": {
      "status": "stored",
      "chunk_count": 12,
      "updated_at": "2025-01-05T14:25:00.100000Z"
    },
    "obsidian_file_67890": {
      "status": "error",
      "error_msg": "Embedding generation failed: timeout",
      "updated_at": "2025-01-05T14:40:00.200000Z"
    },
    "zotero_item_13579": {
      "status": "embedded",
      "chunk_count": 8,
      "updated_at": "2025-01-05T14:42:00.300000Z"
    }
  },
  "stats": {
    "total_documents": 649094,
    "documents_chunked": 12500,
    "documents_embedded": 12000,
    "documents_stored": 11500,
    "total_chunks": 145000,
    "chunks_stored": 140000,
    "errors": 500
  }
}
```

**Field Explanations**:

- `started_at`: When this indexing session started
- `updated_at`: Last update time
- `documents`: Map of doc_id → status info
  - `status`: One of pending, chunked, embedded, stored, error
  - `chunk_count`: Number of chunks created from this document
  - `error_msg`: Why it failed (if status is error)
  - `updated_at`: When this document's status last changed
- `stats`: Aggregate statistics
  - `total_documents`: Total documents to index
  - `documents_chunked`: Count of successfully chunked
  - `documents_embedded`: Count of successfully embedded
  - `documents_stored`: Count successfully stored
  - `total_chunks`: Total chunks created
  - `chunks_stored`: Total chunks in ChromaDB
  - `errors`: Count of documents with errors

## Status Flow

Documents transition through these states:

```
PENDING (not in progress file yet)
  ↓ (chunking succeeds)
CHUNKED (text split into overlapping chunks)
  ↓ (embedding succeeds)
EMBEDDED (vectors generated, waiting to store)
  ↓ (storage succeeds)
STORED (in ChromaDB, searchable)

OR at any step:
  ↓ (error occurs)
ERROR (with error_msg for debugging)
```

## Common Scenarios

### Scenario 1: Power Loss During Indexing

```
Run: python src/main.py
Progress: 10,000 / 649,094 documents stored
Event: Power failure

Later:
Run: python src/main.py
Output:
  Fetched 649094 documents
  Processing documents in batches (size: 50)...
  Batch 200: Processing 50/50 pending documents...
```

**Result**: Resumes from document 10,001, no data loss

### Scenario 2: Out of Memory Error

```
Run: python src/main.py with batch_size: 100
Progress: 200,000 documents stored
Error: MemoryError in ChromaDB

Fix:
1. Reduce batch_size to 50
2. Update config.yaml
3. Run: python src/main.py
4. Resumes from document 200,001 with smaller batches
```

### Scenario 3: API Timeout

```
Run: python src/main.py
Progress: 450,000 documents stored, embedding batch fails
Error: timeout from LM Studio

Fix:
1. Restart LM Studio
2. Wait for it to be ready
3. Run: python src/main.py
4. Resumes automatically, retries failed batch
```

### Scenario 4: Testing New Configuration

```
1. Run indexing: python src/main.py
   Completes with 649,094 documents

2. Change config: chunk_size: 2048 → 4096
   (larger chunks = fewer chunks, faster embedding)

3. Delete progress: rm output/indexing_progress.json

4. Run again: python src/main.py
   Re-chunks with new size, re-embeds
   Takes ~10-15 minutes instead of 5 hours
   (skips 100% of extraction, fast re-chunking)
```

## Python API Usage

If using the pipeline directly in code:

```python
from src.pipeline import ResearchRAGPipeline
from pathlib import Path

# Initialize pipeline
pipeline = ResearchRAGPipeline(Path("config.yaml"))

# Run indexing (automatic resume)
pipeline.run()

# Or force fresh index
pipeline.run(force_reindex=True)

# Check progress programmatically
progress_stats = pipeline.progress.get_stats()
print(f"Stored: {progress_stats['documents_stored']} / {progress_stats['total_documents']}")

# Get documents by status
stored_docs = pipeline.progress.get_documents_by_status(DocumentStatus.STORED)
failed_docs = pipeline.progress.get_documents_by_status(DocumentStatus.ERROR)

# Query (works on current progress state)
results = pipeline.query("search term", k=5)
```

## Troubleshooting

### Issue: Index seems stuck or progressing slowly

**Solution**: Check progress file

```bash
# See current statistics
cat output/indexing_progress.json | jq '.stats'

# Expected: documents_stored should increase every few seconds
# If not changing: Check if LM Studio API is running
```

### Issue: Many documents in ERROR state

**Solution**: Check error messages

```bash
# See all errors
cat output/indexing_progress.json | \
  jq '.documents | map(select(.status == "error"))'

# Common errors:
# - "Embedding generation failed": LM Studio API issue
# - "ChromaDB connection error": ChromaDB not running
# - "File not found": Source file was deleted
```

### Issue: Progress file corrupted

**Solution**: Delete and restart

```bash
# Delete progress (careful!)
rm output/indexing_progress.json

# Restart indexing - will re-index from beginning
python src/main.py
```

### Issue: Want to skip failed documents

**Solution**: Remove them from progress file

```bash
# Edit JSON directly (use your editor)
# Find entries with "status": "error"
# Change to "status": "pending"

# Or delete them entirely (they'll be re-processed)

# Then re-run
python src/main.py
```

## Performance Tips

### For Faster Indexing:

1. **Increase batch size** (if you have memory):

   ```yaml
   indexing:
     batch_size: 100
   ```

2. **Ensure LM Studio is on same machine or low-latency network**

   - Network latency multiplies when embedding many chunks

3. **Use SSD storage** for output and ChromaDB
   - Progress file written frequently

### For Safer Indexing:

1. **Decrease batch size** (less data lost on crash):

   ```yaml
   indexing:
     batch_size: 25
   ```

2. **Backup progress file periodically**:

   ```bash
   cp output/indexing_progress.json output/indexing_progress.json.backup
   ```

3. **Monitor system resources**:
   ```bash
   watch -n 2 free -h  # Memory
   watch -n 2 iostat   # Disk
   ```

## Batch Size Recommendations

| Scenario                | Batch Size | Reason               |
| ----------------------- | ---------- | -------------------- |
| Development/Testing     | 5-10       | Fast feedback        |
| 8GB RAM                 | 25         | Safe memory usage    |
| 16GB RAM                | 50-100     | Good balance         |
| 32GB+ RAM               | 100-200    | Maximum throughput   |
| High reliability needed | 10-25      | Frequent checkpoints |
| Slow network            | 10         | Retry faster         |
| Fast network + local    | 100+       | Fewer checkpoints    |

## Future Enhancements

- [ ] Web dashboard showing real-time progress
- [ ] Automatic retry with exponential backoff for failed documents
- [ ] Metrics/timing per batch
- [ ] Partial batch rollback on failure
- [ ] Parallel embedding while chunking next batch
- [ ] Progress notifications (email, Slack, etc.)
