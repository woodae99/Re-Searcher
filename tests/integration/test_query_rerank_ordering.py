import pytest

from src.pipeline import ResearchRAGPipeline


class DummyEmbedder:
    def embed_query(self, query):
        return [0.0]


class DummyVectorStore:
    def search(self, query_embedding, k=5, filter=None):
        return [
            ("id-1", "first", 0.9, {}),
            ("id-2", "second", 0.8, {}),
        ]


class ReverseReranker:
    def rerank(self, query, results):
        return list(reversed(results))


@pytest.mark.integration
def test_query_rerank_ordering():
    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.config = {
        "retrieval": {
            "k_recall": 5,
            "rerank": {"enabled": True, "top_n": None},
            "expand": {"include_parent": False},
        }
    }
    pipeline.embedder = DummyEmbedder()
    pipeline.vector_store = DummyVectorStore()
    pipeline.reranker = ReverseReranker()

    results = ResearchRAGPipeline.query(pipeline, "query", k=2)

    assert results[0][0] == "id-2"
    assert results[1][0] == "id-1"
