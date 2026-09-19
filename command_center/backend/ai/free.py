"""
Kostenlos ist die Regel. Solange JARVIS_ALLOW_PAID nicht auf 1 steht, gehen alle Modellanfragen an Modelle
mit Endung „:free“. Ist eines ausgelastet (Limit, Überlast), kommt das nächste der Kette dran.
Bezahlte Modelle (Claude Sonnet 5, Haiku) laufen nur, wenn der Master das ausdrücklich einschaltet.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from .base import ProviderInfo, ToolDef

# Reihenfolge = Vorzug. Alle können Werkzeuge aufrufen und verstehen Deutsch.
# Geprüft (Antwort direkt, Werkzeugaufruf klappt, schnell). Modelle, die ihr Nachdenken laut mitsprechen
# (z. B. nemotron), sind bewusst nicht dabei: Jarvis würde es vorlesen.
FREE_FAST = ["nex-agi/nex-n2.5-mini:free", "nex-agi/nex-n2.5-pro:free", "google/gemma-4-26b-a4b-it:free",
             "google/gemma-4-31b-it:free"]
FREE_DEEP = ["nex-agi/nex-n2.5-pro:free", "deepseek/deepseek-v4-flash-0731:free", "google/gemma-4-31b-it:free",
             "poolside/laguna-s-2.1:free", "nex-agi/nex-n2.5-mini:free"]


# Wettlauf: nach so vielen Sekunden ohne erstes Wort startet das nächste Modell zusätzlich.
HEDGE = 1.0
FIRST_TOKEN_DEADLINE = 12.0


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
        """Wettlauf um das erste Wort: Antwortet das erste Modell nicht binnen HEDGE Sekunden, startet das nächste
        zusätzlich, und wer zuerst etwas liefert, gewinnt. Ein Modell, das nur einen Fehler meldet (Limit, Überlast),
        fällt sofort aus dem Rennen. So hängt ein überlastetes kostenloses Modell nie die ganze Antwort auf."""
        gens: dict[int, AsyncIterator[dict]] = {}
        pending: dict[asyncio.Task, int] = {}
        nxt = 0
        last_err: dict | None = None
        deadline = time.monotonic() + FIRST_TOKEN_DEADLINE

        def launch() -> bool:
            nonlocal nxt
            if nxt >= len(self.providers):
                return False
            g = self.providers[nxt].stream(system=system, messages=messages, tools=tools, max_tokens=max_tokens)
            gens[nxt] = g
            pending[asyncio.ensure_future(g.__anext__())] = nxt
            nxt += 1
            return True

        async def close_all(keep: int = -1) -> None:
            for t in list(pending):
                t.cancel()
            for k, g in gens.items():
                if k != keep:
                    try:
                        await g.aclose()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass

        launch()
        winner = -1
        first: dict | None = None
        try:
            while pending and winner < 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                done, _ = await asyncio.wait(pending.keys(), timeout=min(HEDGE, left),
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:                      # noch nichts: das nächste Modell zusätzlich starten
                    launch()
                    continue
                for t in done:
                    k = pending.pop(t)
                    try:
                        ev = t.result()
                    except StopAsyncIteration:
                        continue
                    except Exception as e:  # noqa: BLE001
                        last_err = {"type": "error", "retryable": False, "message": str(e)[:200]}
                        launch()
                        continue
                    if ev.get("type") == "error":
                        last_err = ev
                        launch()
                        continue
                    winner, first = k, ev
                    break
                if winner < 0 and not pending:
                    launch()
        finally:
            if winner >= 0:
                for t in list(pending):
                    t.cancel()
                for k, g in gens.items():
                    if k != winner:
                        try:
                            await g.aclose()  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001
                            pass
            else:
                await close_all()
        if winner < 0 or first is None:
            yield last_err or {"type": "error", "retryable": False,
                               "message": "Kein kostenloses Modell hat rechtzeitig geantwortet."}
            return
        yield first
        async for ev in gens[winner]:
            yield ev

    async def health(self) -> dict:
        return await self.providers[0].health()
