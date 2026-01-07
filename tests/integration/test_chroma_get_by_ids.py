import pytest

from src.storage.chroma import ChromaVectorStore


@pytest.mark.integration
@pytest.mark.requires_chromadb
def test_chroma_get_by_ids(integration_config):
    config = dict(integration_config)
    storage_config = dict(config.get("storage", {}))
    storage_config["collection_name"] = "test_get_by_ids"
    config["storage"] = storage_config

    try:
        store = ChromaVectorStore(config)
    except Exception as exc:
        pytest.skip(f"ChromaDB not available: {exc}")

    store.add_documents(
        texts=["alpha", "beta"],
        embeddings=[[0.1, 0.2], [0.2, 0.3]],
        metadatas=[{"label": "a"}, {"label": "b"}],
        ids=["doc-a", "doc-b"],
    )

    results = store.get_by_ids(["doc-a", "doc-b"])
    ids = {doc_id for doc_id, _, _ in results}

    assert {"doc-a", "doc-b"} == ids

    store.delete_collection()
