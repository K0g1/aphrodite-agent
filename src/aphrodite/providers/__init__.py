"""OpenAI-compatible provider client."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx

from ..config import ProviderInstanceConfig


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class Provider:
    """OpenAI-compatible chat completion client."""

    def __init__(self, config: ProviderInstanceConfig, name: str = "default"):
        self.config = config
        self.name = name
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout_seconds),
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def health_check(self) -> bool:
        """Check if the provider is reachable."""
        try:
            client = await self.get_client()
            resp = await client.get("/models")
            return resp.status_code == 200
        except Exception:
            return False

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> str:
        """Send a chat completion request and return the response text."""
        client = await self.get_client()
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "top_p": self.config.top_p,
            "max_tokens": max_tokens or self.config.max_output_tokens,
            "stream": stream,
        }

        try:
            if stream:
                return await self._stream_completion(client, payload)
            else:
                resp = await client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # Strip thinking tags if present
                content = self._strip_thinking(content)
                return content.strip()
        except httpx.HTTPStatusError as e:
            raise ProviderError(
                f"Provider error {e.response.status_code}: {e.response.text[:200]}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            raise ProviderError(f"Provider request failed: {e}")

    async def _stream_completion(self, client: httpx.AsyncClient, payload: dict) -> str:
        """Stream a completion and return the full text."""
        chunks = []
        thinking_chunks = []
        
        async with client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        chunks.append(content)
                except (json.JSONDecodeError, KeyError):
                    continue

        full_text = "".join(chunks)
        return self._strip_thinking(full_text).strip()

    def _strip_thinking(self, text: str) -> str:
        """Remove thinking tags from model output."""
        import re
        # Remove <think>...</think> blocks
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        # Handle incomplete thinking
        text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
        return text

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
