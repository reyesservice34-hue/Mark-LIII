"""Google Gemini provider via the REST generateContent SSE stream."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import ProviderInfo, ToolDef

_BASE = "https://generativelanguage.googleapis.com/v1beta"
_DROP_KEYS = {"additionalProperties", "$schema", "default", "examples", "title"}


def _gemini_schema(schema: dict) -> dict:
    """Gemini accepts an OpenAPI subset with upper-case types."""
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for k, v in schema.items():
        if k in _DROP_KEYS:
            continue
        if k == "type" and isinstance(v, str):
            out[k] = v.upper()
        elif k == "properties" and isinstance(v, dict):
            out[k] = {pk: _gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _gemini_schema(v)
        else:
            out[k] = v
    return out


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        self.api_key = api_key
        self.info = ProviderInfo(id="gemini", model=model, label=f"Gemini · {model}")

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out = []
        tool_names: dict[str, str] = {}
        for m in messages:
            parts: list[dict] = []
            for b in m["content"]:
                t = b.get("type")
                if t == "text" and b.get("text"):
                    parts.append({"text": b["text"]})
                elif t == "image":
                    parts.append({"inline_data": {"mime_type": b["media_type"], "data": b["data"]}})
                elif t == "tool_use":
                    tool_names[b["id"]] = b["name"]
                    parts.append({"functionCall": {"name": b["name"], "args": b.get("input") or {}}})
                elif t == "tool_result":
                    parts.append({"functionResponse": {
                        "name": tool_names.get(b["tool_use_id"], "tool"),
                        "response": {"result": b.get("content") or "", "error": bool(b.get("is_error"))}}})
            if parts:
                out.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
        return out

    async def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
                     max_tokens: int = 16000) -> AsyncIterator[dict]:
        body: dict = {"contents": self._convert(messages),
                      "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": _gemini_schema(t.input_schema)}
                for t in tools]}]
        url = f"{_BASE}/models/{self.info.model}:streamGenerateContent?alt=sse"
        text_parts: list[str] = []
        calls: list[dict] = []
        finish = ""
        usage: dict = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
                async with client.stream("POST", url, headers={"x-goog-api-key": self.api_key},
                                         json=body) as resp:
                    if resp.status_code >= 400:
                        raw = (await resp.aread()).decode("utf-8", "replace")[:400]
                        yield {"type": "error", "retryable": resp.status_code >= 500,
                               "message": f"Gemini returned HTTP {resp.status_code}: {raw}"}
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            chunk = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        meta = chunk.get("usageMetadata")
                        if meta:
                            usage = {"input_tokens": meta.get("promptTokenCount", 0),
                                     "output_tokens": meta.get("candidatesTokenCount", 0)}
                        for cand in chunk.get("candidates", []):
                            if cand.get("finishReason"):
                                finish = cand["finishReason"]
                            for part in (cand.get("content") or {}).get("parts", []):
                                if part.get("text"):
                                    text_parts.append(part["text"])
                                    yield {"type": "text_delta", "text": part["text"]}
                                if part.get("functionCall"):
                                    fc = part["functionCall"]
                                    calls.append({"id": f"call_{len(calls) + 1}", "name": fc.get("name", ""),
                                                  "input": fc.get("args") or {}})
        except httpx.HTTPError as e:
            yield {"type": "error", "message": f"Cannot reach Gemini: {e}", "retryable": True}
            return

        content: list[dict] = []
        if text_parts:
            content.append({"type": "text", "text": "".join(text_parts)})
        for c in calls:
            content.append({"type": "tool_use", **c})
            yield {"type": "tool_use", **c}
        if finish == "SAFETY":
            yield {"type": "error", "message": "Gemini blocked the response (safety)", "retryable": False}
            return
        stop = "tool_use" if calls else ("max_tokens" if finish == "MAX_TOKENS" else "end_turn")
        yield {"type": "message_end", "stop_reason": stop, "content": content, "usage": usage}

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{_BASE}/models/{self.info.model}",
                                     headers={"x-goog-api-key": self.api_key})
            if r.status_code in (401, 403):
                return {"status": "offline", "detail": "API key rejected"}
            if r.status_code == 404:
                return {"status": "degraded", "detail": f"model {self.info.model} not found"}
            if r.status_code >= 400:
                return {"status": "degraded", "detail": f"HTTP {r.status_code}"}
            return {"status": "healthy", "detail": f"model {self.info.model} available"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach Gemini: {e.__class__.__name__}"}
