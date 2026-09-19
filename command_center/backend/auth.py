"""
Authentication and authorisation.

Two kinds of principal:
  * a *user* logged in through the browser — HttpOnly session cookie + CSRF
    double-submit token, role-based (viewer < operator < admin);
  * a *machine token* — for the desktop client and other automations. Sent as
    `X-Jarvis-Token` (the contract core/control_plane.py already speaks) or as a
    Bearer header. Tokens are stored hashed; the plaintext is shown exactly once.

Passwords use scrypt from the standard library — no extra dependency, and the
parameters are recorded in the hash so they can be raised later without a
migration.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import Database, new_id, now_iso
from .logbook import LogBook

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
ROLES = tuple(ROLE_RANK)

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


@dataclass
class Principal:
    kind: str                 # "user" | "token"
    id: str
    name: str
    role: str
    actor: str
    session_id: str | None = None

    def has_role(self, minimum: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(minimum, 99)

    def public(self) -> dict:
        return {"kind": self.kind, "id": self.id, "name": self.name, "role": self.role,
                "actor": self.actor}


# ── password hashing ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── rate limiting ────────────────────────────────────────────────────────────

class RateLimiter:
    """Sliding-window limiter keyed by an arbitrary string (IP, user, route)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and q[0] < now - window_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


# ── service ──────────────────────────────────────────────────────────────────

class AuthService:
    def __init__(self, db: Database, logbook: LogBook, session_ttl_hours: int = 24 * 7):
        self.db = db
        self.log = logbook
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self.limiter = RateLimiter()

    # -- users --
    def user_count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM users") or 0)

    def bootstrap_admin(self, username: str, password: str) -> tuple[str, str | None]:
        """Create the first admin if no user exists. Returns (username, generated_password_or_None)."""
        if self.user_count() > 0:
            return "", None
        generated = None
        if not password:
            password = secrets.token_urlsafe(12)
            generated = password
        username = username or "admin"
        self.create_user(username, password, role="admin", display_name="Administrator")
        self.log.info("auth", f"Bootstrap admin user '{username}' created")
        return username, generated

    def create_user(self, username: str, password: str, role: str = "viewer",
                    display_name: str = "") -> dict:
        username = username.strip().lower()
        if not username or len(username) > 64:
            raise ValueError("invalid username")
        if role not in ROLES:
            raise ValueError("invalid role")
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        row = {"id": new_id("usr"), "username": username, "password_hash": hash_password(password),
               "role": role, "display_name": display_name or username, "disabled": 0,
               "created_at": now_iso(), "last_login_at": None}
        self.db.insert("users", row)
        return self.public_user(row)

    def list_users(self) -> list[dict]:
        return [self.public_user(r) for r in self.db.fetchall("SELECT * FROM users ORDER BY created_at")]

    def get_user(self, user_id: str) -> dict | None:
        return self.db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))

    def update_user(self, user_id: str, *, role: str | None = None, disabled: bool | None = None,
                    display_name: str | None = None, password: str | None = None) -> dict | None:
        values: dict = {}
        if role is not None:
            if role not in ROLES:
                raise ValueError("invalid role")
            values["role"] = role
        if disabled is not None:
            values["disabled"] = 1 if disabled else 0
        if display_name is not None:
            values["display_name"] = display_name
        if password is not None:
            if len(password) < 8:
                raise ValueError("password must be at least 8 characters")
            values["password_hash"] = hash_password(password)
        if values:
            self.db.update("users", user_id, values)
        if disabled:
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        row = self.get_user(user_id)
        return self.public_user(row) if row else None

    def change_password(self, user_id: str, current: str, new: str) -> bool:
        row = self.get_user(user_id)
        if not row or not verify_password(current, row["password_hash"]):
            return False
        self.update_user(user_id, password=new)
        return True

    @staticmethod
    def public_user(row: dict) -> dict:
        return {"id": row["id"], "username": row["username"], "role": row["role"],
                "display_name": row["display_name"], "disabled": bool(row["disabled"]),
                "created_at": row["created_at"], "last_login_at": row["last_login_at"]}

    # -- login / sessions --
    def verify_login(self, username: str, password: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM users WHERE username=?", (username.strip().lower(),))
        if not row or row["disabled"]:
            # Burn the same time as a real check so usernames cannot be enumerated.
            verify_password(password, hash_password("x" * 8))
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        self.db.update("users", row["id"], {"last_login_at": now_iso()})
        return row

    def create_session(self, user_id: str, *, user_agent: str = "", ip: str = "") -> tuple[str, str]:
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        self.db.insert("sessions", {
            "id": _hash_token(raw), "user_id": user_id, "csrf_token": csrf,
            "created_at": now_iso(), "expires_at": (now + self.session_ttl).isoformat(),
            "last_seen_at": now_iso(), "user_agent": user_agent[:300], "ip": ip[:64],
        })
        return raw, csrf

    def resolve_session(self, raw: str) -> tuple[Principal, str] | None:
        if not raw:
            return None
        sid = _hash_token(raw)
        row = self.db.fetchone("SELECT * FROM sessions WHERE id=?", (sid,))
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            self.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            return None
        user = self.get_user(row["user_id"])
        if not user or user["disabled"]:
            return None
        # Touch at most once a minute to keep writes cheap.
        try:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(
                    row["last_seen_at"].replace("Z", "+00:00"))).total_seconds() > 60:
                self.db.update("sessions", sid, {"last_seen_at": now_iso()})
        except Exception:
            pass
        principal = Principal(kind="user", id=user["id"], name=user["display_name"] or user["username"],
                              role=user["role"], actor=user["username"], session_id=sid)
        return principal, row["csrf_token"]

    def revoke_session(self, raw: str) -> None:
        if raw:
            self.db.execute("DELETE FROM sessions WHERE id=?", (_hash_token(raw),))

    def purge_expired_sessions(self) -> int:
        cur = self.db.execute("DELETE FROM sessions WHERE expires_at < ?",
                              (datetime.now(timezone.utc).isoformat(),))
        return cur.rowcount

    # -- machine tokens --
    def create_api_token(self, *, name: str, actor: str, role: str, created_by: str) -> tuple[dict, str]:
        if role not in ROLES:
            raise ValueError("invalid role")
        raw = "jcc_" + secrets.token_urlsafe(36)
        row = {"id": new_id("tok"), "name": name.strip()[:80] or "token", "token_hash": _hash_token(raw),
               "actor": actor.strip()[:80] or "client", "role": role, "created_by": created_by,
               "created_at": now_iso(), "last_used_at": None, "revoked_at": None}
        self.db.insert("api_tokens", row)
        return self.public_token(row), raw

    def resolve_api_token(self, raw: str) -> Principal | None:
        if not raw:
            return None
        row = self.db.fetchone("SELECT * FROM api_tokens WHERE token_hash=? AND revoked_at IS NULL",
                               (_hash_token(raw),))
        if not row:
            return None
        self.db.update("api_tokens", row["id"], {"last_used_at": now_iso()})
        return Principal(kind="token", id=row["id"], name=row["name"], role=row["role"],
                         actor=row["actor"])

    def list_api_tokens(self) -> list[dict]:
        return [self.public_token(r) for r in self.db.fetchall(
            "SELECT * FROM api_tokens ORDER BY created_at DESC")]

    def revoke_api_token(self, token_id: str) -> bool:
        return self.db.update("api_tokens", token_id, {"revoked_at": now_iso()}) > 0

    @staticmethod
    def public_token(row: dict) -> dict:
        return {"id": row["id"], "name": row["name"], "actor": row["actor"], "role": row["role"],
                "created_by": row["created_by"], "created_at": row["created_at"],
                "last_used_at": row["last_used_at"], "revoked": row["revoked_at"] is not None}
