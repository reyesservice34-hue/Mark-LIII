"""
MCP: fremde Werkzeugserver anbinden — dieselben, die auch Claude benutzt.

Das Model Context Protocol ist der Stecker, auf den sich die Branche geeinigt
hat. Wer einen MCP-Server hat (GitHub, Notion, Linear, Sentry, ein eigener),
kann ihn hier eintragen, und seine Werkzeuge stehen JARVIS zur Verfügung — im
selben Verzeichnis wie die eingebauten, mit derselben Rollenprüfung, demselben
Genehmigungstor und demselben Protokoll.

Was hier geht und was nicht, damit niemand danach sucht:

* Angebunden werden Server über HTTP (Streamable HTTP / JSON-RPC, mit oder
  ohne SSE-Antwort). Das ist die Form, in der MCP-Server aus der Ferne
  angeboten werden.
* Server, die als Programm auf demselben Rechner gestartet werden (stdio),
  gehen NICHT. Dieser Server läuft in einem Container ohne die Laufzeiten
  fremder Pakete, und ein Prozess-Start aus dem Netz heraus wäre genau das
  Loch, das der Rest dieser Anwendung vermeidet.
* Beglaubigt wird mit einem Token im Authorization-Kopf. Es bleibt in der
  Datenbank auf dem Server und wird nie ausgeliefert — die Oberfläche sieht
  nur, ob eines hinterlegt ist.

Werkzeuge aus einem MCP-Server gelten als `risk="high"` und brauchen damit
eine Genehmigung, solange niemand das ausdrücklich anders setzt: Es ist
fremder Code auf einem fremden Rechner, und was er wirklich tut, steht in
seiner Beschreibung — nicht in unserem Quelltext.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..db import Database, dumps, loads, new_id, now_iso

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "jarvis-command-center", "version": "1.0"}
TIMEOUT = 30.0


class McpError(RuntimeError):
    """Etwas ging schief, und der Grund gehört in die Oberfläche."""


def _tool_name(server_slug: str, tool: str) -> str:
    return f"mcp.{server_slug}.{tool}"


class McpClient:
    """Eine Sitzung mit einem MCP-Server über HTTP.

    Kurzlebig: aufbauen, fragen, schließen. Eine dauerhaft offene Sitzung
    brächte hier nichts außer der Pflicht, sie am Leben zu halten.
    """

    def __init__(self, url: str, token: str = "", headers: dict | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.extra = headers or {}
        self._session_id = ""

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            # Manche Server antworten als Ereignisstrom, manche als JSON. Beides
            # anzunehmen erspart eine Fallunterscheidung beim Verbinden.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.extra,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse(res: httpx.Response) -> dict:
        """Antwort lesen, gleich ob JSON oder Ereignisstrom."""
        text = res.text.strip()
        ctype = res.headers.get("content-type", "")
        if "text/event-stream" in ctype or text.startswith("event:") or text.startswith("data:"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    body = line[5:].strip()
                    if body and body != "[DONE]":
                        try:
                            msg = json.loads(body)
                        except ValueError:
                            continue
                        if "result" in msg or "error" in msg:
                            return msg
            raise McpError("Der Server antwortete als Ereignisstrom ohne Ergebnis.")
        try:
            return json.loads(text) if text else {}
        except ValueError as e:
            raise McpError(f"Der Server antwortete nicht in JSON: {text[:120]}") from e

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": new_id(), "method": method}
        if params is not None:
            payload["params"] = params
        try:
            res = await client.post(self.url, json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise McpError(f"Der Server ist nicht erreichbar: {e.__class__.__name__}") from e
        if res.status_code in (401, 403):
            raise McpError("Der Server weist die Anmeldung ab (%d). Token prüfen." % res.status_code)
        if res.status_code == 404:
            raise McpError("Unter dieser Adresse liegt kein MCP-Endpunkt (404).")
        if res.status_code >= 400:
            raise McpError(f"Der Server antwortete mit HTTP {res.status_code}.")
        sid = res.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        msg = self._parse(res)
        if "error" in msg:
            err = msg["error"] or {}
            raise McpError(str(err.get("message") or err)[:300])
        return msg.get("result")

    async def _notify(self, client: httpx.AsyncClient, method: str) -> None:
        try:
            await client.post(self.url, json={"jsonrpc": "2.0", "method": method},
                              headers=self._headers())
        except httpx.HTTPError:
            pass      # eine Benachrichtigung ohne Antwort darf nichts umwerfen

    async def handshake(self, client: httpx.AsyncClient) -> dict:
        result = await self._rpc(client, "initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        await self._notify(client, "notifications/initialized")
        return result or {}

    async def list_tools(self) -> tuple[dict, list[dict]]:
        """(Serverangaben, Werkzeuge). Eine Sitzung, zwei Fragen."""
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            info = await self.handshake(client)
            result = await self._rpc(client, "tools/list") or {}
            tools = result.get("tools") or []
            # Große Server liefern seitenweise.
            cursor = result.get("nextCursor")
            while cursor and len(tools) < 500:
                more = await self._rpc(client, "tools/list", {"cursor": cursor}) or {}
                tools.extend(more.get("tools") or [])
                cursor = more.get("nextCursor")
        return info, tools

    async def call(self, tool: str, arguments: dict) -> dict:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            await self.handshake(client)
            result = await self._rpc(client, "tools/call",
                                     {"name": tool, "arguments": arguments or {}}) or {}
        return result


def flatten(result: dict) -> dict:
    """Die Antwort eines MCP-Werkzeugs in etwas verwandeln, das ein Modell liest.

    MCP liefert eine Liste von Inhaltsblöcken. Die dem Modell roh vorzusetzen
    hieße, ihm das Auspacken beizubringen; also wird der Text zusammengelegt
    und strukturierte Daten bleiben daneben stehen.
    """
    blocks = result.get("content") or []
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    other = [b for b in blocks if isinstance(b, dict) and b.get("type") != "text"]
    out: dict[str, Any] = {"ok": not result.get("isError")}
    if texts:
        out["text"] = "\n".join(t for t in texts if t)
    if result.get("structuredContent"):
        out["data"] = result["structuredContent"]
    if other:
        out["attachments"] = [{"type": b.get("type")} for b in other]
    if result.get("isError"):
        out["error"] = out.pop("text", "Das Werkzeug meldete einen Fehler.")
    return out


class McpRegistry:
    """Die eingetragenen Server, ihre Werkzeuge und deren Anmeldung im Verzeichnis."""

    def __init__(self, state) -> None:
        self.state = state
        self.db: Database = state.db

    # ── Bestand ──────────────────────────────────────────────────────────
    def servers(self, *, with_secrets: bool = False) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM mcp_servers ORDER BY name")
        return [self._public(r, with_secrets=with_secrets) for r in rows]

    def get(self, server_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM mcp_servers WHERE id=?", (server_id,))
        return dict(row) if row else None

    @staticmethod
    def _public(row: dict, *, with_secrets: bool = False) -> dict:
        out = {
            "id": row["id"], "name": row["name"], "slug": row["slug"], "url": row["url"],
            "enabled": bool(row["enabled"]), "status": row["status"], "detail": row["detail"],
            "tool_count": row["tool_count"], "tools": loads(row["tools"], []),
            "requires_approval": bool(row["requires_approval"]),
            "has_token": bool(row["token"]), "server_info": loads(row["server_info"], {}),
            "created_at": row["created_at"], "checked_at": row["checked_at"],
        }
        if with_secrets:
            out["token"] = row["token"]
        return out

    @staticmethod
    def slugify(name: str) -> str:
        keep = [c if (c.isalnum() or c == "_") else "_" for c in name.strip().lower()]
        slug = "".join(keep).strip("_") or "server"
        return slug[:32]

    def add(self, *, name: str, url: str, token: str = "", requires_approval: bool = True,
            actor: str = "") -> dict:
        if not url.startswith(("http://", "https://")):
            raise McpError("Die Adresse muss mit http:// oder https:// beginnen.")
        slug = self.slugify(name)
        if self.db.fetchone("SELECT id FROM mcp_servers WHERE slug=?", (slug,)):
            raise McpError(f"Ein Server mit dem Namen '{slug}' ist schon eingetragen.")
        row = {
            "id": new_id("mcp"), "name": name.strip()[:80] or slug, "slug": slug, "url": url.rstrip("/"),
            "token": token, "enabled": 1, "status": "unknown", "detail": "noch nicht geprüft",
            "tools": dumps([]), "tool_count": 0, "server_info": dumps({}),
            "requires_approval": 1 if requires_approval else 0,
            "created_at": now_iso(), "checked_at": None, "created_by": actor,
        }
        self.db.insert("mcp_servers", row)
        self.state.log.audit(actor_type="user", actor_id=actor, action="mcp.add", target=slug,
                             status="ok", meta={"url": row["url"]})
        return self._public(row)

    def update(self, server_id: str, **fields) -> dict | None:
        row = self.get(server_id)
        if not row:
            return None
        allowed = {k: v for k, v in fields.items()
                   if k in ("name", "url", "token", "enabled", "requires_approval") and v is not None}
        if "enabled" in allowed:
            allowed["enabled"] = 1 if allowed["enabled"] else 0
        if "requires_approval" in allowed:
            allowed["requires_approval"] = 1 if allowed["requires_approval"] else 0
        if allowed:
            self.db.update("mcp_servers", server_id, allowed)
        if allowed.get("enabled") == 0:
            self.unregister_tools(row["slug"])
        return self._public(self.get(server_id) or {})

    def remove(self, server_id: str, actor: str = "") -> bool:
        row = self.get(server_id)
        if not row:
            return False
        self.unregister_tools(row["slug"])
        self.db.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))
        self.state.log.audit(actor_type="user", actor_id=actor, action="mcp.remove",
                             target=row["slug"], status="ok")
        return True

    # ── Verbinden ────────────────────────────────────────────────────────
    def client(self, row: dict) -> McpClient:
        return McpClient(row["url"], row["token"])

    async def refresh(self, server_id: str) -> dict:
        """Nachsehen, was der Server kann, und seine Werkzeuge anmelden."""
        row = self.get(server_id)
        if not row:
            raise McpError("Diesen Server gibt es nicht.")
        try:
            info, tools = await self.client(row).list_tools()
        except McpError as e:
            self.db.update("mcp_servers", server_id,
                           {"status": "offline", "detail": str(e)[:300], "checked_at": now_iso()})
            self.unregister_tools(row["slug"])
            return self._public(self.get(server_id) or {})

        names = [str(t.get("name", "")) for t in tools if t.get("name")]
        self.db.update("mcp_servers", server_id, {
            "status": "healthy", "detail": f"{len(names)} Werkzeuge", "tools": dumps(names),
            "tool_count": len(names), "server_info": dumps(info.get("serverInfo") or {}),
            "checked_at": now_iso(),
        })
        if row["enabled"]:
            self.register_tools(self.get(server_id) or {}, tools)
        return self._public(self.get(server_id) or {})

    async def refresh_all(self) -> list[dict]:
        out = []
        for s in self.db.fetchall("SELECT id FROM mcp_servers WHERE enabled=1"):
            try:
                out.append(await self.refresh(s["id"]))
            except McpError:
                continue
        return out

    # ── Anmeldung im Werkzeugverzeichnis ─────────────────────────────────
    def unregister_tools(self, slug: str) -> int:
        prefix = f"mcp.{slug}."
        gone = [t.name for t in self.state.tools.all() if t.name.startswith(prefix)]
        for name in gone:
            self.state.tools.unregister(name)
        return len(gone)

    def register_tools(self, row: dict, tools: list[dict]) -> int:
        from ..orchestrator.tool_registry import ToolSpec

        self.unregister_tools(row["slug"])
        url, token, slug = row["url"], row["token"], row["slug"]
        needs_approval = bool(row["requires_approval"])
        count = 0
        for t in tools:
            raw_name = str(t.get("name") or "")
            if not raw_name:
                continue

            async def handler(_ctx, args: dict, _tool=raw_name, _url=url, _token=token) -> dict:
                result = await McpClient(_url, _token).call(_tool, args or {})
                return flatten(result)

            self.state.tools.register(ToolSpec(
                name=_tool_name(slug, raw_name),
                description=(str(t.get("description") or "")[:600]
                             or f"Werkzeug '{raw_name}' vom MCP-Server {row['name']}"),
                input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                category=f"mcp:{slug}",
                # Fremder Code auf einem fremden Rechner: hoch eingestuft, bis
                # der Betreiber dieses Servers ausdrücklich etwas anderes sagt.
                risk="high" if needs_approval else "medium",
                requires_approval=needs_approval,
                source=f"mcp:{slug}",
                handler=handler,
            ), replace=True)
            count += 1
        return count

    async def load_all(self) -> int:
        """Beim Start: eingetragene Server abklopfen, ohne den Start aufzuhalten."""
        rows = self.db.fetchall("SELECT id FROM mcp_servers WHERE enabled=1")
        if not rows:
            return 0
        done = await asyncio.gather(*(self.refresh(r["id"]) for r in rows), return_exceptions=True)
        return sum(1 for d in done if isinstance(d, dict) and d.get("status") == "healthy")


__all__ = ["McpClient", "McpError", "McpRegistry", "flatten"]
