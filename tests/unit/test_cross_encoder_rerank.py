"""Unit tests for CrossEncoderReranker (vLLM /rerank). HTTP is mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.factories.reranker_factory import create_reranker  # noqa: E402
from src.retrieval.rerank import CrossEncoderReranker  # noqa: E402


def _config(**ce_overrides):
    ce = {"base_url": "http://localhost:8005/v1", "model": "BAAI/bge-reranker-v2-m3"}
    ce.update(ce_overrides)
    return {"retrieval": {"rerank": {"enabled": True, "type": "cross_encoder",
                                     "max_candidates": 30, "max_chars_per_candidate": 1200,
                                     "cross_encoder": ce}}}


def _results(*ids):
    # ResultTuple = (doc_id, text, score, metadata)
    return [(i, f"text for {i}", 0.0, {"source_id": i}) for i in ids]


def _patch_rerank(monkeyresults):
    """Patch urllib.request.urlopen to return a fixed /rerank response; capture request."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"results": monkeyresults}).encode()
        cm = mock.MagicMock()
        cm.__enter__.return_value = resp
        return cm

    return mock.patch("src.retrieval.rerank.urllib.request.urlopen", side_effect=fake_urlopen), captured


def test_factory_builds_cross_encoder():
    assert isinstance(create_reranker(_config()), CrossEncoderReranker)


def test_missing_model_raises():
    cfg = _config()
    cfg["retrieval"]["rerank"]["cross_encoder"].pop("model")
    try:
        CrossEncoderReranker(cfg)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_reorders_by_relevance_score_and_records_it():
    rr = CrossEncoderReranker(_config())
    # vLLM returns results sorted; index refers to position in the documents list.
    api = [{"index": 2, "relevance_score": 0.9},
           {"index": 0, "relevance_score": 0.5},
           {"index": 1, "relevance_score": 0.1}]
    patcher, captured = _patch_rerank(api)
    with patcher:
        out = rr.rerank("the query", _results("a", "b", "c"))
    # request shape
    assert captured["body"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert captured["body"]["query"] == "the query"
    assert captured["body"]["documents"] == ["text for a", "text for b", "text for c"]
    # output reordered c, a, b with scores in metadata
    assert [doc_id for doc_id, *_ in out] == ["c", "a", "b"]
    assert [round(m["rerank_score"], 2) for *_, m in out] == [0.9, 0.5, 0.1]


def test_truncates_documents_to_max_chars():
    rr = CrossEncoderReranker(_config(model="m"))
    rr.max_chars_per_candidate = 5
    patcher, captured = _patch_rerank([{"index": 0, "relevance_score": 1.0}])
    with patcher:
        rr.rerank("q", [("a", "abcdefghij", 0.0, {})])
    assert captured["body"]["documents"] == ["abcde"]


def test_empty_results_short_circuits():
    rr = CrossEncoderReranker(_config())
    assert rr.rerank("q", []) == []
