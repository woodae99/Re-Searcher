import pytest

from src.pipeline import ResearchRAGPipeline
from src.processing.chunker import TextChunker
from src.sources.base import Document


class DummyEmbedder:
    def embed_texts(self, texts):
        return [[float(len(text))] for text in texts]

    def embed_query(self, query):
        return [float(len(query))]


class DummyVectorStore:
    def __init__(self):
        self.records = []

    def add_documents(self, texts, embeddings, metadatas, ids=None):
        for doc_id, text, metadata in zip(ids, texts, metadatas):
            self.records.append((doc_id, text, 1.0, metadata))

    def search(self, query_embedding, k=5):
        return self.records[:k]


class ReverseReranker:
    def rerank(self, query, results):
        return list(reversed(results))


@pytest.mark.pipeline
@pytest.mark.slow
def test_minimal_pipeline_end_to_end():
    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.config = {
        "chunking": {"chunk_size": 50, "chunk_overlap": 0, "strategy": "recursive"},
        "retrieval": {
            "k_recall": 5,
            "rerank": {"enabled": True, "top_n": None},
            "expand": {"include_parent": False},
        },
    }
    pipeline.chunker = TextChunker(pipeline.config)
    pipeline.embedder = DummyEmbedder()
    pipeline.vector_store = DummyVectorStore()
    pipeline.reranker = ReverseReranker()

    documents = [
        Document("First document content.", {"source_type": "obsidian"}, doc_id="doc-1"),
        Document("Second document content.", {"source_type": "obsidian"}, doc_id="doc-2"),
    ]

    chunks, metadatas, ids = ResearchRAGPipeline._chunk_batch(pipeline, documents)
    embeddings = pipeline.embedder.embed_texts(chunks)
    pipeline._store_batch(chunks, embeddings, metadatas, ids)

    results = ResearchRAGPipeline.query(pipeline, "query", k=2)

    assert results
    assert results[0][0] == ids[-1]
