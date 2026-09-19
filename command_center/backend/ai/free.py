"""
Kostenlos ist die Regel. Solange JARVIS_ALLOW_PAID nicht auf 1 steht, gehen alle Modellanfragen an Modelle
mit Endung „:free“. Ist eines ausgelastet (Limit, Überlast), kommt das nächste der Kette dran.
Bezahlte Modelle (Claude Sonnet 5, Haiku) laufen nur, wenn der Master das ausdrücklich einschaltet.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from .base import ProviderInfo, ToolDef

# Reihenfolge = Vorzug. Alle können Werkzeuge aufrufen und verstehen Deutsch.
# Geprüft (Antwort direkt, Werkzeugaufruf klappt, schnell). Modelle, die ihr Nachdenken laut mitsprechen
# (z. B. nemotron), sind bewusst nicht dabei: Jarvis würde es vorlesen.
FREE_FAST = ["nex-agi/nex-n2.5-mini:free", "nex-agi/nex-n2.5-pro:free", "google/gemma-4-26b-a4b-it:free",
             "google/gemma-4-31b-it:free"]
FREE_DEEP = ["nex-agi/nex-n2.5-pro:free", "deepseek/deepseek-v4-flash-0731:free", "google/gemma-4-31b-it:free",
             "poolside/laguna-s-2.1:free", "nex-agi/nex-n2.5-mini:free"]


def allow_paid() -> bool:
    return os.environ.get("JARVIS_ALLOW_PAID", "") == "1"


def free_or(model: str, chain: list[str] | None = None) -> str:
    """Bezahltes Modell nur, wenn erlaubt; sonst das erste kostenlose der Kette."""
    if allow_paid() or model.endswith(":free"):
        return model
    return (chain or FREE_FAST)[0]


class FreeChain:
    """Mehrere kostenlose Modelle hinter einem Zugang; fällt eines aus, bevor es etwas geliefert hat, das nächste."""

    def __init__(self, base, models: list[str], label: str):
        from .openai_compat import OpenAICompatProvider
        self.providers = [OpenAICompatProvider(base.info.id, base.base_url, base.api_key, m) for m in models]
        self.info = ProviderInfo(id=base.info.id, model=models[0], label=f"{label} · {models[0]}")
        self.base_url, self.api_key = base.base_url, base.api_key

    async def stream(self, *, system: str, messages: list[dict], tools: list[ToolDef],
                     max_tokens: int = 16000) -> AsyncIterator[dict]:
        last: Exception | None = None
        for i, p in enumerate(self.providers):
            started = False
            try:
                async for ev in p.stream(system=system, messages=messages, tools=tools, max_tokens=max_tokens):
                    # Der Zugang meldet Limit oder Überlast als error-Ereignis, nicht als Ausnahme.
                    if ev.get("type") == "error" and not started:
                        last = ev
                        break
                    started = True
                    yield ev
                else:
                    return
            except Exception as e:  # noqa: BLE001
                last = e
                if started:
                    raise
        if isinstance(last, dict):
            yield last
        elif last:
            raise last

    async def health(self) -> dict:
        return await self.providers[0].health()
