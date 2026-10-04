"""
ask_openai — Direct OpenAI API integration for MIA.

Allows MIA to query OpenAI's models (GPT-4, GPT-4o, etc.) directly
without going through the command center's orchestration chain.

Uses the OPENAI_API_KEY from environment (.env).
"""

import os
import json
from typing import Optional, Any
import httpx
import asyncio


class OpenAIClient:
    """Direct OpenAI client wrapper."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY not found in environment. "
                "Set it in .env or pass it explicitly."
            )
        self.base_url = "https://api.openai.com/v1"
        self.model = "gpt-4o"
        self.timeout = httpx.Timeout(30.0, connect=10.0)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "MIA/1.0 (ask_openai)"
        }

    async def query(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        system: Optional[str] = None,
    ) -> dict[str, Any]:
        model = model or self.model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )

            if response.status_code >= 400:
                error_text = response.text[:500]
                return {
                    "success": False,
                    "model": model,
                    "error": f"OpenAI API error ({response.status_code}): {error_text}",
                    "content": None,
                    "tokens": 0,
                }

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return {
                "success": True,
                "model": model,
                "content": content,
                "tokens": {
                    "input": usage.get("prompt_tokens", 0),
                    "output": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                },
                "error": None,
            }

        except httpx.HTTPError as e:
            return {
                "success": False,
                "model": model,
                "error": f"Connection error: {str(e)}",
                "content": None,
                "tokens": 0,
            }
        except (KeyError, ValueError) as e:
            return {
                "success": False,
                "model": model,
                "error": f"Response parsing error: {str(e)}",
                "content": None,
                "tokens": 0,
            }


def get_client() -> OpenAIClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client


async def ask(
    prompt: str,
    model: str = "gpt-4o",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    system: Optional[str] = None,
) -> dict[str, Any]:
    client = get_client()
    return await client.query(
        prompt=prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        system=system,
    )


async def health() -> dict[str, Any]:
    client = get_client()
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            response = await c.get(
                "https://api.openai.com/v1/models",
                headers=get_client()._headers(),
            )
        if response.status_code == 401:
            return {"status": "offline", "detail": "API key rejected"}
        if response.status_code >= 400:
            return {"status": "degraded", "detail": f"HTTP {response.status_code}"}
        data = response.json()
        model_count = len(data.get("data", []))
        return {"status": "healthy", "detail": f"{model_count} models available"}
    except Exception as e:
        return {"status": "offline", "detail": f"Cannot reach OpenAI: {str(e)}"}


_client: Optional[OpenAIClient] = None
