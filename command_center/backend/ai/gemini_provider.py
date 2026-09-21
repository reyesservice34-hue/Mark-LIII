"""Google Gemini provider via the REST generateContent SSE stream."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import ProviderInfo, ToolDef, ToolNameMap

_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        self.api_key = api_key
        self.info = ProviderInfo(id="gemini", model=model, label=f"Gemini · {model}")

    @staticmethod
    def _convert(messages: list[dict], wire=lambda n: n) -> list[dict]:
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
                    tool_names[b["id"]] = wire(b["name"])
                    parts.append({"functionCall": {"name": wire(b["name"]), "args": b.get("input") or {}}})
                elif t == "tool_result":
                    parts.append({"functionResponse": {
                        "name": tool_names.get(b["tool_use_id"], "tool"),
                        "response": {"result": b.get("content") or "", "error": bool(b.get("is_error"))}}})
            if parts:
                out.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
        return out

    async def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
                     max_tokens: int = 16000) -> AsyncIterator[dict]:
        # Gemini rejects a dot in a function name just as the others do.
        names = ToolNameMap((t.name for t in tools), limit=64)
        body: dict = {"contents": self._convert(messages, names.wire),
                      "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            # parametersJsonSchema, not parameters: the latter validates against
            # Gemini's own Schema type, a strict OpenAPI subset that rejects any
            # real JSON-Schema-2020-12 keyword it doesn't know (const, $schema,
            # a numeric exclusiveMinimum, ...) — and one bad tool's schema took
            # every tool down with it, since the whole tools[] array is validated
            # together. parametersJsonSchema is untyped on Gemini's side and
            # passed straight through as JSON Schema (confirmed against the
            # actual field the official google-genai SDK sends on the wire for
            # its own parameters_json_schema, since this file talks REST
            # directly rather than through that SDK) — no local sanitizing needed.
            body["tools"] = [{"functionDeclarations": [
                {"name": names.wire(t.name), "description": t.description,
                 "parametersJsonSchema": t.input_schema}
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
                                    calls.append({"id": f"call_{len(calls) + 1}",
                                                  "name": names.real(fc.get("name", "")),
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
