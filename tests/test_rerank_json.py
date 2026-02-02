import pytest

from src.retrieval.rerank import LLMReranker


def _make_reranker():
    # Minimal config for constructing LLMReranker; we won't call the model.
    return LLMReranker(
        {
            "retrieval": {
                "rerank": {
                    "llm": {"provider": "lmstudio", "model": "ibm/granite-4-micro"},
                }
            },
            "embedding": {"endpoint": "http://localhost:1234/v1", "lmstudio": {}},
        }
    )


def test_parse_scores_json_direct():
    r = _make_reranker()
    txt = '{"scores": [{"id": "a", "score": 10}]}'
    parsed = r._parse_scores_json(txt)
    assert parsed["scores"][0]["id"] == "a"


def test_parse_scores_json_wrapped_text():
    r = _make_reranker()
    txt = 'nonsense prefix\n{"scores": [{"id": "a", "score": 10}]}\ntrailing'
    parsed = r._parse_scores_json(txt)
    assert parsed["scores"][0]["score"] == 10


def test_parse_scores_json_truncated_returns_none():
    r = _make_reranker()
    txt = '{"scores": [{"id": "a", "score": 10}, {"id":'
    assert r._parse_scores_json(txt) is None
