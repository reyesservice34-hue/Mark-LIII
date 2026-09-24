"""Keeps the local model loaded and its long prompt prefix in the model's cache.

A local CPU model needs minutes to digest MIA's long system prompt cold (measured: ~26 tokens/s, so a
13k-token prompt is ~8 minutes) but ~1 second when the same prefix is already cached. This job sends
exactly the prefix a real chat sends (runtime.stable_prefix) so the first real message does not pay for
it, and it pins the model so the cache is not lost when Ollama unloads an idle model.

It only ever talks to the local provider, only while no run is active (it must not compete with a real
conversation for the CPU), and it never touches chats, tasks or memory.
"""
from __future__ import annotations

import time

import httpx


class LocalWarmup:
    def __init__(self, state) -> None:
        self.state = state
        self.last: dict = {}

    def _provider(self):
        rt = self.state.runtime
        provider = rt.fast_provider or rt.provider
        return provider if provider is not None and rt._is_local(provider) else None

    @staticmethod
    async def _pin(provider) -> bool:
        """Ask Ollama to keep the model loaded (keep_alive -1). Other servers just don't have this endpoint."""
        base = getattr(provider, "base_url", "")
        if not base:
            return False
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(base.removesuffix("/v1") + "/api/generate",
                                      json={"model": provider.info.model, "prompt": "", "keep_alive": -1})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def run_once(self) -> dict:
        st = self.state
        provider = self._provider()
        if provider is None:
            return {"skipped": "kein lokaler Anbieter"}
        agent = st.agents.get(st.agents.master_id())
        if agent is None or not agent.enabled:
            return {"skipped": "Master-Agent nicht verfügbar"}
        if st.runtime.active_runs():
            return {"skipped": "ein Lauf ist aktiv"}
        started = time.monotonic()
        pinned = await self._pin(provider)
        # The prefix a chat of an admin gets; other roles see other tools and warm their own prefix on first use.
        system, tool_defs = st.runtime.stable_prefix(agent, st.tools.for_agent(agent.tools, "admin"))
        error = ""
        async for ev in provider.stream(system=system, tools=tool_defs, max_tokens=1,
                                        messages=[{"role": "user", "content": [{"type": "text", "text": "Antworte mit: ok"}]}]):
            if ev.get("type") == "error":
                error = str(ev.get("message", ""))[:300]
                break
        self.last = {"ok": not error, "pinned": pinned, "model": provider.info.model, "prefix_chars": len(system),
                     "tools": len(tool_defs), "seconds": round(time.monotonic() - started, 1), "error": error}
        if error:
            raise RuntimeError(f"Vorwärmen fehlgeschlagen: {error}")
        return self.last
