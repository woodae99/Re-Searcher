"""Re-ranking implementations for recall results."""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

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

        if self.provider == "lmstudio":
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

        payload = {
            "query": query,
            "results": [
                {
                    "id": doc_id,
                    "text": text,
                }
                for doc_id, text, _, _ in results
            ],
        }

        prompt = (
            "You are a relevance reranker. "
            "Score each candidate from 0 to 100 based on relevance to the query. "
            "Return strict JSON as {\"scores\": [{\"id\": ..., \"score\": ...}, ...]}."
        )

        payload_text = json.dumps(payload, ensure_ascii=True)
        self._log_debug(
            "rerank_request",
            {
                "model": self.model,
                "results_count": len(results),
                "query": query,
                "prompt": prompt,
                "payload_chars": len(payload_text),
                "payload_preview": self._truncate(payload_text),
            },
        )

        response_text = self._invoke_model(prompt, payload)
        self._log_debug(
            "rerank_response_raw",
            {
                "response_chars": len(response_text or ""),
                "response_preview": self._truncate(response_text or ""),
            },
        )
        response_text = self._clean_response_text(response_text)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            self._log_debug(
                "rerank_parse_error",
                {
                    "error": str(exc),
                    "response_preview": self._truncate(response_text),
                },
            )
            raise ValueError(f"Invalid JSON from reranker: {response_text}") from exc

        scores = {item["id"]: item.get("score", 0) for item in parsed.get("scores", [])}
        reranked = []
        for doc_id, text, score, metadata in results:
            rerank_score = scores.get(doc_id, 0)
            new_metadata = dict(metadata)
            new_metadata["rerank_score"] = rerank_score
            reranked.append((doc_id, text, score, new_metadata))

        reranked.sort(key=lambda item: item[3].get("rerank_score", 0), reverse=True)
        return reranked

    def _clean_response_text(self, response_text: str) -> str:
        """Strip reasoning tags and isolate JSON payload."""
        if not response_text:
            return response_text

        # Remove <think>...</think> blocks used by some models.
        cleaned = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)

        # Extract the first JSON object if extra text remains.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return cleaned[start:end + 1].strip()

        return cleaned.strip()

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

    def _log_debug(self, event: str, payload: Dict[str, Any]) -> None:
        log_path = os.getenv("RERANK_DEBUG_LOG")
        if not log_path:
            return
        record = {"ts": datetime.utcnow().isoformat() + "Z", "event": event}
        record.update(payload)
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            pass

    @staticmethod
    def _truncate(text: str, limit: int = 2000) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "...(truncated)"
