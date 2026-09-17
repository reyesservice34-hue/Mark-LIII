"""Anthropic provider — official SDK, streaming, tool use."""
from __future__ import annotations

from typing import AsyncIterator

from .base import ProviderInfo, ToolDef


class AnthropicProvider:
    def __init__(self, api_key: str, model: str = "claude-opus-5"):
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        self.info = ProviderInfo(id="anthropic", model=model, label=f"Anthropic · {model}")

    @staticmethod
    def _convert_messages(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            blocks = []
            for b in m["content"]:
                t = b.get("type")
                if t == "text":
                    if b.get("text"):
                        blocks.append({"type": "text", "text": b["text"]})
                elif t == "image":
                    blocks.append({"type": "image", "source": {
                        "type": "base64", "media_type": b["media_type"], "data": b["data"]}})
                elif t == "tool_use":
                    blocks.append({"type": "tool_use", "id": b["id"], "name": b["name"],
                                   "input": b.get("input") or {}})
                elif t == "tool_result":
                    blocks.append({"type": "tool_result", "tool_use_id": b["tool_use_id"],
                                   "content": b.get("content") or "", "is_error": bool(b.get("is_error"))})
            if blocks:
                out.append({"role": m["role"], "content": blocks})
        return out

    async def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
                     max_tokens: int = 16000) -> AsyncIterator[dict]:
        anthropic = self._anthropic
        kwargs: dict = {
            "model": self.info.model,
            "max_tokens": max_tokens,
            "messages": self._convert_messages(messages),
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description,
                                "input_schema": t.input_schema} for t in tools]
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield {"type": "text_delta", "text": event.text}
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            yield {"type": "error", "message": f"Anthropic rejected the API key: {e.message}", "retryable": False}
            return
        except anthropic.RateLimitError as e:
            yield {"type": "error", "message": f"Anthropic rate limit: {e.message}", "retryable": True}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "message": f"Anthropic API error {e.status_code}: {e.message}",
                   "retryable": e.status_code >= 500}
            return
        except anthropic.APIConnectionError as e:
            yield {"type": "error", "message": f"Cannot reach Anthropic: {e}", "retryable": True}
            return

        content: list[dict] = []
        for block in final.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name,
                                "input": dict(block.input or {})})
        stop = final.stop_reason or "end_turn"
        if stop == "refusal":
            detail = ""
            sd = getattr(final, "stop_details", None)
            if sd is not None:
                detail = f" ({getattr(sd, 'category', '') or ''} {getattr(sd, 'explanation', '') or ''})".rstrip()
            yield {"type": "error", "message": "The model declined this request" + detail, "retryable": False}
            return
        if stop == "max_tokens" and any(b["type"] == "tool_use" for b in content):
            yield {"type": "error", "message": "Tool input was cut off by the token limit", "retryable": False}
            return
        for b in content:
            if b["type"] == "tool_use":
                yield {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]}
        usage = {"input_tokens": getattr(final.usage, "input_tokens", 0),
                 "output_tokens": getattr(final.usage, "output_tokens", 0)}
        yield {"type": "message_end", "stop_reason": stop, "content": content, "usage": usage}

    async def health(self) -> dict:
        try:
            await self._client.with_options(timeout=8.0, max_retries=0).models.retrieve(self.info.model)
            return {"status": "healthy", "detail": f"model {self.info.model} available"}
        except self._anthropic.AuthenticationError:
            return {"status": "offline", "detail": "API key rejected"}
        except self._anthropic.NotFoundError:
            return {"status": "degraded", "detail": f"model {self.info.model} not found"}
        except self._anthropic.APIConnectionError:
            return {"status": "offline", "detail": "cannot reach api.anthropic.com"}
        except Exception as e:  # noqa: BLE001
            return {"status": "degraded", "detail": str(e)[:200]}
