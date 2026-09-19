"""
Composio — one account instead of a dozen sets of credentials.

Composio hosts the OAuth dance for a few hundred services (Gmail, Slack,
Notion, Linear, …) and exposes each as a tool slug. This server does not
re-implement any of that: it asks Composio which toolkits *this project has
actually connected*, which tools those offer, and executes one on request.

Three rules keep it honest:

  * Nothing is claimed without a connection. A tool belonging to a toolkit
    with no ACTIVE connected account is refused by name, not attempted.
  * A Composio call that answers `successful: false` is an error here too,
    carrying Composio's own message rather than a cheerful summary.
  * With no API key the whole thing reports NOT CONFIGURED and says which
    variable is missing.

API (verified against https://backend.composio.dev/api/v3/openapi.json):
  GET  /api/v3/connected_accounts        header x-api-key
  GET  /api/v3/toolkits
  GET  /api/v3/tools
  POST /api/v3/tools/execute/{tool_slug}
"""
from __future__ import annotations

import os
from typing import Any

import httpx

COMPOSIO_API = "https://backend.composio.dev"
# Composio's own wording for a connection that can be used right now.
LIVE = "ACTIVE"
DASHBOARD = "https://platform.composio.dev"


class ComposioError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class ComposioService:
    def __init__(self) -> None:
        self.api_key = _env("COMPOSIO_API_KEY")
        # Composio scopes connections per end user; a single-operator server
        # has exactly one, and "default" is Composio's own convention for it.
        self.user_id = _env("COMPOSIO_USER_ID") or "default"
        self.base = (_env("COMPOSIO_BASE_URL") or COMPOSIO_API).rstrip("/")

    # ── configuration ────────────────────────────────────────────────────
    def configured(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        return "" if self.configured() else "COMPOSIO_API_KEY not set"

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "Accept": "application/json",
                "User-Agent": "JARVIS-CommandCenter/0.1"}

    # ── transport ────────────────────────────────────────────────────────
    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: dict | None = None, timeout: float = 30.0) -> Any:
        if not self.configured():
            raise ComposioError(self.unavailable_reason())
        url = f"{self.base}/api/v3{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.request(method, url, headers=self._headers(), params=params, json=json)
        except httpx.HTTPError as e:
            raise ComposioError(f"cannot reach Composio: {e.__class__.__name__}") from e
        if r.status_code in (401, 403):
            raise ComposioError("Composio rejected the API key.")
        if r.status_code == 404:
            raise ComposioError("Composio does not know that tool or toolkit.")
        if r.status_code == 429:
            raise ComposioError("Composio rate limit reached — try again shortly.")
        if r.status_code >= 400:
            raise ComposioError(f"Composio refused the request (HTTP {r.status_code}): {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise ComposioError("Composio sent a response that is not JSON.") from e

    # ── reading ──────────────────────────────────────────────────────────
    async def connections(self) -> list[dict]:
        """Connected accounts for this user, newest first."""
        body = await self._request("GET", "/connected_accounts",
                                   params={"user_ids": self.user_id, "limit": 100})
        out = []
        for item in (body or {}).get("items", []) or []:
            toolkit = (item.get("toolkit") or {}).get("slug", "")
            out.append({"id": item.get("id", ""), "toolkit": toolkit,
                        "status": item.get("status", "UNKNOWN"),
                        "disabled": bool(item.get("is_disabled")),
                        "created_at": item.get("created_at", "")})
        return out

    async def live_toolkits(self) -> dict[str, str]:
        """{toolkit slug: connected account id} for connections usable right now."""
        return {c["toolkit"]: c["id"] for c in await self.connections()
                if c["status"] == LIVE and not c["disabled"] and c["toolkit"]}

    async def tools(self, toolkit: str = "", search: str = "", limit: int = 20) -> list[dict]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
        if toolkit:
            params["toolkit_slug"] = toolkit.lower()
        if search:
            params["search"] = search
        body = await self._request("GET", "/tools", params=params)
        out = []
        for item in (body or {}).get("items", []) or []:
            tk = item.get("toolkit") or {}
            out.append({"slug": item.get("slug", ""), "name": item.get("name", ""),
                        "toolkit": tk.get("slug", ""),
                        "description": (item.get("description") or "")[:400],
                        "needs_connection": not item.get("no_auth", False),
                        "input_parameters": item.get("input_parameters") or {}})
        return out

    async def toolkits(self, search: str = "", limit: int = 30) -> list[dict]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
        if search:
            params["search"] = search
        body = await self._request("GET", "/toolkits", params=params)
        return [{"slug": i.get("slug", ""), "name": i.get("name", ""),
                 "no_auth": bool(i.get("no_auth"))}
                for i in (body or {}).get("items", []) or []]

    # ── acting ───────────────────────────────────────────────────────────
    async def execute(self, tool_slug: str, arguments: dict | None = None,
                      text: str = "") -> dict:
        """Run one Composio tool. `arguments` and `text` are mutually exclusive."""
        slug = (tool_slug or "").strip().upper()
        if not slug:
            raise ComposioError("No tool slug given — call composio.tools first to find one.")
        if arguments and text:
            raise ComposioError("Give either arguments or a text instruction, not both.")

        # Refuse by name rather than firing a call that cannot work.
        toolkit = slug.split("_", 1)[0].lower()
        live = await self.live_toolkits()
        account_id = live.get(toolkit)
        if account_id is None and toolkit not in ("composio",):
            known = ", ".join(sorted(live)) or "none"
            raise ComposioError(
                f"'{toolkit}' is not connected in Composio, so {slug} cannot run. "
                f"Connected right now: {known}. Connect it once at {DASHBOARD}, then try again.")

        body: dict[str, Any] = {"user_id": self.user_id}
        if account_id:
            body["connected_account_id"] = account_id
        if text:
            body["text"] = text
        else:
            body["arguments"] = arguments or {}

        result = await self._request("POST", f"/tools/execute/{slug}", json=body, timeout=90.0)
        if not isinstance(result, dict):
            raise ComposioError("Composio sent an unexpected response.")
        if result.get("successful") is False:
            raise ComposioError(f"{slug} failed: {result.get('error') or 'Composio gave no reason.'}")
        return {"tool": slug, "toolkit": toolkit, "data": result.get("data"),
                "log_id": result.get("log_id", "")}

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": self.unavailable_reason()}
        try:
            conns = await self.connections()
        except ComposioError as e:
            detail = str(e)
            offline = "reject" in detail or "cannot reach" in detail
            return {"status": "offline" if offline else "degraded", "detail": detail}
        live = [c for c in conns if c["status"] == LIVE and not c["disabled"]]
        if not conns:
            return {"status": "degraded",
                    "detail": f"API key works, but no app is connected yet for user '{self.user_id}' — "
                              f"connect one at {DASHBOARD}"}
        if not live:
            broken = ", ".join(sorted({f"{c['toolkit']} ({c['status'].lower()})" for c in conns}))
            return {"status": "degraded", "detail": f"no usable connection: {broken}"}
        return {"status": "healthy",
                "detail": f"{len(live)} connected: {', '.join(sorted(c['toolkit'] for c in live))}"}


__all__ = ["ComposioService", "ComposioError", "DASHBOARD"]
