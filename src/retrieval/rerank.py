"""Re-ranking implementations for recall results."""

import json
import urllib.request
from typing import Any, Dict, List, Tuple

ResultTuple = Tuple[str, str, float, Dict[str, Any]]


class BaseReranker:
    """Base class for reranking implementations."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def rerank(self, query: str, results: List[ResultTuple]) -> List[ResultTuple]:
        raise NotImplementedError


class NoRerank(BaseReranker):
    """No-op reranker."""

    def rerank(self, query: str, results: List[ResultTuple]) -> List[ResultTuple]:
        return results


class CrossEncoderReranker(BaseReranker):
    """Cross-encoder reranker via an OpenAI-compatible /rerank endpoint (vLLM).

    Scores (query, document) text pairs directly with a cross-encoder (e.g.
    BAAI/bge-reranker-v2-m3) with no LLM-emitted JSON to parse. This is the v0.6 production
    reranker runtime; see docs/EMBEDDING_BACKEND.md and docs/RERANKER_BAKEOFF.md.

    Matches vLLM's /rerank contract:
        POST {model, query, documents:[text,...]}
        -> {"results": [{"index": i, "relevance_score": s}, ...]}  (sorted desc)
    Scores are mapped back onto the input ResultTuples by position.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        rerank_config = config.get("retrieval", {}).get("rerank", {})
        ce = rerank_config.get("cross_encoder", {})
        base_url = (ce.get("base_url") or "http://localhost:8005/v1").rstrip("/")
        self.url = f"{base_url}/rerank"
        self.model = ce.get("model")
        self.timeout = int(ce.get("timeout_seconds", 60))
        # Reuse the generic rerank bounds.
        self.max_candidates = int(rerank_config.get("max_candidates") or 0)
        self.max_chars_per_candidate = int(rerank_config.get("max_chars_per_candidate") or 0)
        if not self.model:
            raise ValueError(
                "Cross-encoder reranker requires retrieval.rerank.cross_encoder.model."
            )

    def rerank(self, query: str, results: List[ResultTuple]) -> List[ResultTuple]:
        if not results:
            return results

        candidates = results[: self.max_candidates] if self.max_candidates else list(results)
        documents = [
            (text[: self.max_chars_per_candidate]
             if (self.max_chars_per_candidate and isinstance(text, str)) else text)
            for _doc_id, text, _score, _meta in candidates
        ]
        payload = {"model": self.model, "query": query, "documents": documents}
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        scores_by_pos: Dict[int, float] = {}
        for item in data.get("results", []):
            idx = item.get("index")
            if idx is None:
                continue
            scores_by_pos[int(idx)] = float(
                item.get("relevance_score", item.get("score", 0.0))
            )

        reranked: List[ResultTuple] = []
        for pos, (doc_id, text, score, metadata) in enumerate(candidates):
            new_metadata = dict(metadata)
            new_metadata["rerank_score"] = scores_by_pos.get(pos, 0.0)
            reranked.append((doc_id, text, score, new_metadata))
        reranked.sort(key=lambda item: item[3].get("rerank_score", 0.0), reverse=True)
        # Keep any candidates beyond the reranked window, after the scored ones.
        if self.max_candidates and len(results) > len(candidates):
            reranked.extend(results[len(candidates):])
        return reranked
