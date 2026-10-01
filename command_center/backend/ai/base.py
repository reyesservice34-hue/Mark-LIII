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

import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Protocol


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


# ── tool names on the wire ───────────────────────────────────────────────────
# This registry names tools by area: "calendar.read", "composio.run". Every
# major provider validates function names against roughly [a-zA-Z0-9_-] and
# rejects the dot, so each declaration would be refused. Renaming the registry
# would be the wrong fix: the dotted name is what the audit trail, the approval
# gate, the agent whitelists and the UI all show. So it is translated at the
# boundary and translated back, and nothing else in the system has to know.
_ILLEGAL_IN_TOOL_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def wire_name(name: str, limit: int = 128) -> str:
    return _ILLEGAL_IN_TOOL_NAME.sub("_", name)[:limit] or "tool"


class ToolNameMap:
    """Translates between the registry's names and what a provider accepts.

    A collision (two tools whose names differ only in the separator) would
    silently route a call to the wrong tool, so the second one is given a
    suffix rather than being allowed to overwrite the first.
    """

    def __init__(self, names: "Iterable[str]", limit: int = 128) -> None:
        self.to_wire: dict[str, str] = {}
        self.to_real: dict[str, str] = {}
        for name in names:
            w = wire_name(name, limit)
            if w in self.to_real and self.to_real[w] != name:
                n = 2
                while f"{w[:limit - 2]}_{n}" in self.to_real:
                    n += 1
                w = f"{w[:limit - 2]}_{n}"
            self.to_wire[name] = w
            self.to_real[w] = name

    def wire(self, name: str) -> str:
        return self.to_wire.get(name) or wire_name(name)

    def real(self, name: str) -> str:
        return self.to_real.get(name, name)
