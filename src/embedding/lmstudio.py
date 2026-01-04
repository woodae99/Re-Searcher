"""LM Studio embedding provider using OpenAI-compatible API."""

from typing import List

from openai import OpenAI

from .base import EmbeddingProvider


class LMStudioEmbedding(EmbeddingProvider):
    """Embedding provider using LM Studio's OpenAI-compatible API."""

    def __init__(self, config: dict):
        super().__init__(config)
        embedding_config = config.get("embedding", {})

        self.endpoint = embedding_config.get("endpoint", "http://localhost:1234/v1")
        self.model = embedding_config.get("model", "text-embedding-bge-m3")
        self.batch_size = embedding_config.get("batch_size", 32)
        self.timeout = embedding_config.get("timeout", 30)

        # Initialize OpenAI client pointing to LM Studio
        self.client = OpenAI(
            base_url=self.endpoint,
            api_key="lm-studio",  # LM Studio doesn't require a real API key
            timeout=self.timeout,
        )

        # Cache dimension after first embedding
        self._dimension = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        embeddings = []

        # Process in batches
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                )

                batch_embeddings = [item.embedding for item in response.data]
                embeddings.extend(batch_embeddings)

                # Cache dimension from first response
                if self._dimension is None and batch_embeddings:
                    self._dimension = len(batch_embeddings[0])

            except Exception as e:
                print(f"❌ Error generating embeddings for batch {i // self.batch_size}: {e}")
                # Return zero vectors for failed batch
                if self._dimension:
                    embeddings.extend([[0.0] * self._dimension] * len(batch))
                else:
                    raise RuntimeError("Cannot determine embedding dimension") from e

        return embeddings

    def embed_query(self, query: str) -> List[float]:
        """
        Generate embedding for a single query.

        Args:
            query: Query text to embed

        Returns:
            Embedding vector
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=[query],
            )

            embedding = response.data[0].embedding

            # Cache dimension
            if self._dimension is None:
                self._dimension = len(embedding)

            return embedding

        except Exception as e:
            print(f"❌ Error generating query embedding: {e}")
            if self._dimension:
                return [0.0] * self._dimension
            raise

    @property
    def dimension(self) -> int:
        """Return the dimension of the embedding vectors."""
        if self._dimension is None:
            # Generate a dummy embedding to determine dimension
            try:
                dummy_embedding = self.embed_query("test")
                self._dimension = len(dummy_embedding)
            except Exception:
                # Default dimension for BGE-M3
                self._dimension = 1024

        return self._dimension
