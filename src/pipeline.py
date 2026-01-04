"""Main pipeline for indexing research library into vector store."""

import hashlib
from pathlib import Path
from typing import Any, Dict, List

import yaml
from tqdm import tqdm

from .embedding.lmstudio import LMStudioEmbedding
from .processing.chunker import TextChunker
from .sources.obsidian import ObsidianSource
from .sources.zotero import ZoteroSource
from .storage.chroma import ChromaVectorStore


class ResearchRAGPipeline:
    """Main pipeline for indexing research library."""

    def __init__(self, config_path: Path):
        """
        Initialize pipeline with configuration.

        Args:
            config_path: Path to YAML configuration file
        """
        self.config = self._load_config(config_path)
        self.config_path = config_path

        # Initialize components
        self.sources = self._initialize_sources()
        self.chunker = TextChunker(self.config)
        self.embedder = LMStudioEmbedding(self.config)
        self.vector_store = ChromaVectorStore(self.config)

        # Output directory for metadata
        self.output_dir = Path(self.config.get("output_folder", "./output"))
        self.output_dir.mkdir(exist_ok=True)

    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    def _initialize_sources(self) -> List:
        """Initialize all enabled data sources."""
        sources = []

        # Zotero source
        zotero = ZoteroSource(self.config)
        if zotero.is_enabled() and zotero.validate_config():
            sources.append(zotero)
            print("✅ Zotero source enabled")

        # Obsidian source
        obsidian = ObsidianSource(self.config)
        if obsidian.is_enabled() and obsidian.validate_config():
            sources.append(obsidian)
            print("✅ Obsidian source enabled")

        if not sources:
            print("⚠️  No data sources enabled!")

        return sources

    def run(self, force_reindex: bool = False):
        """
        Run the full indexing pipeline.

        Args:
            force_reindex: If True, re-index even if sources haven't changed
        """
        print("\n" + "=" * 60)
        print("Research RAG Pipeline - Indexing")
        print("=" * 60 + "\n")

        # Check if re-indexing is needed
        if not force_reindex and not self._needs_reindex():
            print("📦 Index is up to date. Use --force to re-index anyway.")
            return

        # Step 1: Fetch documents from all sources
        print("\n📚 Step 1/4: Fetching documents from sources...")
        documents = self._fetch_all_documents()
        print(f"✅ Fetched {len(documents)} documents")

        if not documents:
            print("⚠️  No documents to index!")
            return

        # Step 2: Chunk documents
        print("\n✂️  Step 2/4: Chunking documents...")
        chunks, chunk_metadatas, chunk_ids = self._chunk_documents(documents)
        print(f"✅ Created {len(chunks)} chunks")

        # Step 3: Generate embeddings
        print("\n🧠 Step 3/4: Generating embeddings...")
        embeddings = self._generate_embeddings(chunks)
        print(f"✅ Generated {len(embeddings)} embeddings")

        # Step 4: Store in vector database
        print("\n💾 Step 4/4: Storing in ChromaDB...")
        self._store_embeddings(chunks, embeddings, chunk_metadatas, chunk_ids)
        print(f"✅ Stored {len(chunks)} chunks in ChromaDB")

        # Save hash of sources
        self._save_source_hash()

        # Print stats
        stats = self.vector_store.get_collection_stats()
        print("\n" + "=" * 60)
        print("Indexing Complete!")
        print("=" * 60)
        print(f"Collection: {stats.get('collection_name')}")
        print(f"Total documents: {stats.get('document_count')}")
        print(f"Endpoint: {stats.get('endpoint')}")
        print("=" * 60 + "\n")

    def _fetch_all_documents(self) -> List:
        """Fetch documents from all sources."""
        all_documents = []

        for source in self.sources:
            print(f"\n  Fetching from {source.__class__.__name__}...")
            try:
                documents = list(source.fetch_documents())
                all_documents.extend(documents)
                print(f"  ✅ Fetched {len(documents)} documents")
            except Exception as e:
                print(f"  ❌ Error fetching from {source.__class__.__name__}: {e}")

        return all_documents

    def _chunk_documents(self, documents: List) -> tuple:
        """
        Chunk documents into smaller segments.

        Returns:
            Tuple of (chunks, metadatas, ids)
        """
        all_chunks = []
        all_metadatas = []
        all_ids = []

        for doc in tqdm(documents, desc="Chunking"):
            try:
                chunk_data = self.chunker.chunk_with_metadata(doc.content, doc.metadata)

                for idx, (chunk_text, chunk_metadata) in enumerate(chunk_data):
                    all_chunks.append(chunk_text)
                    all_metadatas.append(chunk_metadata)

                    # Generate unique ID for chunk
                    chunk_id = f"{doc.doc_id}-chunk-{idx}"
                    all_ids.append(chunk_id)

            except Exception as e:
                print(f"  ⚠️  Error chunking document {doc.doc_id}: {e}")

        return all_chunks, all_metadatas, all_ids

    def _generate_embeddings(self, chunks: List[str]) -> List[List[float]]:
        """Generate embeddings for chunks."""
        try:
            embeddings = self.embedder.embed_texts(chunks)
            return embeddings
        except Exception as e:
            print(f"❌ Error generating embeddings: {e}")
            raise

    def _store_embeddings(
        self,
        chunks: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str],
    ):
        """Store embeddings in vector database."""
        try:
            # Store in batches to avoid overwhelming ChromaDB
            batch_size = 100
            for i in tqdm(range(0, len(chunks), batch_size), desc="Storing"):
                batch_end = min(i + batch_size, len(chunks))
                self.vector_store.add_documents(
                    texts=chunks[i:batch_end],
                    embeddings=embeddings[i:batch_end],
                    metadatas=metadatas[i:batch_end],
                    ids=ids[i:batch_end],
                )
        except Exception as e:
            print(f"❌ Error storing embeddings: {e}")
            raise

    def _compute_source_hash(self) -> str:
        """Compute hash of all data sources to detect changes."""
        h = hashlib.md5()

        # Hash config file
        if self.config_path.exists():
            h.update(self.config_path.read_bytes())

        # Hash Zotero database if enabled
        zotero_cfg = self.config.get("zotero", {})
        if zotero_cfg.get("enabled"):
            zotero_db = Path(zotero_cfg.get("data_directory", "")).expanduser() / "zotero.sqlite"
            if zotero_db.exists():
                h.update(str(zotero_db.stat().st_mtime).encode())

        # Hash Obsidian vault if enabled
        obsidian_cfg = self.config.get("obsidian", {})
        if obsidian_cfg.get("enabled"):
            vault_path = Path(obsidian_cfg.get("vault_path", "")).expanduser()
            if vault_path.exists():
                # Hash all markdown files
                for md_file in sorted(vault_path.rglob("*.md")):
                    h.update(str(md_file.stat().st_mtime).encode())

        return h.hexdigest()

    def _needs_reindex(self) -> bool:
        """Check if re-indexing is needed based on source changes."""
        hash_file = self.output_dir / "source_hash.txt"

        current_hash = self._compute_source_hash()
        if not hash_file.exists():
            return True

        previous_hash = hash_file.read_text().strip()
        return current_hash != previous_hash

    def _save_source_hash(self):
        """Save current source hash."""
        hash_file = self.output_dir / "source_hash.txt"
        current_hash = self._compute_source_hash()
        hash_file.write_text(current_hash)

    def query(self, query_text: str, k: int = 5) -> List:
        """
        Query the vector store.

        Args:
            query_text: Query string
            k: Number of results to return

        Returns:
            List of search results
        """
        print(f"\n🔍 Querying: {query_text}\n")

        # Generate query embedding
        query_embedding = self.embedder.embed_query(query_text)

        # Search vector store
        results = self.vector_store.search(query_embedding, k=k)

        return results
