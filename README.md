# Research RAG Pipeline

**Semantic Search & RAG for Researchers**

A modular Python pipeline for indexing your research library (Zotero + Obsidian) into ChromaDB for semantic search and RAG (Retrieval-Augmented Generation) queries.

## Features

- **Multi-Source Indexing**: Automatically index from multiple sources
  - Zotero library (items, notes, annotations, PDF attachments)
  - Obsidian vault (markdown notes with frontmatter)
  - Local files (PDF, DOCX, HTML, Markdown, EPUB)

- **Flexible Embedding**: Use LM Studio (local) or SentenceTransformers
  - LM Studio with BGE-M3 (recommended for quality)
  - SentenceTransformers (legacy support)

- **ChromaDB Vector Storage**: Persistent, scalable vector database
  - HTTP client for Docker deployments
  - Automatic collection management
  - Metadata filtering

- **Rich Metadata**: Preserve context for RAG applications
  - Source type tracking
  - Author, title, year metadata
  - Deep links back to original sources (`zotero://` and `obsidian://`)
  - Wikilinks and backlinks

- **Modular Architecture**: Easy to extend with new sources or embedding models
  - Abstract base classes for sources, embeddings, and storage
  - Config-driven design
  - No hardcoded paths

## Architecture

```
src/
├── sources/          # Data source integrations
│   ├── base.py       # Abstract DataSource class
│   ├── zotero.py     # Zotero SQLite reader
│   └── obsidian.py   # Obsidian vault reader
├── processing/       # Text processing
│   └── chunker.py    # Text chunking (langchain)
├── embedding/        # Embedding providers
│   ├── base.py       # Abstract EmbeddingProvider
│   └── lmstudio.py   # LM Studio (OpenAI-compatible API)
├── storage/          # Vector storage backends
│   ├── base.py       # Abstract VectorStore
│   └── chroma.py     # ChromaDB HTTP client
└── pipeline.py       # Main orchestration
```

## Prerequisites

1. **ChromaDB** running on `localhost:8000`:
   ```bash
   docker run -p 8000:8000 chromadb/chroma
   ```

2. **LM Studio** running on `localhost:1234` with BGE-M3 model:
   - Download LM Studio from https://lmstudio.ai/
   - Load the `text-embedding-bge-m3` model
   - Start the local server

3. **Python 3.8+**

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/research-rag.git
   cd research-rag
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create your configuration:
   ```bash
   cp config.example.yaml config.yaml
   # Edit config.yaml with your paths
   ```

## Configuration

Edit `config.yaml` to configure your data sources:

```yaml
# Zotero configuration
zotero:
  enabled: true
  data_directory: "/path/to/Zotero"  # Update this path
  extract_attachments: true
  include_notes: true
  include_annotations: true

# Obsidian configuration
obsidian:
  enabled: true
  vault_path: "/path/to/ObsidianVault"  # Update this path
  include_folders:
    - "LitNotes"
    - "Concepts"
  exclude_folders:
    - "Templates"
    - ".obsidian"

# Embedding configuration
embedding:
  provider: lmstudio
  endpoint: "http://localhost:1234/v1"
  model: "text-embedding-bge-m3"

# Storage configuration
storage:
  provider: chroma
  endpoint: "http://localhost:8000"
  collection_name: "research_library"
```

## Usage

### 1. Index Your Library

Run the indexing pipeline to extract and embed all documents:

```bash
python scripts/index.py
```

This will:
1. Fetch documents from Zotero and Obsidian
2. Chunk documents into smaller segments
3. Generate embeddings using LM Studio
4. Store in ChromaDB

**Options:**
- `--force`: Force re-indexing even if sources haven't changed
- `--config PATH`: Use a different config file

### 2. Query Your Library

#### Interactive Mode

```bash
python scripts/query.py
```

This starts an interactive query session with commands:
- `<query text>` - Search for documents
- `:full` - Toggle full text display
- `:k <number>` - Set number of results
- `:stats` - Show collection statistics
- `:quit` or `:q` - Exit

#### Single Query Mode

```bash
python scripts/query.py "your query here"
```

**Options:**
- `-k N`: Number of results (default: 5)
- `--full`: Show full text instead of preview

## Example Queries

```bash
# Find papers about attention mechanisms
python scripts/query.py "attention mechanisms in transformers"

# Get implementation details
python scripts/query.py "how to implement RLHF" -k 10

# Find related concepts in your notes
python scripts/query.py "embodied cognition" --full
```

## Metadata Schema

Each chunk stored in ChromaDB includes rich metadata:

```python
{
    # Common fields
    "source_type": "zotero_fulltext" | "zotero_note" | "zotero_annotation" | "obsidian",
    "title": "Paper title",
    "authors": "Author names",
    "year": "2024",
    "chunk_index": 0,
    "total_chunks": 10,

    # Zotero-specific
    "zotero_key": "KYRAUWYK",
    "zotero_id": 12345,
    "backlink": "zotero://select/items/KYRAUWYK",
    "tags": ["tag1", "tag2"],
    "collections": ["My Collection"],

    # Obsidian-specific
    "vault_path": "/path/to/vault",
    "file_path": "/path/to/note.md",
    "relative_path": "Folder/Note.md",
    "wikilinks": ["[[Related Note]]"],
    "backlink": "obsidian://open?vault=MyVault&file=Folder/Note.md",

    # Annotations
    "page": "42",
    "annotation_id": 67890,
}
```

## Extending the Pipeline

### Add a New Data Source

1. Create a new file in `src/sources/`:
   ```python
   from .base import DataSource, Document

   class MySource(DataSource):
       def is_enabled(self) -> bool:
           return self.config.get("mysource", {}).get("enabled", False)

       def fetch_documents(self):
           # Yield Document objects
           yield Document(content="...", metadata={...})
   ```

2. Register in `src/pipeline.py`:
   ```python
   from .sources.mysource import MySource

   # In _initialize_sources():
   mysource = MySource(self.config)
   if mysource.is_enabled():
       sources.append(mysource)
   ```

### Add a New Embedding Provider

1. Create `src/embedding/myprovider.py`:
   ```python
   from .base import EmbeddingProvider

   class MyEmbedding(EmbeddingProvider):
       def embed_texts(self, texts):
           # Return list of embeddings
           pass
   ```

2. Update `src/pipeline.py` to use it based on config.

## Project History

This project evolved from **Re-Searcher**, a semantic search tool for researchers. The refactoring focused on:
- Replacing FAISS with ChromaDB for better scalability
- Adding LM Studio support for local, high-quality embeddings
- Adding Obsidian vault integration
- Creating a modular architecture for easy extension
- Improving metadata schema for RAG applications

## Troubleshooting

**ChromaDB connection error:**
- Ensure ChromaDB is running: `docker ps | grep chroma`
- Check endpoint in config.yaml

**LM Studio embedding error:**
- Ensure LM Studio server is running
- Check that BGE-M3 model is loaded
- Verify endpoint: `http://localhost:1234/v1`

**No documents indexed:**
- Check that paths in config.yaml are correct
- Verify data sources are enabled
- Check permissions on Zotero/Obsidian directories

## License

MIT License - See LICENSE file

## Contributing

Contributions welcome! Please open an issue or PR.

## Credits

- Built on [ChromaDB](https://www.trychroma.com/)
- Uses [LM Studio](https://lmstudio.ai/) for local embeddings
- Text extraction via PyMuPDF, python-docx, and others
- Chunking via [langchain-text-splitters](https://python.langchain.com/)
