import json
import uuid
from typing import Any

import httpx

from ai_agent.config import get_config


class LLMClient:
    """OpenAI-compatible API client for the agent."""

    def __init__(self):
        config = get_config()
        self.base_url = config.AI_API_BASE.rstrip("/")
        self.api_key = config.AI_API_KEY
        self.model = config.AI_MODEL
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    async def close(self):
        await self.client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: dict,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        content = await self.chat(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"error": f"Failed to parse JSON response: {content[:200]}"}

    def estimate_tokens(self, text: str) -> int:
        return len(text.encode("utf-8")) // 4
