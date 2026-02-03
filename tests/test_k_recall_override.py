import yaml
from pathlib import Path

from src.pipeline import ResearchRAGPipeline


class _DummyStore:
    def __init__(self):
        self.calls = []

    def search(self, query_embedding, k=5, filter=None):
        self.calls.append({"k": k, "filter": filter})
        # return empty results
        return []


class _DummyEmbedder:
    def embed_query(self, q):
        return [0.0] * 3


def test_k_recall_override_used(tmp_path):
    cfg = {
        "retrieval": {"k_recall": 50, "rerank": {"enabled": False}, "diversity": {"enabled": False}},
        "storage": {"endpoint": "http://localhost:8000", "collection_name": "x"},
    }
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg))

    # construct pipeline without running full __init__
    pipe = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipe.config = cfg
    pipe.embedder = _DummyEmbedder()
    pipe.vector_store = _DummyStore()

    pipe.query("q", k=5, k_recall_override=123)
    assert pipe.vector_store.calls[0]["k"] == 123
