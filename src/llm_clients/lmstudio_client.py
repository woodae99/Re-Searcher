"""LM Studio chat-completions client."""

from typing import Any, Dict, Optional
import os

from openai import OpenAI


class LMStudioClient:
    """Client for LM Studio's OpenAI-compatible chat API."""

    def __init__(self, config: Dict[str, Any]):
        llm_config = config.get("retrieval", {}).get("rerank", {}).get("llm", {})

        embedding_config = config.get("embedding", {})
        lmstudio_config = embedding_config.get("lmstudio", {})

        base_url = lmstudio_config.get("base_url") or embedding_config.get("endpoint")
        timeout = lmstudio_config.get("timeout_seconds") or embedding_config.get("timeout") or 60
        api_key = lmstudio_config.get("api_key") or embedding_config.get("api_key")
        # Allow ${ENV_VAR} style indirection
        if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
            api_key = os.getenv(api_key[2:-1])

        if not base_url:
            base_url = "http://localhost:1234/v1"

        client_kwargs: Dict[str, Any] = {"base_url": base_url, "timeout": timeout}
        if api_key:
            client_kwargs["api_key"] = api_key

        self.client = OpenAI(**client_kwargs)
        self.default_model = llm_config.get("model")

    def chat_completion(
        self,
        system_prompt: str,
        user_message: str,
        model: Optional[str] = None,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> str:
        response = self.client.chat.completions.create(
            model=model or self.default_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content
