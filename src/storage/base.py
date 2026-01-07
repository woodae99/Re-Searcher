"""Abstract base class for vector storage backends."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple


class VectorStore(ABC):
    """Abstract base class for vector storage backends."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def add_documents(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str] = None,
    ) -> None:
        """
        Add documents with their embeddings and metadata to the store.

        Args:
            texts: List of document texts
            embeddings: List of embedding vectors
            metadatas: List of metadata dictionaries
            ids: Optional list of document IDs
        """
        pass

    @abstractmethod
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
            filter: Optional metadata filter

        Returns:
            List of tuples: (doc_id, text, score, metadata)
        """
        pass

    @abstractmethod
    def delete_collection(self) -> None:
        """Delete the entire collection."""
        pass

    @abstractmethod
    def get_by_ids(self, ids: List[str]) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Fetch documents by IDs."""
        pass

    @abstractmethod
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the collection."""
        pass
