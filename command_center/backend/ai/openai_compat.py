"""
OpenAI-compatible chat-completions provider.

Covers OpenAI itself and every server that mirrors its API: LM Studio, Ollama
(/v1), vLLM, LocalAI, Jan, llama.cpp. Streaming via SSE, tools via
`tools=[{type: function}]`.
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from .base import ProviderInfo, ToolDef, ToolNameMap


class OpenAICompatProvider:
    def __init__(self, provider_id: str, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.api_key = api_key
        # The read timeout is the wait between two chunks. A local CPU model sends nothing while it digests a
        # long cold prompt (minutes), so a local provider gets a much longer wait than a cloud API.
        self.read_timeout = (float(os.environ.get("LOCAL_LLM_TIMEOUT") or 1500) if provider_id == "local" else 300.0)
        self.info = ProviderInfo(id=provider_id, model=model,
                                 label=f"{'OpenAI' if provider_id == 'openai' else 'Local model'} · {model}")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (compatible; jarvis-cc/1.0)"}
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
        if getattr(self, "include_usage", False):
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": names.wire(t.name), "description": t.description,
                "parameters": t.input_schema}} for t in tools]
        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish = "stop"
        usage: dict = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.read_timeout, connect=15.0)) as client:
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
            yield {"type": "error", "message": f"Cannot reach {self.info.label}: {e or e.__class__.__name__}", "retryable": True}
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

    async def tool_check(self) -> tuple[bool, str]:
        """Beherrscht dieses Modell Werkzeugaufrufe? Einmal wirklich ausprobiert.

        Der Unterschied entscheidet alles: Ein Modell, das antwortet, aber keine
        Werkzeuge aufrufen kann, macht JARVIS zu einem Gesprächspartner ohne
        Hände. Er kann dann über den Kalender reden, aber keinen Termin anlegen
        — und das sieht von außen aus wie ein Fehler ganz woanders.

        Gerade bei lokalen Modellen ist das der Regelfall, nicht die Ausnahme:
        Viele kleine Modelle führen `tools` im Datenblatt und rufen trotzdem
        keines auf. Deshalb wird es probiert, nicht geglaubt.
        """
        probe = ToolDef(name="melde_zahl",
                        description="Melde die Zahl, nach der gefragt wird.",
                        input_schema={"type": "object",
                                      "properties": {"zahl": {"type": "integer"}},
                                      "required": ["zahl"]})
        body = {
            "model": self.info.model,
            "messages": [{"role": "user",
                          "content": "Rufe melde_zahl mit zahl=7 auf. Antworte sonst nichts."}],
            "tools": [{"type": "function", "function": {
                "name": probe.name, "description": probe.description,
                "parameters": probe.input_schema}}],
            "tool_choice": "auto", "max_tokens": 128, "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.post(f"{self.base_url}/chat/completions",
                                      headers=self._headers(), json=body)
        except httpx.HTTPError as e:
            return False, f"Werkzeugprobe nicht möglich: {e.__class__.__name__}"
        if r.status_code >= 400:
            # Manche Server lehnen `tools` rundheraus ab — auch das ist eine Antwort.
            return False, f"Der Server nimmt keine Werkzeuge an (HTTP {r.status_code})"
        try:
            nachricht = (r.json()["choices"][0]["message"]) or {}
        except (KeyError, IndexError, ValueError):
            return False, "Antwort auf die Werkzeugprobe war nicht lesbar"
        if nachricht.get("tool_calls"):
            return True, "ruft Werkzeuge auf"
        return False, (f"{self.info.model} antwortet, ruft aber kein Werkzeug auf. "
                       f"Damit kann JARVIS reden, aber nichts tun — kein Termin, keine Mail, "
                       f"kein Zugriff auf den PC. Ein Modell mit Werkzeugunterstützung wählen "
                       f"(z. B. qwen2.5 oder llama3.1 in einer Größe ab 7B).")

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
            if r.status_code == 401:
                return {"status": "offline", "detail": "API key rejected"}
            if r.status_code >= 400:
                return {"status": "degraded", "detail": f"HTTP {r.status_code} from /models"}
            j = r.json()
            items = j if isinstance(j, list) else (j.get("data") or [])
            ids = [m.get("id") for m in items]
            if ids and self.info.model not in ids and not any(
                    str(i).startswith(self.info.model) for i in ids):
                return {"status": "degraded", "detail": f"model {self.info.model} not listed by server"}
            return {"status": "healthy", "detail": f"{len(ids)} models listed"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach {self.base_url}: {e.__class__.__name__}"}
