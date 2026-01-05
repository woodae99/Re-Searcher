"""ChromaDB vector storage backend."""

from typing import Any, Dict, List, Tuple

import chromadb
from chromadb.config import Settings

from .base import VectorStore


class ChromaVectorStore(VectorStore):
    """Vector store using ChromaDB."""

    def __init__(self, config: dict):
        super().__init__(config)
        storage_config = config.get("storage", {})

        self.endpoint = storage_config.get("endpoint", "http://localhost:8000")
        self.collection_name = storage_config.get("collection_name", "research_library")
        self.distance_metric = storage_config.get("distance_metric", "cosine")

        # Map distance metric to ChromaDB format
        distance_map = {
            "cosine": "cosine",
            "l2": "l2",
            "ip": "ip",  # inner product
        }
        self.chroma_distance = distance_map.get(self.distance_metric, "cosine")

        # Initialize ChromaDB client
        self.client = chromadb.HttpClient(
            host=self._parse_host(self.endpoint),
            port=self._parse_port(self.endpoint),
            settings=Settings(anonymized_telemetry=False),
        )

        # Get or create collection
        self.collection = self._get_or_create_collection()

    def _parse_host(self, endpoint: str) -> str:
        """Parse host from endpoint URL."""
        # Remove protocol
        if "://" in endpoint:
            endpoint = endpoint.split("://", 1)[1]
        # Remove port
        if ":" in endpoint:
            endpoint = endpoint.split(":")[0]
        return endpoint

    def _parse_port(self, endpoint: str) -> int:
        """Parse port from endpoint URL."""
        # Remove protocol
        if "://" in endpoint:
            endpoint = endpoint.split("://", 1)[1]
        # Extract port
        if ":" in endpoint:
            port_str = endpoint.split(":")[1]
            # Remove any trailing path
            if "/" in port_str:
                port_str = port_str.split("/")[0]
            return int(port_str)
        return 8000  # Default ChromaDB port

    def _get_or_create_collection(self):
        """Get existing collection or create new one."""
        # Use get_or_create_collection which handles both cases
        collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": self.chroma_distance},
        )
        count = collection.count()
        if count > 0:
            print(f"✅ Connected to existing ChromaDB collection: {self.collection_name} ({count} documents)")
        else:
            print(f"✅ Created/connected to ChromaDB collection: {self.collection_name}")
        return collection

    def add_documents(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str] = None,
    ) -> None:
        """
        Add documents to ChromaDB.

        Args:
            texts: List of document texts
            embeddings: List of embedding vectors
            metadatas: List of metadata dictionaries
            ids: Optional list of document IDs
        """
        if not texts:
            return

        # Generate IDs if not provided
        if ids is None:
            ids = [f"doc_{i}" for i in range(len(texts))]

        # ChromaDB requires metadata values to be strings, ints, floats, or bools
        # Convert lists and other types to strings
        sanitized_metadatas = []
        for metadata in metadatas:
            sanitized = {}
            for key, value in metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    sanitized[key] = value
                elif isinstance(value, list):
                    # Convert lists to comma-separated strings
                    sanitized[key] = ", ".join(str(v) for v in value)
                else:
                    sanitized[key] = str(value)
            sanitized_metadatas.append(sanitized)

        try:
            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=sanitized_metadatas,
            )
        except Exception as e:
            print(f"❌ Error adding documents to ChromaDB: {e}")
            raise

    def search(
        self,
        query_embedding: List[float],
        k: int = 5,
        filter: Dict[str, Any] = None,
    ) -> List[Tuple[str, str, float, Dict[str, Any]]]:
        """
        Search for similar documents.

        Args:
            query_embedding: Query embedding vector
            k: Number of results to return
            filter: Optional metadata filter (ChromaDB where clause)

        Returns:
            List of tuples: (doc_id, text, score, metadata)
        """
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                where=filter,
                include=["documents", "metadatas", "distances"],
            )

            # Format results
            formatted_results = []
            if results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    doc_id = results["ids"][0][i]
                    text = results["documents"][0][i]
                    distance = results["distances"][0][i]
                    metadata = results["metadatas"][0][i]

                    # Convert distance to similarity score
                    # For cosine distance: similarity = 1 - distance
                    # For L2: we'll just use distance as-is (lower is better)
                    if self.chroma_distance == "cosine":
                        score = 1 - distance
                    else:
                        score = distance

                    formatted_results.append((doc_id, text, score, metadata))

            return formatted_results

        except Exception as e:
            print(f"❌ Error searching ChromaDB: {e}")
            return []

    def delete_collection(self) -> None:
        """Delete the entire collection."""
        try:
            self.client.delete_collection(name=self.collection_name)
            print(f"🗑️  Deleted collection: {self.collection_name}")
        except Exception as e:
            print(f"❌ Error deleting collection: {e}")

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the collection."""
        try:
            count = self.collection.count()
            return {
                "collection_name": self.collection_name,
                "document_count": count,
                "distance_metric": self.chroma_distance,
                "endpoint": self.endpoint,
            }
        except Exception as e:
            print(f"❌ Error getting collection stats: {e}")
            return {}
