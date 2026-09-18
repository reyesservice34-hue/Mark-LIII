"""
Integration registry.

Each adapter answers two questions honestly: *is it configured* (are the
required environment variables present — values are never returned) and *is
it healthy* (a real request, when the service offers a cheap one). Nothing is
mocked: an integration with no credentials is reported as NOT CONNECTED.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..db import Database, dumps, loads, now_iso
from ..events import EventBus

STATUSES = ("healthy", "degraded", "offline", "not_configured", "unknown")


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
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "LOCAL_LLM_URL not set"}
        from ..ai import build_provider
        p = build_provider("local")
        return await p.health() if p else {"status": "offline", "detail": "provider init failed"}


class N8nIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "N8N_BASE_URL / N8N_API_KEY not set"}
        base = os.environ["N8N_BASE_URL"].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(f"{base}/api/v1/workflows", params={"limit": 1},
                                headers={"X-N8N-API-KEY": os.environ["N8N_API_KEY"]})
            if r.status_code == 401:
                return {"status": "offline", "detail": "n8n rejected the API key"}
            if r.status_code >= 400:
                return {"status": "degraded", "detail": f"n8n HTTP {r.status_code}"}
            return {"status": "healthy", "detail": "n8n API reachable"}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach n8n: {e.__class__.__name__}"}


class GitHubIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "GITHUB_TOKEN not set"}
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get("https://api.github.com/user", headers={
                    "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
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


class SmtpImapIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        from ..services.email_service import EmailService
        svc = EmailService()
        if not svc.configured():
            return {"status": "not_configured", "detail": svc.unavailable_reason()}
        return await svc.health()


class VoiceIntegration(IntegrationAdapter):
    async def check(self) -> dict:
        from ..services.voice_service import VoiceService
        return await VoiceService().health()


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
    VoiceIntegration("voice", "Voice (speech to text · text to speech)", "ai",
                     ["browser transcription", "spoken answers"],
                     any_env=["JARVIS_CC_STT_URL", "JARVIS_CC_TTS_URL"],
                     optional_env=["JARVIS_CC_STT_MODEL", "JARVIS_CC_TTS_MODEL", "JARVIS_CC_TTS_VOICE",
                                   "JARVIS_CC_STT_LANGUAGE"], icon="mic"),
    IntegrationAdapter("whatsapp", "WhatsApp", "communication", ["voice notes (planned)"],
                       required_env=["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID"], icon="message-circle"),
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

    def list(self) -> list[dict]:
        return [self.public(a) for a in self._adapters.values()]

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
