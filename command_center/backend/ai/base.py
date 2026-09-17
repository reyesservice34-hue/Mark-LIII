"""
Normalised chat contract every provider implements.

Messages:  {"role": "user"|"assistant", "content": [block, ...]}
Blocks:    {"type": "text", "text": str}
           {"type": "image", "media_type": str, "data": base64}
           {"type": "tool_use", "id": str, "name": str, "input": dict}      (assistant)
           {"type": "tool_result", "tool_use_id": str, "content": str,
            "is_error": bool}                                            (user)

Stream events yielded by `LLMProvider.stream()`:
           {"type": "text_delta", "text": str}
           {"type": "tool_use", "id": str, "name": str, "input": dict}
           {"type": "message_end", "stop_reason": str, "content": [blocks],
            "usage": {"input_tokens": int, "output_tokens": int}}
           {"type": "error", "message": str, "retryable": bool}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ProviderInfo:
    id: str
    model: str
    label: str

    def public(self) -> dict:
        return {"id": self.id, "model": self.model, "label": self.label}


class LLMProvider(Protocol):
    info: ProviderInfo

    def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
               max_tokens: int = 16000) -> AsyncIterator[dict]: ...

    async def health(self) -> dict: ...


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def text_of(blocks: list[dict]) -> str:
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def trim(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 20] + "\n…[truncated]…"
