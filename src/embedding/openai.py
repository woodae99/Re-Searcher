"""OpenAI embedding provider."""

import os
from typing import List

from openai import OpenAI

from .base import EmbeddingProvider


class OpenAIEmbedding(EmbeddingProvider):
    """Embedding provider using OpenAI's API."""

    def __init__(self, config: dict):
        super().__init__(config)
        embedding_config = config.get("embedding", {})
        openai_config = embedding_config.get("openai", {})

        api_key = openai_config.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not configured. Set embedding.openai.api_key or OPENAI_API_KEY.")

        base_url = openai_config.get("base_url")
        model = openai_config.get("model", "text-embedding-3-large")
        timeout = openai_config.get("timeout_seconds", 60)
        batch_size = openai_config.get("batch_size", 32)

        client_kwargs = {"api_key": api_key, "timeout": timeout}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.batch_size = batch_size
        self._dimension = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)
            batch_embeddings = [item.embedding for item in response.data]
            embeddings.extend(batch_embeddings)

            if self._dimension is None and batch_embeddings:
                self._dimension = len(batch_embeddings[0])

        return embeddings

    def embed_query(self, query: str) -> List[float]:
        response = self.client.embeddings.create(model=self.model, input=[query])
        embedding = response.data[0].embedding

        if self._dimension is None:
            self._dimension = len(embedding)

        return embedding

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            dummy_embedding = self.embed_query("dimension probe")
            self._dimension = len(dummy_embedding)
        return self._dimension
