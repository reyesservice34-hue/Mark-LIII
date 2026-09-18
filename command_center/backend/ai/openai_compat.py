"""
OpenAI-compatible chat-completions provider.

Covers OpenAI itself and every server that mirrors its API: LM Studio, Ollama
(/v1), vLLM, LocalAI, Jan, llama.cpp. Streaming via SSE, tools via
`tools=[{type: function}]`.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import ProviderInfo, ToolDef, ToolNameMap


class OpenAICompatProvider:
    def __init__(self, provider_id: str, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.api_key = api_key
        self.info = ProviderInfo(id=provider_id, model=model,
                                 label=f"{'OpenAI' if provider_id == 'openai' else 'Local model'} · {model}")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @staticmethod
    def _convert_messages(system: str, messages: list[dict],
                          wire=lambda n: n) -> list[dict]:
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": wire(b["name"]),
                                       "arguments": json.dumps(b.get("input") or {})}}
                         for b in m["content"] if b.get("type") == "tool_use"]
                msg: dict = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                parts: list[dict] = []
                for b in m["content"]:
                    t = b.get("type")
                    if t == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                    "content": b.get("content") or ""})
                    elif t == "text":
                        parts.append({"type": "text", "text": b.get("text", "")})
                    elif t == "image":
                        parts.append({"type": "image_url", "image_url": {
                            "url": f"data:{b['media_type']};base64,{b['data']}"}})
                if parts:
                    if len(parts) == 1 and parts[0]["type"] == "text":
                        out.append({"role": "user", "content": parts[0]["text"]})
                    else:
                        out.append({"role": "user", "content": parts})
        return out

    async def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
                     max_tokens: int = 16000) -> AsyncIterator[dict]:
        # OpenAI-compatible endpoints validate function names against
        # ^[a-zA-Z0-9_-]{1,64}$, and this registry's names are dotted.
        names = ToolNameMap((t.name for t in tools), limit=64)
        body: dict = {
            "model": self.info.model, "stream": True, "max_tokens": max_tokens,
            "messages": self._convert_messages(system, messages, names.wire),
        }
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": names.wire(t.name), "description": t.description,
                "parameters": t.input_schema}} for t in tools]
        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish = "stop"
        usage: dict = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions",
                                         headers=self._headers(), json=body) as resp:
                    if resp.status_code >= 400:
                        raw = (await resp.aread()).decode("utf-8", "replace")[:400]
                        yield {"type": "error", "retryable": resp.status_code >= 500,
                               "message": f"{self.info.label} returned HTTP {resp.status_code}: {raw}"}
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except ValueError:
                            continue
                        if chunk.get("usage"):
                            usage = {"input_tokens": chunk["usage"].get("prompt_tokens", 0),
                                     "output_tokens": chunk["usage"].get("completion_tokens", 0)}
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                text_parts.append(delta["content"])
                                yield {"type": "text_delta", "text": delta["content"]}
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["args"] += fn["arguments"]
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
        except httpx.HTTPError as e:
            yield {"type": "error", "message": f"Cannot reach {self.info.label}: {e}", "retryable": True}
            return

        content: list[dict] = []
        text = "".join(text_parts)
        if text:
            content.append({"type": "text", "text": text})
        for idx in sorted(calls):
            slot = calls[idx]
            try:
                args = json.loads(slot["args"] or "{}")
            except ValueError:
                args = {"_raw": slot["args"]}
            call_id = slot["id"] or f"call_{idx}"
            real = names.real(slot["name"])
            block = {"type": "tool_use", "id": call_id, "name": real, "input": args}
            content.append(block)
            yield {"type": "tool_use", "id": call_id, "name": real, "input": args}
        stop = "tool_use" if calls else ("max_tokens" if finish == "length" else "end_turn")
        yield {"type": "message_end", "stop_reason": stop, "content": content, "usage": usage}

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
            if r.status_code == 401:
                return {"status": "offline", "detail": "API key rejected"}
            if r.status_code >= 400:
                return {"status": "degraded", "detail": f"HTTP {r.status_code} from /models"}
            ids = [m.get("id") for m in (r.json().get("data") or [])]
            if ids and self.info.model not in ids and not any(
                    str(i).startswith(self.info.model) for i in ids):
                return {"status": "degraded", "detail": f"model {self.info.model} not listed by server"}
            return {"status": "healthy", "detail": f"{len(ids)} models listed"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach {self.base_url}: {e.__class__.__name__}"}
