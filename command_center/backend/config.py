"""
Settings — every knob comes from the environment (or a .env file loaded by the
process manager). Nothing here is a secret; secrets are *read* from env at use
time and never serialised into API responses.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # ── paths ────────────────────────────────────────────────────────────
    data_dir: Path
    db_path: Path
    workspace_dir: Path
    static_dir: Path

    # ── http ─────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080
    root_path: str = ""
    allowed_origins: list[str] = field(default_factory=list)
    secure_cookies: str = "auto"          # auto | true | false
    trust_proxy: bool = True
    session_ttl_hours: int = 24 * 7
    login_rate_limit_per_minute: int = 8
    api_rate_limit_per_minute: int = 900

    # ── bootstrap admin ──────────────────────────────────────────────────
    admin_user: str = ""
    admin_password: str = ""

    # ── master agent ─────────────────────────────────────────────────────
    master_agent_mode: str = "auto"       # auto | local | remote | none
    ai_provider: str = ""                 # anthropic | openai | gemini | local | ""
    ai_model: str = ""
    max_agent_steps: int = 16
    max_delegation_depth: int = 2
    approval_timeout_minutes: int = 30
    agent_roster_path: Path | None = None

    # ── upstream control plane (remote master agent) ─────────────────────
    gateway_url: str = ""
    gateway_actor: str = "command-center"

    # ── capabilities that are off unless explicitly enabled ──────────────
    allow_terminal: bool = False
    allow_docker_actions: bool = False
    allow_service_restart: bool = False
    docker_socket: str = "/var/run/docker.sock"
    monitored_services: list[str] = field(default_factory=list)

    # ── retention ────────────────────────────────────────────────────────
    log_retention_rows: int = 50_000
    metrics_history_points: int = 720     # at 5s sampling ≈ 1 hour
    max_upload_mb: int = 200

    @property
    def secret_key(self) -> bytes:
        return _load_or_create_secret(self.data_dir)

    # Provider secrets are read on demand and never stored on the settings
    # object, so a debug dump of settings can never leak them.
    @staticmethod
    def provider_secret(name: str) -> str:
        return _env(name)

    def configured_ai_provider(self) -> str:
        """Which AI provider is usable right now, based purely on env."""
        explicit = self.ai_provider.lower()
        candidates = {
            "anthropic": bool(_env("ANTHROPIC_API_KEY")),
            "openai": bool(_env("OPENAI_API_KEY")),
            "gemini": bool(_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")),
            "local": bool(_env("LOCAL_LLM_URL")),
        }
        if explicit:
            return explicit if candidates.get(explicit) else ""
        for name in ("anthropic", "openai", "gemini", "local"):
            if candidates[name]:
                return name
        return ""

    def resolved_master_mode(self) -> str:
        mode = self.master_agent_mode.lower()
        if mode in ("local", "remote", "none"):
            if mode == "local" and not self.configured_ai_provider():
                return "none"
            if mode == "remote" and not _env("JARVIS_GATEWAY_TOKEN"):
                return "none"
            return mode
        if self.configured_ai_provider():
            return "local"
        if _env("JARVIS_GATEWAY_TOKEN") and self.gateway_url:
            return "remote"
        return "none"


def _load_or_create_secret(data_dir: Path) -> bytes:
    env_secret = _env("JARVIS_CC_SECRET_KEY")
    if env_secret:
        return env_secret.encode("utf-8")
    path = data_dir / "secret.key"
    try:
        if path.exists():
            return path.read_bytes().strip()
        data_dir.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48).encode("utf-8")
        path.write_bytes(value)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value
    except OSError:
        # Read-only filesystem: fall back to a process-lifetime secret. Sessions
        # will not survive a restart, which is the safe failure mode.
        return secrets.token_urlsafe(48).encode("utf-8")


_settings: Settings | None = None


def load_settings() -> Settings:
    data_dir = Path(_env("JARVIS_CC_DATA_DIR") or (REPO_ROOT / "data" / "command_center")).resolve()
    db_path = Path(_env("JARVIS_CC_DB_PATH") or (data_dir / "jarvis.db"))
    workspace_dir = Path(_env("JARVIS_CC_WORKSPACE_DIR") or (data_dir / "workspace")).resolve()
    static_dir = Path(_env("JARVIS_CC_STATIC_DIR") or (Path(__file__).resolve().parent / "static"))

    roster_env = _env("JARVIS_CC_AGENT_ROSTER")
    roster_path: Path | None = None
    if roster_env:
        roster_path = Path(roster_env)
    else:
        candidate = REPO_ROOT / "config" / "command_center" / "agents.json"
        roster_path = candidate if candidate.exists() else None

    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        workspace_dir=workspace_dir,
        static_dir=static_dir,
        host=_env("JARVIS_CC_HOST", "0.0.0.0"),
        port=_env_int("JARVIS_CC_PORT", 8080),
        root_path=_env("JARVIS_CC_ROOT_PATH"),
        allowed_origins=_env_list("JARVIS_CC_ALLOWED_ORIGINS"),
        secure_cookies=_env("JARVIS_CC_SECURE_COOKIES", "auto").lower() or "auto",
        trust_proxy=_env_bool("JARVIS_CC_TRUST_PROXY", True),
        session_ttl_hours=_env_int("JARVIS_CC_SESSION_TTL_HOURS", 24 * 7),
        login_rate_limit_per_minute=_env_int("JARVIS_CC_LOGIN_RATE_LIMIT", 8),
        api_rate_limit_per_minute=_env_int("JARVIS_CC_API_RATE_LIMIT", 900),
        admin_user=_env("JARVIS_CC_ADMIN_USER"),
        admin_password=_env("JARVIS_CC_ADMIN_PASSWORD"),
        master_agent_mode=_env("JARVIS_MASTER_AGENT_MODE", "auto"),
        ai_provider=_env("JARVIS_AI_PROVIDER"),
        ai_model=_env("JARVIS_AI_MODEL"),
        max_agent_steps=_env_int("JARVIS_CC_MAX_AGENT_STEPS", 16),
        max_delegation_depth=_env_int("JARVIS_CC_MAX_DELEGATION_DEPTH", 2),
        approval_timeout_minutes=_env_int("JARVIS_CC_APPROVAL_TIMEOUT_MIN", 30),
        agent_roster_path=roster_path,
        gateway_url=_env("JARVIS_GATEWAY_URL"),
        gateway_actor=_env("JARVIS_GATEWAY_ACTOR", "command-center"),
        allow_terminal=_env_bool("JARVIS_CC_ALLOW_TERMINAL", False),
        allow_docker_actions=_env_bool("JARVIS_CC_ALLOW_DOCKER_ACTIONS", False),
        allow_service_restart=_env_bool("JARVIS_CC_ALLOW_SERVICE_RESTART", False),
        docker_socket=_env("JARVIS_CC_DOCKER_SOCKET", "/var/run/docker.sock"),
        monitored_services=_env_list("JARVIS_CC_MONITORED_SERVICES"),
        log_retention_rows=_env_int("JARVIS_CC_LOG_RETENTION_ROWS", 50_000),
        metrics_history_points=_env_int("JARVIS_CC_METRICS_HISTORY_POINTS", 720),
        max_upload_mb=_env_int("JARVIS_CC_MAX_UPLOAD_MB", 200),
    )


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings() -> None:
    """Testing hook: re-read the environment on next access."""
    global _settings
    _settings = None
