"""
Ausfallkette: mehrere OpenAI-kompatible Zugaenge hintereinander (z. B. Groq -> Together -> DeepSeek).
Antwortet ein Zugang mit einem Fehler, BEVOR er etwas geliefert hat (Limit, Guthaben leer, Ueberlast, zu grosse
Anfrage), kommt der naechste dran. Ein abgelehnter Zugang wird kurz uebersprungen, damit nicht jede Nachricht erst
gegen dieselbe Wand laeuft.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import AsyncIterator

from .base import ProviderInfo, ToolDef

_HTTP = re.compile(r"HTTP (\d{3})")
USAGE_FILE = os.environ.get("JARVIS_CHAIN_USAGE_FILE", "/data/chain_usage.json")
# Preis in USD je Million Tokens (Eingabe, Ausgabe); ueberschreibbar mit JARVIS_<NAME>_PRICE_IN / _OUT
_PRICES = {"groq": (0.0, 0.0), "together": (0.14, 0.28), "deepseek": (0.14, 0.28)}


def _price(name: str) -> tuple[float, float]:
    pin, pout = _PRICES.get(name, (0.0, 0.0))
    try:
        pin = float(os.environ.get(f"JARVIS_{name.upper()}_PRICE_IN", pin))
        pout = float(os.environ.get(f"JARVIS_{name.upper()}_PRICE_OUT", pout))
    except ValueError:
        pass
    return pin, pout


def _load() -> dict:
    try:
        return json.load(open(USAGE_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    try:
        tmp = USAGE_FILE + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, USAGE_FILE)
    except OSError:
        pass


class ChainProvider:
    def __init__(self, providers: list, labels: list[str]):
        self.providers = providers
        self.labels = labels
        self._skip_until = [0.0] * len(providers)
        self._too_big = [float("inf")] * len(providers)
        self._rejected: dict[int, dict] = {}
        self.info = ProviderInfo(id="chain", model=providers[0].info.model,
                                 label="Kette · " + " > ".join(labels))

    def _cooldown(self, message: str) -> float:
        m = _HTTP.search(message or "")
        code = int(m.group(1)) if m else 0
        if code in (401, 402, 403):
            return 900.0
        if code in (413, 429):
            return 300.0
        return 60.0

    async def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
                     max_tokens: int = 16000) -> AsyncIterator[dict]:
        last_err: dict | None = None
        now = time.monotonic()
        size = len(system) + len(repr(messages)) + sum(len(t.description) + len(repr(t.input_schema)) for t in tools)
        order = [i for i in range(len(self.providers)) if self._skip_until[i] <= now and size < self._too_big[i]]
        order += [i for i in range(len(self.providers)) if i not in order and size < self._too_big[i]]
        order += [i for i in range(len(self.providers)) if i not in order]  # zuletzt auch die uebrigen
        for i in order:
            gen = self.providers[i].stream(system=system, messages=messages, tools=tools, max_tokens=max_tokens)
            try:
                first = await gen.__anext__()
            except StopAsyncIteration:
                continue
            if first.get("type") == "error":
                msg = str(first.get("message", ""))
                mm = _HTTP.search(msg)
                if mm:
                    self._rejected[i] = {"code": int(mm.group(1)), "at": time.time()}
                last_err = {**first, "message": self.labels[i] + ": " + msg}
                if _HTTP.search(msg) and _HTTP.search(msg).group(1) == "413":
                    self._too_big[i] = min(self._too_big[i], size)  # nur zu gross, nicht gesperrt
                else:
                    self._skip_until[i] = time.monotonic() + self._cooldown(first.get("message", ""))
                await gen.aclose()  # type: ignore[attr-defined]
                continue
            self.info = ProviderInfo(id="chain", model=self.providers[i].info.model,
                                     label=f"Kette · {self.labels[i]} · {self.providers[i].info.model}")
            self._rejected.pop(i, None)
            yield first
            async for ev in gen:
                if ev.get("type") == "message_end":
                    u = ev.get("usage") or {}
                    if not u.get("input_tokens"):
                        u = {**u, "input_tokens": int(size / 3.5)}  # Schaetzung, falls der Zugang nichts meldet
                    self._record(i, u)
                yield ev
            return
        yield last_err or {"type": "error", "retryable": False, "message": "Kein Zugang der Kette hat geantwortet."}

    async def health(self) -> dict:
        details = []
        best = "offline"
        for p, label in zip(self.providers, self.labels):
            h = await p.health()
            details.append(label + ": " + str(h.get("status")))
            if h.get("status") == "healthy":
                best = "healthy"
            elif h.get("status") == "degraded" and best != "healthy":
                best = "degraded"
        return {"status": best, "detail": " | ".join(details)}

    async def tool_check(self) -> tuple[bool, str]:
        return await self.providers[0].tool_check()

    def _record(self, i: int, usage: dict) -> None:
        name = self.providers[i].info.id
        pin, pout = _price(name)
        tin, tout = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
        d = _load()
        e = d.setdefault(name, {})
        if "credit_usd" not in e and os.environ.get(f"JARVIS_{name.upper()}_CREDIT_USD"):
            try:
                e["credit_usd"] = float(os.environ[f"JARVIS_{name.upper()}_CREDIT_USD"])
            except ValueError:
                pass
        e["in_tokens"] = e.get("in_tokens", 0) + tin
        e["out_tokens"] = e.get("out_tokens", 0) + tout
        e["spent_usd"] = e.get("spent_usd", 0.0) + tin * pin / 1e6 + tout * pout / 1e6
        e["requests"] = e.get("requests", 0) + 1
        _save(d)

    def set_credit(self, name: str, usd: float) -> None:
        d = _load()
        d[name] = {**d.get(name, {}), "credit_usd": float(usd), "spent_usd": 0.0, "set_at": time.time()}
        _save(d)

    def budget(self) -> list[dict]:
        d = _load()
        out = []
        for i, (p, label) in enumerate(zip(self.providers, self.labels)):
            name = p.info.id
            e = d.get(name, {})
            credit = e.get("credit_usd")
            if credit is None and os.environ.get(f"JARVIS_{name.upper()}_CREDIT_USD"):
                try:
                    credit = float(os.environ[f"JARVIS_{name.upper()}_CREDIT_USD"])
                except ValueError:
                    credit = None
            spent = float(e.get("spent_usd", 0.0))
            out.append({"id": name, "label": label, "spent_usd": round(spent, 4),
                        "credit_usd": credit, "remaining_usd": None if credit is None else round(credit - spent, 4),
                        "requests": e.get("requests", 0), "rejected": self._rejected.get(i)})
        return out
