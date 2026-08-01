"""OpenAI-compatible provider client."""

from __future__ import annotations

import asyncio
import json

import httpx

from ..config import ProviderInstanceConfig

RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


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
        """Check if the provider is reachable and actually serves a model list."""
        try:
            client = await self.get_client()
            resp = await client.get("/models")
            if resp.status_code != 200:
                return False
            data = resp.json()
            # A 200 with an error body (bad key/auth) is not healthy.
            return isinstance(data, dict) and isinstance(data.get("data"), list)
        except Exception:  # noqa: BLE001 - provider boundary: any failure = unhealthy
            return False

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> str:
        """Send a chat completion request and return the response text.

        Transient failures (429/502/503/504 and network errors) are retried
        with bounded exponential backoff; streaming responses are not retried
        because partial output cannot be replayed safely.
        """
        client = await self.get_client()

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_output_tokens if max_tokens is None else max_tokens,
            "stream": stream,
        }

        if stream:
            try:
                return await self._stream_completion(client, payload)
            except ProviderError:
                raise
            except httpx.HTTPStatusError as e:
                raise ProviderError(
                    f"Provider error {e.response.status_code}: {e.response.text[:200]}",
                    status_code=e.response.status_code,
                ) from e
            except httpx.TransportError as e:
                raise ProviderError(f"Provider request failed: network error: {e}") from e
            except Exception as e:
                raise ProviderError(f"Provider request failed: {e}") from e

        attempts = 0
        while True:
            try:
                resp = await client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # Strip thinking tags if present
                content = self._strip_thinking(content)
                return content.strip()
            except httpx.HTTPStatusError as e:
                if (
                    e.response.status_code in RETRYABLE_STATUS_CODES
                    and attempts < self.config.retries
                ):
                    attempts += 1
                    await asyncio.sleep(0.5 * (2 ** (attempts - 1)))
                    continue
                raise ProviderError(
                    f"Provider error {e.response.status_code}: {e.response.text[:200]}",
                    status_code=e.response.status_code,
                )
            except httpx.TransportError:
                if attempts < self.config.retries:
                    attempts += 1
                    await asyncio.sleep(0.5 * (2 ** (attempts - 1)))
                    continue
                raise ProviderError("Provider request failed: network error")
            except ProviderError:
                raise
            except Exception as e:  # noqa: BLE001 - boundary: convert to ProviderError
                raise ProviderError(f"Provider request failed: {e}")

    async def _stream_completion(self, client: httpx.AsyncClient, payload: dict) -> str:
        """Stream a completion and return the full text."""
        chunks = []

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
                except json.JSONDecodeError:
                    continue
                if "error" in data:
                    err = data["error"]
                    detail = (
                        err.get("message", "stream error") if isinstance(err, dict) else str(err)
                    )
                    raise ProviderError(f"Provider stream error: {detail}")
                try:
                    delta = data["choices"][0].get("delta", {})
                except (KeyError, IndexError, TypeError):
                    continue
                content = delta.get("content", "")
                if content:
                    chunks.append(content)

        full_text = "".join(chunks)
        return self._strip_thinking(full_text).strip()

    def _strip_thinking(self, text: str) -> str:
        """Remove thinking tags from model output."""
        import re

        # Remove complete <think>/<thinking> blocks (any casing/spacing).
        text = re.sub(
            r"<\s*thinking\s*>.*?</\s*thinking\s*>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(r"<\s*think\s*>.*?</\s*think\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Handle incomplete/dangling thinking blocks.
        text = re.sub(r"<\s*thinking\s*>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<\s*think\s*>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
        return text

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
