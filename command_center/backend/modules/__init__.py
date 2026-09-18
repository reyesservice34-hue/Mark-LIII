"""
Module registry — the backend half of "new modules register themselves".

A module is a package under `modules/` exposing `MODULE: ModuleSpec`. The app
factory imports every enabled module, mounts its router under /api/<prefix>
and publishes the navigation entries through GET /api/modules, so the frontend
sidebar and command palette are generated from what the server actually
runs. A future module (say `modules/crm/`) needs a `MODULE` spec and a router;
nothing in the shell changes.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Callable

from fastapi import APIRouter

DEFAULT_MODULES = [
    "auth", "health", "events", "chat", "agents", "tasks", "teach", "workflows", "automations",
    "server", "desktop", "files", "integrations", "logs", "notifications", "approvals", "analytics",
    "settings", "gateway", "voice", "live", "extensions",
]


@dataclass
class ModuleSpec:
    id: str
    title: str
    router: APIRouter
    icon: str = "box"
    path: str = ""                 # frontend route ("" → not in navigation)
    order: int = 100
    nav: bool = True
    mobile_priority: int = 0       # >0 → shown in the mobile tab bar, higher first
    min_role: str = "viewer"
    description: str = ""
    commands: list[dict] = field(default_factory=list)   # command palette entries
    on_startup: Callable | None = None                   # (state) -> None | awaitable
    on_shutdown: Callable | None = None

    def nav_entry(self) -> dict:
        return {"id": self.id, "title": self.title, "icon": self.icon, "path": self.path,
                "order": self.order, "mobile_priority": self.mobile_priority,
                "min_role": self.min_role, "description": self.description,
                "commands": self.commands}


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ModuleSpec] = {}

    def register(self, spec: ModuleSpec) -> None:
        if spec.id in self._modules:
            raise ValueError(f"module '{spec.id}' already registered")
        self._modules[spec.id] = spec

    def load(self, names: list[str]) -> list[ModuleSpec]:
        loaded = []
        for name in names:
            mod = importlib.import_module(f"{__name__}.{name}")
            spec: ModuleSpec = getattr(mod, "MODULE")
            self.register(spec)
            loaded.append(spec)
        return loaded

    def all(self) -> list[ModuleSpec]:
        return sorted(self._modules.values(), key=lambda m: (m.order, m.id))

    def get(self, module_id: str) -> ModuleSpec | None:
        return self._modules.get(module_id)

    def navigation(self, role_rank: int) -> list[dict]:
        from ..auth import ROLE_RANK
        return [m.nav_entry() for m in self.all()
                if m.nav and m.path and ROLE_RANK.get(m.min_role, 99) <= role_rank]
