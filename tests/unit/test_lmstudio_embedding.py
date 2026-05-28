import time
from threading import Lock

from src.embedding import lmstudio
from src.embedding.lmstudio import LMStudioEmbedding


class _FakeEmbeddingItem:
    def __init__(self, index, embedding):
        self.index = index
        self.embedding = embedding


class _FakeEmbeddingResponse:
    def __init__(self, data):
        self.data = data


class _FakeEmbeddingsEndpoint:
    def __init__(self):
        self.lock = Lock()
        self.active = 0
        self.max_active = 0
        self.batch_sizes = []

    def create(self, model, input):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.batch_sizes.append(len(input))

        time.sleep(0.01)

        with self.lock:
            self.active -= 1

        return _FakeEmbeddingResponse(
            [
                _FakeEmbeddingItem(index, [float(text.removeprefix("text-"))])
                for index, text in enumerate(input)
            ]
        )


class _FakeOpenAIClient:
    def __init__(self):
        self.embeddings = _FakeEmbeddingsEndpoint()


def test_lmstudio_embed_texts_uses_configured_parallel_batches(monkeypatch):
    fake_client = _FakeOpenAIClient()
    monkeypatch.setattr(lmstudio, "OpenAI", lambda **kwargs: fake_client)

    embedder = LMStudioEmbedding(
        {
            "embedding": {
                "endpoint": "http://localhost:1234/v1",
                "model": "text-embedding-bge-m3",
                "batch_size": 128,
                "max_concurrent_requests": 2,
            }
        }
    )

    texts = [f"text-{index}" for index in range(300)]

    embeddings = embedder.embed_texts(texts)

    assert embeddings == [[float(index)] for index in range(300)]
    assert sorted(fake_client.embeddings.batch_sizes) == [44, 128, 128]
    assert fake_client.embeddings.max_active == 2
    assert embedder.dimension == 1
