"""Re-ranking implementations for recall results."""

import json
import os
import re
from typing import Any, Dict, List, Tuple, Optional

from openai import OpenAI

from src.llm_clients.lmstudio_client import LMStudioClient

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


class LLMReranker(BaseReranker):
    """LLM-based reranker using LM Studio or OpenAI style endpoints."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        rerank_config = config.get("retrieval", {}).get("rerank", {})
        llm_config = rerank_config.get("llm", {})

        self.provider = llm_config.get("provider", "lmstudio")
        self.model = llm_config.get("model")
        self.max_tokens = llm_config.get("max_tokens", 256)
        self.temperature = llm_config.get("temperature", 0.0)

        # Bound rerank input size
        self.max_candidates = rerank_config.get("max_candidates", 30)
        self.max_chars_per_candidate = rerank_config.get("max_chars_per_candidate", 1200)

        if self.provider == "lmstudio":
            if not self.model:
                raise ValueError(
                    "LM Studio reranker requires retrieval.rerank.llm.model when rerank is enabled."
                )
            self.client = LMStudioClient(config)
        elif self.provider == "openai":
            embedding_config = config.get("embedding", {})
            openai_config = embedding_config.get("openai", {})
            api_key = openai_config.get("api_key") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OpenAI API key not configured for reranking.")
            if not self.model:
                raise ValueError("OpenAI reranker requires a model name.")
            client_kwargs: Dict[str, Any] = {"api_key": api_key}
            base_url = openai_config.get("base_url")
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = OpenAI(**client_kwargs)
        else:
            raise ValueError(f"Unsupported rerank provider '{self.provider}'.")

    def rerank(self, query: str, results: List[ResultTuple]) -> List[ResultTuple]:
        if not results:
            return results

        # Bound reranker work to avoid context blowups and truncation.
        max_candidates = int(self.max_candidates) if self.max_candidates else len(results)
        max_chars = int(self.max_chars_per_candidate) if self.max_chars_per_candidate else None
        candidates = results[:max_candidates]

        # Use compact integer indices in the rerank payload to reduce output size
        # and avoid JSON truncation when document IDs are long.
        idx_to_id: Dict[int, str] = {}
        compact_results = []
        for idx, (doc_id, text, _, _) in enumerate(candidates):
            idx_to_id[idx] = doc_id
            compact_results.append(
                {
                    "idx": idx,
                    "text": (text[:max_chars] if (max_chars and isinstance(text, str)) else text),
                }
            )

        payload = {
            "query": query,
            "results": compact_results,
        }

        prompt = (
            "You are a relevance reranker. "
            "Score each candidate from 0 to 100 based on relevance to the query. "
            "Return strict JSON as {\"scores\": [{\"idx\": <int>, \"score\": <int>}, ...]}. "
            "Do not include any extra text."
        )

        response_text = self._invoke_model(prompt, payload)

        parsed = self._parse_scores_json(response_text)
        if parsed is None:
            raise ValueError(f"Invalid JSON from reranker: {response_text}")

        # Support either idx-based or id-based responses.
        scores_by_id: Dict[str, int] = {}
        for item in parsed.get("scores", []):
            if not isinstance(item, dict):
                continue
            if "idx" in item:
                try:
                    idx = int(item["idx"])
                except Exception:
                    continue
                doc_id = idx_to_id.get(idx)
                if not doc_id:
                    continue
                scores_by_id[doc_id] = item.get("score", 0)
            elif "id" in item:
                scores_by_id[item["id"]] = item.get("score", 0)

        reranked = []
        for doc_id, text, score, metadata in results:
            rerank_score = scores_by_id.get(doc_id, 0)
            new_metadata = dict(metadata)
            new_metadata["rerank_score"] = rerank_score
            reranked.append((doc_id, text, score, new_metadata))

        reranked.sort(key=lambda item: item[3].get("rerank_score", 0), reverse=True)
        return reranked

    def _parse_scores_json(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Best-effort parse of reranker JSON.

        Some models/endpoints may prepend/append text or truncate output.
        We try:
        1) direct json.loads
        2) extract substring between first '{' and last '}' and parse
        """
        if not response_text:
            return None
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        start = response_text.find("{")
        end = response_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = response_text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # Last-resort: regex-extract (idx, score) pairs from partially-truncated output.
        # This is intentionally narrow to the expected schema.
        pairs = re.findall(r"\{\s*\"idx\"\s*:\s*(\d+)\s*,\s*\"score\"\s*:\s*(\d+)\s*\}", response_text)
        if pairs:
            return {
                "scores": [
                    {"idx": int(idx), "score": int(score)} for idx, score in pairs
                ]
            }

        return None

    def _invoke_model(self, prompt: str, payload: Dict[str, Any]) -> str:
        if self.provider == "lmstudio":
            return self.client.chat_completion(
                system_prompt=prompt,
                user_message=json.dumps(payload),
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return response.choices[0].message.content
