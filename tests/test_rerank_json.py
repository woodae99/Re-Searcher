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
    txt = '{"scores": [{"idx": 0, "score": 10}]}'
    parsed = r._parse_scores_json(txt)
    assert parsed["scores"][0]["idx"] == 0


def test_parse_scores_json_wrapped_text():
    r = _make_reranker()
    txt = 'nonsense prefix\n{"scores": [{"idx": 0, "score": 10}]}\ntrailing'
    parsed = r._parse_scores_json(txt)
    assert parsed["scores"][0]["score"] == 10


def test_parse_scores_json_truncated_extracts_pairs():
    r = _make_reranker()
    # Missing closing brackets/braces, but contains at least one full pair
    txt = '{"scores": [{"idx": 7, "score": 100}, {"idx":'
    parsed = r._parse_scores_json(txt)
    assert parsed is not None
    assert parsed["scores"][0] == {"idx": 7, "score": 100}
