"""
Tool registry — the standardised interface every agent uses to act.

A tool is metadata + an async handler. The registry never executes anything
itself; `runtime.ToolExecutor` does, so that permissions, the approval gate
and the audit trail sit in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..ai.base import ToolDef
from ..auth import ROLE_RANK
from ..db import Database, dumps, now_iso

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import AppState
    from ..auth import Principal

RISK_LEVELS = ("low", "medium", "high", "critical")


@dataclass
class ToolContext:
    state: "AppState"
    principal: "Principal"
    agent_id: str
    run_id: str | None = None
    task_id: str | None = None
    conversation_id: str | None = None
    depth: int = 0
    emit: Callable[[str, dict], None] = lambda kind, data: None
    # Ein Werkzeug, das ein Bild besorgt — ein Bildschirmfoto des PCs, eine
    # Seite im Browser —, gibt hier den Pfad im Arbeitsbereich ab. Die
    # Agentenschleife hängt es an den nächsten Zug, damit das Modell es
    # WIRKLICH sieht. Ein Pfad im Text allein nützt ihm nichts: Er kann ihn
    # lesen, aber nicht ansehen.
    attach: Callable[[str], None] = lambda path: None


Handler = Callable[[ToolContext, dict], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] = field(default_factory=dict)
    category: str = "general"
    risk: str = "low"
    min_role: str = "operator"
    handler: Handler | None = None
    available: bool = True
    reason: str = ""
    source: str = "builtin"
    requires_approval: bool | None = None      # None → derived from risk
    timeout_seconds: float = 120.0
    calls: int = 0
    errors: int = 0

    def needs_approval(self, threshold: str = "high") -> bool:
        if self.requires_approval is not None:
            return self.requires_approval
        return RISK_LEVELS.index(self.risk) >= RISK_LEVELS.index(threshold)

    def to_def(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, input_schema=self.input_schema)

    def public(self) -> dict:
        return {
            "name": self.name, "description": self.description, "category": self.category,
            "risk": self.risk, "permissions": [self.min_role], "input_schema": self.input_schema,
            "output_schema": self.output_schema, "available": self.available and self.handler is not None,
            "reason": self.reason, "source": self.source, "requires_approval": self.needs_approval(),
            "calls": self.calls, "errors": self.errors,
        }


class ToolRegistry:
    def __init__(self, approval_threshold: str = "high"):
        self._tools: dict[str, ToolSpec] = {}
        self.approval_threshold = approval_threshold

    def register(self, spec: ToolSpec, replace: bool = False) -> ToolSpec:
        if spec.name in self._tools and not replace:
            raise ValueError(f"tool '{spec.name}' already registered")
        if spec.risk not in RISK_LEVELS:
            spec.risk = "low"
        self._tools[spec.name] = spec
        return spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: (t.category, t.name))

    def available(self) -> list[ToolSpec]:
        return [t for t in self.all() if t.available and t.handler is not None]

    def for_agent(self, allowed: list[str], role: str) -> list[ToolSpec]:
        """Tools an agent may offer to the model: allowed by its roster entry,
        available at runtime, and within the invoking user's role."""
        rank = ROLE_RANK.get(role, 0)
        out = []
        for t in self.available():
            if not _matches(t.name, allowed):
                continue
            if ROLE_RANK.get(t.min_role, 99) > rank:
                continue
            out.append(t)
        return out

    def record(self, name: str, ok: bool) -> None:
        t = self._tools.get(name)
        if t:
            t.calls += 1
            if not ok:
                t.errors += 1

    def categories(self) -> list[str]:
        return sorted({t.category for t in self._tools.values()})

    def snapshot(self, db: Database) -> None:
        for t in self._tools.values():
            db.upsert("tools", {
                "id": t.name, "description": t.description, "category": t.category,
                "permissions": dumps([t.min_role]), "input_schema": dumps(t.input_schema),
                "output_schema": dumps(t.output_schema), "risk": t.risk,
                "available": 1 if (t.available and t.handler) else 0, "reason": t.reason,
                "source": t.source, "updated_at": now_iso(), "calls": t.calls, "errors": t.errors,
            })


def _matches(name: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p == "*" or p == name:
            return True
        if p.endswith("*") and name.startswith(p[:-1]):
            return True
    return False
