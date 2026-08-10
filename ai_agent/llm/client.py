import json
import ssl
import uuid
from typing import Any

import httpx

from ai_agent.config import get_config

_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def _resolve_verify(verify_ssl: object) -> bool | str | ssl.SSLContext:
    """Resolve verify_ssl to a value httpx understands."""
    if isinstance(verify_ssl, str):
        verify_ssl = verify_ssl.lower() not in ("false", "0", "no")
    if verify_ssl is True:
        return True
    if verify_ssl is False:
        # Use CA bundle instead of bare False — avoids httpx SSL context
        # caching issues with RunPod proxy certificates
        if _CA_BUNDLE:
            return _CA_BUNDLE
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return verify_ssl


class LLMClient:
    """OpenAI-compatible API client for the agent."""

    def __init__(self):
        config = get_config()
        self.base_url = config.AI_API_BASE.rstrip("/")
        self.api_key = config.AI_API_KEY
        self.model = config.AI_MODEL
        verify_ssl = getattr(config, "AI_API_VERIFY_SSL", True)
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
            verify=_resolve_verify(verify_ssl),
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
        message = data["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content", "")
        return content.strip()

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
