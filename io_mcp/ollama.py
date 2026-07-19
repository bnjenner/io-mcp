"""Thin async wrapper around the Ollama HTTP API. No LangChain."""

from __future__ import annotations

import json
from typing import Any

import httpx

DEFAULT_TIMEOUT = 120.0


class OllamaError(RuntimeError):
    """Raised when Ollama returns an error or an unparseable response."""


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "mistral:7b",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        format: str | None = None,
    ) -> str:
        """POST /api/generate — returns the full response text."""
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if format:
            payload["format"] = format
        data = await self._post("/api/generate", payload)
        return data.get("response", "")

    async def chat(self, messages: list[dict], model: str | None = None) -> str:
        """POST /api/chat — returns the assistant message content."""
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": False,
        }
        data = await self._post("/api/chat", payload)
        return (data.get("message") or {}).get("content", "")

    async def generate_json(
        self, prompt: str, model: str | None = None, system: str | None = None
    ) -> dict:
        """Like generate(), but sets format='json' and parses the response."""
        text = await self.generate(prompt, model=model, system=system, format="json")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OllamaError(
                f"Ollama did not return valid JSON: {text[:200]!r}"
            ) from exc

    # ----------------------------------------------------------------------- #
    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request to {url} failed: {exc}") from exc
