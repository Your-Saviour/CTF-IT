import json
import ssl
import uuid
from typing import Any

import httpx
from openai import AsyncOpenAI

from ai_agent.config import get_config


class LLMClient:
    """OpenAI-compatible API client for the agent."""

    def __init__(self):
        config = get_config()
        base_url = config.AI_API_BASE.rstrip("/") + "/"
        self.api_key = config.AI_API_KEY
        self.model = config.AI_MODEL
        verify_ssl = getattr(config, "AI_API_VERIFY_SSL", True)
        if isinstance(verify_ssl, str):
            verify_ssl = verify_ssl.lower() not in ("false", "0", "no")

        if verify_ssl is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=base_url,
                http_client=httpx.AsyncClient(
                    timeout=120.0,
                    verify=ctx,
                    trust_env=False,
                ),
            )
        else:
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=base_url,
                http_client=httpx.AsyncClient(
                    timeout=120.0,
                    verify=True,
                    trust_env=False,
                ),
            )

    async def close(self):
        await self.client.http_client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        resp = await self.client.chat.completions.create(**kwargs)
        message = resp.choices[0].message
        content = message.content or getattr(message, "reasoning_content", "") or ""
        content = content.strip()
        # Strip markdown code blocks that some models wrap responses in
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove the opening ```[language] line and closing ``` line
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            content = content.strip()
        return content

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
