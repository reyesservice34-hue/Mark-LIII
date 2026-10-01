"""
Integration registry.

Each adapter answers two questions honestly: *is it configured* (are the
required environment variables present — values are never returned) and *is
it healthy* (a real request, when the service offers a cheap one). Nothing is
mocked: an integration with no credentials is reported as NOT CONNECTED.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..db import Database, dumps, loads, now_iso
from ..events import EventBus

STATUSES = ("healthy", "degraded", "offline", "not_configured", "unknown")


def jwt_claims(token: str) -> dict:
    """Die Angaben aus einem JWT lesen, ohne ihn zu prüfen oder zu verwenden.

    Ein n8n-Schlüssel IST ein JWT. Sein Mittelteil ist unverschlüsselt — er
    trägt das Ablaufdatum und den Aussteller. Das ist kein Geheimnis, und es
    entscheidet eine Frage, die sonst nur zu raten wäre: Ein 401 heißt nicht
    von selbst „abgelaufen". Läuft der Schlüssel erst nächstes Jahr ab und
    wird trotzdem abgelehnt, dann gehört er einer ANDEREN Instanz — und wer
    ihn dann neu erzeugt, erzeugt ihn zum zweiten Mal an der falschen Stelle.

    Nie wird der Schlüssel selbst zurückgegeben, nur was über ihn aussagbar
    ist. Ein Wert, der kein JWT ist, ergibt ein leeres Ergebnis.
    """
    teile = token.split(".")
    if len(teile) != 3:
        return {}
    mitte = teile[1]
    mitte += "=" * (-len(mitte) % 4)          # base64url ohne Polster
    try:
        return json.loads(base64.urlsafe_b64decode(mitte))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}


def key_verdict(token: str) -> str:
    """Ein Satz darüber, was der Schlüssel selbst über sich sagt — oder ''."""
    exp = jwt_claims(token).get("exp")
    if not isinstance(exp, (int, float)):
        return ""
    if exp < time.time():
        tag = time.strftime("%d.%m.%Y", time.localtime(exp))
        return f"Der Schlüssel ist am {tag} abgelaufen."
    tag = time.strftime("%d.%m.%Y", time.localtime(exp))
    return (f"Der Schlüssel läuft erst am {tag} ab, ist also NICHT abgelaufen — "
            f"er gehört zu einer anderen n8n-Instanz oder wurde widerrufen.")


@dataclass
class IntegrationAdapter:
    id: str
    name: str
    kind: str
    capabilities: list[str]
    required_env: list[str] = field(default_factory=list)   # all required
    any_env: list[str] = field(default_factory=list)        # at least one required
    optional_env: list[str] = field(default_factory=list)
    docs: str = ""
    icon: str = "plug"

    def configured(self) -> bool:
        if self.required_env and not all(os.environ.get(k) for k in self.required_env):
            return False
        if self.any_env and not any(os.environ.get(k) for k in self.any_env):
            return False
        return bool(self.required_env or self.any_env)

    def config_state(self) -> dict:
        keys = [*self.required_env, *self.any_env, *self.optional_env]
        return {k: bool(os.environ.get(k)) for k in keys}

    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "credentials not set"}
        return {"status": "unknown", "detail": "credentials present; no live check for this integration yet"}


class AnthropicIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "ANTHROPIC_API_KEY not set"}
        from ..ai import build_provider
        p = build_provider("anthropic")
        return await p.health() if p else {"status": "offline", "detail": "provider init failed"}


class OpenAIIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "OPENAI_API_KEY not set"}
        from ..ai import build_provider
        p = build_provider("openai")
        return await p.health() if p else {"status": "offline", "detail": "provider init failed"}


class GeminiIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "GEMINI_API_KEY not set"}
        from ..ai import build_provider
        p = build_provider("gemini")
        return await p.health() if p else {"status": "offline", "detail": "provider init failed"}


class LocalLLMIntegration(IntegrationAdapter):
    """Ein lokales Modell kostet nichts — aber nur, wenn es auch etwas kann.

    Der übliche Gesundheitscheck fragt, ob das Modell gelistet ist. Das sagt
    nichts darüber, ob es Werkzeuge aufrufen kann, und genau daran hängt alles:
    Ein Modell ohne Werkzeugaufrufe macht JARVIS zu einem Gesprächspartner ohne
    Hände. Er redet dann über den Kalender, statt einen Termin anzulegen.

    Deshalb wird es hier wirklich ausprobiert — einmal, und das Ergebnis wird
    gemerkt: Ein lokales Modell antwortet langsam, und ein Probelauf bei jedem
    Gesundheitscheck wäre eine Dauerlast auf demselben Rechner.
    """

    _tools_ok: tuple[str, bool, str] | None = None     # (Modell, kann es, Grund)
    _probe: "asyncio.Task | None" = None
    _probe_at: float = 0.0
    _TRANSIENT = "Werkzeugprobe nicht möglich"

    @classmethod
    async def _run_probe(cls, p) -> None:
        # Own long wait: a cold local model needs minutes on a CPU. The 15 s of the health check would cancel
        # it mid-load and start the load again at every refresh.
        kann, grund = await p.tool_check(timeout=900.0)
        if not grund.startswith(cls._TRANSIENT):        # only a real answer is remembered, not a hiccup
            cls._tools_ok = (p.info.model, kann, grund)

    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "LOCAL_LLM_URL not set"}
        from ..ai import build_provider
        p = build_provider("local")
        if p is None:
            return {"status": "offline", "detail": "provider init failed"}
        # Check the model that answers chats by default. A local server holds only a couple of models in memory:
        # probing the large one would evict the resident chat model (and its warmed prompt cache) at every check.
        fast = os.environ.get("JARVIS_FAST_MODEL", "").strip()
        if fast and hasattr(p, "base_url") and p.info.model != fast:
            from ..ai.openai_compat import OpenAICompatProvider
            p = OpenAICompatProvider("local", p.base_url, p.api_key, fast)
        zustand = await p.health()
        if zustand.get("status") != "healthy" or not hasattr(p, "tool_check"):
            return zustand

        gemerkt = type(self)._tools_ok
        if gemerkt is None or gemerkt[0] != p.info.model:
            cls = type(self)
            if cls._probe is None or (cls._probe.done() and time.monotonic() - cls._probe_at > 600):
                cls._probe_at = time.monotonic()
                cls._probe = asyncio.create_task(cls._run_probe(p))
            return {"status": "degraded",
                    "detail": f"Werkzeugprobe für {p.info.model} läuft im Hintergrund (auf der CPU dauert das Minuten); "
                              "bis dahin nicht bestätigt."}
        _, kann, grund = gemerkt

        if not kann:
            # Erreichbar, aber für diesen Zweck untauglich — das ist „degraded",
            # nicht „healthy". Alles andere wäre ein grüner Punkt über einem
            # Modell, mit dem nichts funktioniert.
            return {"status": "degraded", "detail": grund}
        return {"status": "healthy", "detail": f"{p.info.model} — {grund}, kostet nichts"}


class N8nIntegration(IntegrationAdapter):
    db: Database | None = None

    def _andere_instanz(self, base: str) -> str:
        """Zeigt ein eingetragener MCP-Server auf ein ANDERES n8n als hier?

        Der Fall, der sonst stundenlang kostet: Der Schlüssel stammt aus der
        n8n-Cloud, aber N8N_BASE_URL zeigt auf einen Container nebenan. Beide
        antworten, beide heißen n8n — und der Cloud-Schlüssel wird vom
        Container abgelehnt, weil er ihn nicht ausgestellt hat. Das sieht aus
        wie ein toter Schlüssel und ist eine falsche Adresse.
        """
        if self.db is None:
            return ""
        try:
            rows = self.db.fetchall("SELECT url FROM mcp_servers WHERE enabled=1")
        except Exception:  # noqa: BLE001 — Diagnose darf nie die Prüfung stürzen lassen
            return ""
        hier = httpx.URL(base).host if base else ""
        for row in rows:
            url = str(row["url"] or "")
            if "n8n" not in url.lower():
                continue
            dort = httpx.URL(url).host
            if dort and dort != hier:
                return (f" Achtung: Ein eingetragener MCP-Server zeigt auf {dort}, "
                        f"N8N_BASE_URL aber auf {hier or base}. Ein n8n-Schlüssel gilt nur "
                        f"bei der Instanz, die ihn ausgestellt hat — steht der Schlüssel "
                        f"von {dort} in der .env, muss dort auch N8N_BASE_URL hinzeigen.")
        return ""

    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "N8N_BASE_URL / N8N_API_KEY not set"}
        base = os.environ["N8N_BASE_URL"].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(f"{base}/api/v1/workflows", params={"limit": 1},
                                headers={"X-N8N-API-KEY": os.environ["N8N_API_KEY"]})
            # Die Zahl allein schickt niemanden zur Lösung. n8n unterscheidet
            # zwei Fälle, die gleich aussehen: ein Schlüssel, den es nicht mehr
            # gibt (401), und eine öffentliche API, die gar nicht eingeschaltet
            # ist (404 auf einen Pfad, den es sonst immer gibt).
            if r.status_code == 401:
                # „Neu erzeugen" ist nur dann der richtige Rat, wenn der
                # Schlüssel wirklich hinüber ist. Er sagt selbst, ob er das
                # ist — und wenn nicht, schickt derselbe Satz den Nutzer
                # zum zweiten Mal an die falsche Stelle.
                urteil = key_verdict(os.environ["N8N_API_KEY"])
                rat = ("Neu erzeugen in n8n unter Settings → n8n API, dann setup-keys.sh"
                       if "abgelaufen." in urteil or not urteil
                       else "Zuerst prüfen, ob N8N_BASE_URL auf die Instanz zeigt, "
                            "bei der der Schlüssel erzeugt wurde.")
                return {"status": "offline",
                        "detail": (f"n8n unter {base} weist den Schlüssel ab (401). "
                                   f"{urteil} {rat}{self._andere_instanz(base)}").strip()}
            if r.status_code == 404:
                return {"status": "offline",
                        "detail": "n8n antwortet, aber die öffentliche API ist dort nicht "
                                  "eingeschaltet (404 auf /api/v1/workflows)"}
            if r.status_code >= 400:
                return {"status": "degraded", "detail": f"n8n HTTP {r.status_code}"}
            return {"status": "healthy", "detail": "n8n API reachable"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach n8n: {e.__class__.__name__}"}


class GitHubIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "GITHUB_TOKEN not set"}
        token = os.environ["GITHUB_TOKEN"]
        if not token.isascii() or any(ch.isspace() for ch in token):
            # A token copied from a UI that masks it ("ghp_ab••••••") is not a token. Say so instead of
            # letting the HTTP layer fail with an unreadable 'ascii' codec error.
            return {"status": "offline", "detail": "GITHUB_TOKEN enthält Sonderzeichen oder Leerzeichen (z. B. •): "
                                                   "vermutlich maskiert kopiert. Den vollständigen Token neu eintragen."}
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get("https://api.github.com/user", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json"})
            if r.status_code == 401:
                return {"status": "offline", "detail": "GitHub rejected the token"}
            if r.status_code >= 400:
                return {"status": "degraded", "detail": f"GitHub HTTP {r.status_code}"}
            return {"status": "healthy", "detail": f"authenticated as {r.json().get('login', '?')}"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach GitHub: {e.__class__.__name__}"}


class ControlPlaneIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "JARVIS_GATEWAY_URL / JARVIS_GATEWAY_TOKEN not set"}
        base = os.environ["JARVIS_GATEWAY_URL"].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(f"{base}/v1/commands/health-probe",
                                headers={"X-Jarvis-Token": os.environ["JARVIS_GATEWAY_TOKEN"]})
            if r.status_code in (401, 403):
                return {"status": "offline", "detail": "control plane rejected the gateway token"}
            return {"status": "healthy", "detail": f"reachable (HTTP {r.status_code})"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach control plane: {e.__class__.__name__}"}


class GoogleIntegration(IntegrationAdapter):
    """Checked through the calendar service, because that is what actually uses it."""

    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured",
                    "detail": "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN not set"}
        from ..services.calendar_service import CalendarService
        return await CalendarService().health()


class LocalCalendarIntegration(IntegrationAdapter):
    """Always present: the local calendar store needs no credentials at all."""

    def configured(self) -> bool:
        return True

    def config_state(self) -> dict:
        return {}

    async def check(self) -> dict:
        from ..services.calendar_service import CalendarService
        svc = CalendarService()
        if not svc.available():
            return {"status": "offline", "detail": svc.unavailable_reason()}
        if svc.backend_name() == "google":
            return {"status": "healthy", "detail": "standing by — Google Calendar is the active backend"}
        return await svc.health()


@dataclass
class SmtpImapIntegration(IntegrationAdapter):
    slug: str = "buero"          # welches Postfach diese Karte betrifft

    async def check(self) -> dict:
        from ..services.email_service import EmailService
        svc = EmailService()
        if self.slug not in svc.accounts:
            return {"status": "not_configured", "detail": f"{self.required_env[0]} / {self.required_env[1]} not set"}
        return await svc.health(self.slug)


class VoiceIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        from ..services.voice_service import VoiceService
        return await VoiceService().health()


class ComposioIntegration(IntegrationAdapter):
    """One key, then every app the user connected in Composio's own dashboard.

    'Configured' here means the API key is present; whether anything is
    actually *connected* is the health check's job, because that is decided in
    Composio, not in this server's environment.
    """

    async def check(self) -> dict:
        from ..services.composio import ComposioService
        return await ComposioService().health()


class DesktopChannelIntegration(IntegrationAdapter):
    """A capability the server borrows from a paired desktop.

    WhatsApp is the case that matters: the working implementation is the
    desktop's linked WhatsApp Web session, not a Meta Business account. So this
    reports what is actually true — whether a paired PC offers that action and
    is reachable — instead of asking for credentials nothing here would use.
    """

    def __init__(self, *args, action: str = "", db=None, **kw):
        super().__init__(*args, **kw)
        self.action = action
        self.db = db

    def configured(self) -> bool:
        return bool(self._devices())

    def config_state(self) -> dict:
        return {f"desktop offers {self.action}": bool(self._devices())}

    def _devices(self) -> list[dict]:
        if self.db is None:
            return []
        try:
            rows = self.db.fetchall("SELECT id, name, actions, last_seen_at FROM desktop_devices")
        except Exception:      # a shut-down app's connection must not break a listing
            return []
        out = []
        for row in rows:
            actions = loads(row["actions"], [])
            if any(a.get("name") == self.action for a in actions):
                out.append({"id": row["id"], "name": row["name"], "last_seen_at": row["last_seen_at"]})
        return out

    async def check(self) -> dict:
        devices = self._devices()
        if not devices:
            return {"status": "not_configured",
                    "detail": f"no paired desktop offers '{self.action}' — pair a PC (Desktop page) and "
                              f"link the channel there once"}
        from datetime import datetime, timezone
        fresh = []
        for d in devices:
            try:
                seen = datetime.fromisoformat((d["last_seen_at"] or "").replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - seen).total_seconds() < 90:
                    fresh.append(d["name"])
            except ValueError:
                continue
        if fresh:
            return {"status": "healthy", "detail": f"available through {', '.join(fresh)}"}
        return {"status": "offline",
                "detail": f"{devices[0]['name']} offers it but is not connected right now"}


DEFAULT_ADAPTERS: list[IntegrationAdapter] = [
    AnthropicIntegration("anthropic", "Anthropic", "ai", ["chat", "tool use", "vision"],
                         required_env=["ANTHROPIC_API_KEY"], icon="sparkles"),
    OpenAIIntegration("openai", "OpenAI", "ai", ["chat", "tool use", "vision"],
                      required_env=["OPENAI_API_KEY"], optional_env=["OPENAI_BASE_URL"], icon="cpu"),
    GeminiIntegration("gemini", "Google Gemini", "ai", ["chat", "tool use", "vision"],
                      any_env=["GEMINI_API_KEY", "GOOGLE_API_KEY"], icon="cpu"),
    LocalLLMIntegration("local_llm", "Local model (Ollama / LM Studio)", "ai", ["chat", "tool use"],
                        required_env=["LOCAL_LLM_URL"], optional_env=["LOCAL_LLM_MODEL"], icon="cpu"),
    N8nIntegration("n8n", "n8n", "automation", ["list workflows", "executions", "trigger via webhook",
                                                  "activate/deactivate"],
                   required_env=["N8N_BASE_URL", "N8N_API_KEY"], optional_env=["N8N_WEBHOOK_BASE_URL"],
                   icon="workflow"),
    GitHubIntegration("github", "GitHub", "code",
                      ["read files", "issues and pull requests", "commits", "repository overview"],
                      required_env=["GITHUB_TOKEN"], optional_env=["GITHUB_DEFAULT_REPO"], icon="git-branch"),
    ControlPlaneIntegration("control_plane", "Upstream JARVIS control plane", "agent",
                            ["remote master agent", "shared memory"],
                            required_env=["JARVIS_GATEWAY_URL", "JARVIS_GATEWAY_TOKEN"], icon="radio"),
    GoogleIntegration("google_calendar", "Google Calendar", "productivity",
                      ["read appointments", "book", "move", "cancel"],
                      required_env=["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"],
                      optional_env=["GOOGLE_CALENDAR_ID"], icon="calendar"),
    LocalCalendarIntegration("local_calendar", "Local calendar (fallback)", "productivity",
                             ["read appointments", "book", "move", "cancel", "writes .ics files"],
                             icon="calendar"),
    SmtpImapIntegration("email", "E-Mail (IMAP / SMTP)", "communication",
                        ["inbox and unread", "search", "read full mail", "draft", "send (approval-gated)"],
                        required_env=["EMAIL_USER", "EMAIL_PASSWORD"],
                        optional_env=["EMAIL_IMAP_HOST", "EMAIL_IMAP_PORT", "EMAIL_SMTP_HOST",
                                      "EMAIL_SMTP_PORT", "EMAIL_SENDER_NAME"], icon="mail"),
    SmtpImapIntegration("email_privat", "E-Mail Firma privat (IMAP / SMTP)", "communication",
                        ["inbox and unread", "search", "read full mail", "draft", "send (approval-gated)"],
                        required_env=["EMAIL_PRIVAT_USER", "EMAIL_PRIVAT_PASSWORD"],
                        optional_env=["EMAIL_PRIVAT_IMAP_HOST", "EMAIL_PRIVAT_IMAP_PORT", "EMAIL_PRIVAT_SMTP_HOST",
                                      "EMAIL_PRIVAT_SMTP_PORT", "EMAIL_PRIVAT_SENDER_NAME"], icon="mail", slug="privat"),
    SmtpImapIntegration("email_rechnungen", "E-Mail Rechnungen (IMAP / SMTP)", "communication",
                        ["inbox and unread", "search", "read full mail", "draft", "send (approval-gated)"],
                        required_env=["EMAIL_RECHNUNGEN_USER", "EMAIL_RECHNUNGEN_PASSWORD"],
                        optional_env=["EMAIL_RECHNUNGEN_IMAP_HOST", "EMAIL_RECHNUNGEN_IMAP_PORT", "EMAIL_RECHNUNGEN_SMTP_HOST",
                                      "EMAIL_RECHNUNGEN_SMTP_PORT", "EMAIL_RECHNUNGEN_SENDER_NAME"], icon="mail", slug="rechnungen"),
    VoiceIntegration("voice", "Voice (speech to text · text to speech)", "ai",
                     ["browser transcription", "spoken answers"],
                     any_env=["JARVIS_CC_STT_URL", "JARVIS_CC_TTS_URL"],
                     optional_env=["JARVIS_CC_STT_MODEL", "JARVIS_CC_TTS_MODEL", "JARVIS_CC_TTS_VOICE",
                                   "JARVIS_CC_STT_LANGUAGE"], icon="mic"),
    ComposioIntegration("composio", "Composio", "integrations",
                        ["hosted OAuth for a few hundred services", "tool catalogue", "execute a tool"],
                        required_env=["COMPOSIO_API_KEY"],
                        optional_env=["COMPOSIO_USER_ID", "COMPOSIO_BASE_URL"], icon="plug"),
    DesktopChannelIntegration("whatsapp", "WhatsApp (through the paired PC)", "communication",
                              ["voice notes in your own voice", "text messages"],
                              action="whatsapp", icon="message-circle"),
    IntegrationAdapter("ionos", "IONOS", "hosting", ["dns (planned)", "hosting (planned)"],
                       required_env=["IONOS_API_KEY"], icon="globe"),
]


class IntegrationRegistry:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus
        self._adapters: dict[str, IntegrationAdapter] = {}
        self._health: dict[str, dict] = {}
        for a in DEFAULT_ADAPTERS:
            self.register(a)

    def register(self, adapter: IntegrationAdapter) -> None:
        # Adapters that read the server's own state (e.g. which desktop offers
        # which channel) get this registry's database handed to them. Always
        # reassign: DEFAULT_ADAPTERS is module-level, so a second app in the same
        # process would otherwise inherit the first one's closed connection.
        if hasattr(adapter, "db"):
            adapter.db = self.db
        self._adapters[adapter.id] = adapter
        self._health.setdefault(adapter.id, {"status": "not_configured" if not adapter.configured() else "unknown",
                                             "detail": "not checked yet", "checked_at": None})

    def get(self, integration_id: str) -> IntegrationAdapter | None:
        return self._adapters.get(integration_id)

    def public(self, adapter: IntegrationAdapter) -> dict:
        h = self._health.get(adapter.id, {})
        status = h.get("status") or ("unknown" if adapter.configured() else "not_configured")
        return {
            "id": adapter.id, "name": adapter.name, "kind": adapter.kind, "icon": adapter.icon,
            "capabilities": adapter.capabilities, "configured": adapter.configured(),
            "status": status, "detail": h.get("detail", ""), "last_checked_at": h.get("checked_at"),
            "config_state": adapter.config_state(), "required_env": adapter.required_env,
            "any_env": adapter.any_env, "optional_env": adapter.optional_env,
        }

    # Was der Betrieb nicht braucht, bleibt im Hintergrund lauffähig, steht aber nicht in der Liste:
    # bezahlte Direktzugänge (Denken läuft über OpenRouter), die ungenutzte Fernsteuerung, IONOS ohne
    # Funktion und die Google-Calendar-Karte (der Kalender läuft über die n8n-Brücke).
    HIDDEN_DEFAULT = "anthropic,openai,gemini,control_plane,google_calendar,ionos"

    def hidden(self) -> set[str]:
        raw = os.environ.get("JARVIS_CC_HIDDEN_INTEGRATIONS", self.HIDDEN_DEFAULT)
        return {x.strip() for x in raw.split(",") if x.strip()}

    def list(self) -> list[dict]:
        hide = self.hidden()
        return [self.public(a) for a in self._adapters.values() if a.id not in hide]

    def summary(self) -> dict:
        items = self.list()
        return {"total": len(items), "connected": sum(1 for i in items if i["status"] == "healthy"),
                "configured": sum(1 for i in items if i["configured"]),
                "degraded": sum(1 for i in items if i["status"] in ("degraded", "offline"))}

    async def check(self, integration_id: str) -> dict:
        adapter = self._adapters.get(integration_id)
        if not adapter:
            raise KeyError(integration_id)
        try:
            result = await asyncio.wait_for(adapter.check(), timeout=15)
        except asyncio.TimeoutError:
            result = {"status": "offline", "detail": "health check timed out"}
        except Exception as e:  # noqa: BLE001
            result = {"status": "degraded", "detail": str(e)[:200]}
        result["checked_at"] = now_iso()
        previous = self._health.get(adapter.id, {}).get("status")
        self._health[adapter.id] = result
        self.db.upsert("integrations", {
            "id": adapter.id, "name": adapter.name, "kind": adapter.kind, "status": result["status"],
            "capabilities": dumps(adapter.capabilities), "last_sync_at": result["checked_at"],
            "health": dumps(result), "config_state": dumps(adapter.config_state()), "updated_at": now_iso(),
        })
        pub = self.public(adapter)
        self.bus.publish("integration.status", pub)
        pub["changed"] = previous is not None and previous != result["status"]
        return pub

    async def check_all(self) -> list[dict]:
        return list(await asyncio.gather(*[self.check(i) for i in self._adapters]))
