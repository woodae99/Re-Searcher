"""LM Studio embedding provider using OpenAI-compatible API."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import List
import os

from openai import OpenAI

from .base import EmbeddingProvider


class LMStudioEmbedding(EmbeddingProvider):
    """Embedding provider using LM Studio's OpenAI-compatible API."""

    def __init__(self, config: dict):
        super().__init__(config)
        embedding_config = config.get("embedding", {})
        lmstudio_config = embedding_config.get("lmstudio", {})

        self.endpoint = (
            lmstudio_config.get("base_url")
            or embedding_config.get("endpoint")
            or "http://localhost:1234/v1"
        )
        self.model = (
            lmstudio_config.get("model")
            or embedding_config.get("model")
            or "text-embedding-bge-m3"
        )
        self.batch_size = int(lmstudio_config.get("batch_size", embedding_config.get("batch_size", 32)))
        self.batch_size = max(1, self.batch_size)
        self.max_concurrent_requests = int(
            lmstudio_config.get(
                "max_concurrent_requests",
                embedding_config.get("max_concurrent_requests", 1),
            )
        )
        self.max_concurrent_requests = max(1, self.max_concurrent_requests)
        self.timeout = lmstudio_config.get("timeout_seconds", embedding_config.get("timeout", 30))
        self.api_key = lmstudio_config.get("api_key") or embedding_config.get("api_key")
        # Allow ${ENV_VAR} style indirection (common in config files)
        if isinstance(self.api_key, str) and self.api_key.startswith("${") and self.api_key.endswith("}"):
            self.api_key = os.getenv(self.api_key[2:-1])

        # Initialize OpenAI client pointing to LM Studio
        client_kwargs = {"base_url": self.endpoint, "timeout": self.timeout}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        self.client = OpenAI(**client_kwargs)

        # Cache dimension after first embedding
        self._dimension = None
        self._dimension_lock = Lock()

    def _cache_dimension(self, embeddings: List[List[float]]) -> None:
        """Cache the embedding dimension from the first successful response."""
        if not embeddings:
            return
        with self._dimension_lock:
            if self._dimension is None:
                self._dimension = len(embeddings[0])

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(
            model=self.model,
            input=batch,
        )

        response_data = response.data
        if response_data and hasattr(response_data[0], "index"):
            response_data = sorted(response_data, key=lambda item: item.index)

        batch_embeddings = [item.embedding for item in response_data]
        if len(batch_embeddings) != len(batch):
            raise RuntimeError(
                f"Embedding response length mismatch: expected {len(batch)}, got {len(batch_embeddings)}"
            )

        self._cache_dimension(batch_embeddings)
        return batch_embeddings

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

        batches = [
            (batch_idx, texts[i : i + self.batch_size])
            for batch_idx, i in enumerate(range(0, len(texts), self.batch_size))
        ]
        batch_results: List[List[List[float]] | None] = [None] * len(batches)
        failed_batches = []

        if self.max_concurrent_requests == 1 or len(batches) == 1:
            for batch_idx, batch in batches:
                try:
                    batch_results[batch_idx] = self._embed_batch(batch)
                except Exception as e:
                    print(f"❌ Error generating embeddings for batch {batch_idx}: {e}")
                    failed_batches.append((batch_idx, batch, e))
        else:
            worker_count = min(self.max_concurrent_requests, len(batches))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_batch = {
                    executor.submit(self._embed_batch, batch): (batch_idx, batch)
                    for batch_idx, batch in batches
                }
                for future in as_completed(future_to_batch):
                    batch_idx, batch = future_to_batch[future]
                    try:
                        batch_results[batch_idx] = future.result()
                    except Exception as e:
                        print(f"❌ Error generating embeddings for batch {batch_idx}: {e}")
                        failed_batches.append((batch_idx, batch, e))

        if failed_batches:
            if self._dimension is None:
                first_error = failed_batches[0][2]
                raise RuntimeError("Cannot determine embedding dimension") from first_error
            zero_vector = [0.0] * self._dimension
            for batch_idx, batch, _ in failed_batches:
                batch_results[batch_idx] = [zero_vector.copy() for _ in batch]

        embeddings = []
        for batch_embeddings in batch_results:
            if batch_embeddings is not None:
                embeddings.extend(batch_embeddings)

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
            self._cache_dimension([embedding])

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
