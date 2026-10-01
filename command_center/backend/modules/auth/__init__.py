"""Auth module: login/logout/session, users (admin), machine tokens."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import CSRF_COOKIE, SESSION_COOKIE, AppState, client_ip, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = "viewer"
    display_name: str = ""


class UserPatch(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    actor: str = Field(min_length=1, max_length=80)
    role: str = "operator"


def _set_cookies(request: Request, response: Response, state: AppState, raw: str, csrf: str) -> None:
    from ...app import cookie_kwargs
    kw = cookie_kwargs(request, state.settings)
    response.set_cookie(SESSION_COOKIE, raw, **kw)
    response.set_cookie(CSRF_COOKIE, csrf, **{**kw, "httponly": False})


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, state: AppState = Depends(get_state)):
    ip = client_ip(request, state.settings.trust_proxy) or "unknown"
    if not state.auth.limiter.allow(f"login:{ip}", state.settings.login_rate_limit_per_minute):
        state.log.warning("auth", f"Login rate limit hit from {ip}")
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in a minute.")
    user = state.auth.verify_login(body.username, body.password)
    if not user:
        state.log.audit(actor_type="user", actor_id=body.username.lower(), action="auth.login", status="denied",
                        meta={"ip": ip})
        raise HTTPException(status_code=401, detail="Invalid username or password")
    raw, csrf = state.auth.create_session(user["id"], user_agent=request.headers.get("user-agent", ""), ip=ip)
    _set_cookies(request, response, state, raw, csrf)
    state.auth.limiter.reset(f"login:{ip}")
    state.log.audit(actor_type="user", actor_id=user["username"], action="auth.login", status="ok", meta={"ip": ip})
    principal = Principal(kind="user", id=user["id"], name=user["display_name"] or user["username"],
                          role=user["role"], actor=user["username"])
    return {"user": principal.public(), "csrf_token": csrf}


@router.post("/pairing")
async def create_browser_pairing(request: Request, state: AppState = Depends(get_state),
                                 principal: Principal = Depends(current_principal)):
    """Create a short-lived, one-time link that logs the same user into another browser.

    The plaintext secret exists only in the returned URL. The database stores its SHA-256 hash.
    It expires after five minutes and is deleted on first use.
    """
    if principal.kind != "user":
        raise HTTPException(status_code=400, detail="QR pairing is only available for browser users")
    state.db.execute("CREATE TABLE IF NOT EXISTS browser_pairings ("
                     "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, "
                     "expires_at TEXT NOT NULL, created_by TEXT NOT NULL)")
    now = datetime.now(timezone.utc)
    state.db.execute("DELETE FROM browser_pairings WHERE expires_at < ?", (now.isoformat(),))
    secret = secrets.token_urlsafe(32)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    expires = now + timedelta(minutes=5)
    state.db.insert("browser_pairings", {"id": digest, "user_id": principal.id, "created_at": now_iso(),
                                         "expires_at": expires.isoformat(), "created_by": principal.actor})
    base = str(request.base_url).rstrip("/")
    pair_url = f"{base}/api/auth/pair/{secret}"
    state.log.audit(actor_type="user", actor_id=principal.actor, action="auth.pairing.create",
                    status="ok", meta={"expires_minutes": 5})
    return {"pair_url": pair_url, "expires_at": expires.isoformat()}


@router.get("/pair/{secret}")
async def consume_browser_pairing(secret: str, request: Request, state: AppState = Depends(get_state)):
    """Consume a one-time QR link and establish the normal HttpOnly browser session."""
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    row = state.db.fetchone("SELECT * FROM browser_pairings WHERE id=?", (digest,))
    now = datetime.now(timezone.utc)
    if not row or datetime.fromisoformat(row["expires_at"]) < now:
        if row:
            state.db.execute("DELETE FROM browser_pairings WHERE id=?", (digest,))
        return HTMLResponse("<!doctype html><meta name='viewport' content='width=device-width'><title>MIA</title>"
                            "<body style='background:#05070b;color:#eee;font-family:system-ui;display:grid;place-items:center;height:100vh'>"
                            "<div style='text-align:center'><h2>QR-Code abgelaufen</h2><p>Erzeuge im MIA Command Center einen neuen Code.</p></div></body>",
                            status_code=410)
    # One-time means consume before creating the session. A refresh cannot reuse it.
    state.db.execute("DELETE FROM browser_pairings WHERE id=?", (digest,))
    raw, csrf = state.auth.create_session(row["user_id"], user_agent=request.headers.get("user-agent", ""),
                                          ip=client_ip(request, state.settings.trust_proxy) or "unknown")
    response = RedirectResponse(url="/", status_code=303)
    _set_cookies(request, response, state, raw, csrf)
    user = state.auth.get_user(row["user_id"])
    state.log.audit(actor_type="user", actor_id=(user or {}).get("username", row["created_by"]),
                    action="auth.pairing.consume", status="ok")
    return response


@router.post("/logout")
async def logout(request: Request, response: Response, state: AppState = Depends(get_state),
                 principal: Principal = Depends(current_principal)):
    state.auth.revoke_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="auth.logout", status="ok")
    return {"ok": True}


@router.get("/me")
async def me(request: Request, state: AppState = Depends(get_state),
             principal: Principal = Depends(current_principal)):
    from ...auth import ROLE_RANK
    modules = state.services["modules"].navigation(ROLE_RANK.get(principal.role, 0))
    return {"user": principal.public(), "csrf_token": getattr(request.state, "csrf_token", ""),
            "modules": modules, "version": state.version}


@router.post("/password")
async def change_password(body: PasswordBody, state: AppState = Depends(get_state),
                          principal: Principal = Depends(current_principal)):
    if principal.kind != "user":
        raise HTTPException(status_code=400, detail="Only users have passwords")
    if not state.auth.change_password(principal.id, body.current_password, body.new_password):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="auth.password_change", status="ok")
    return {"ok": True}


# ── users (admin) ────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(state: AppState = Depends(get_state), _: Principal = Depends(require_role("admin"))):
    return {"users": state.auth.list_users()}


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    try:
        user = state.auth.create_user(body.username, body.password, role=body.role, display_name=body.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=409, detail="Username already exists")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="user.create", target=user["username"],
                    status="ok", meta={"role": body.role})
    return {"user": user}


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("admin"))):
    if user_id == principal.id and (body.disabled or (body.role and body.role != "admin")):
        raise HTTPException(status_code=400, detail="You cannot demote or disable yourself")
    try:
        user = state.auth.update_user(user_id, role=body.role, disabled=body.disabled,
                                      display_name=body.display_name, password=body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="user.update", target=user["username"],
                    status="ok", meta={k: v for k, v in body.model_dump().items() if v is not None and k != "password"})
    return {"user": user}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    if user_id == principal.id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen.")
    user = state.auth.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    state.auth.delete_user(user_id)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="user.delete", target=user["username"],
                    status="ok", meta={"role": user["role"]})
    return {"ok": True}


# ── machine tokens (admin) ───────────────────────────────────────────────
@router.get("/tokens")
async def list_tokens(state: AppState = Depends(get_state), _: Principal = Depends(require_role("admin"))):
    return {"tokens": state.auth.list_api_tokens()}


@router.post("/tokens", status_code=201)
async def create_token(body: TokenCreate, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("admin"))):
    try:
        row, raw = state.auth.create_api_token(name=body.name, actor=body.actor, role=body.role,
                                               created_by=principal.actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="token.create", target=body.actor,
                    status="ok", meta={"token_id": row["id"], "role": body.role})
    return {"token": row, "secret": raw}


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, purge: bool = False, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("admin"))):
    """Sperren (bleibt in der Liste, als gesperrt) — oder mit purge=true endgültig löschen."""
    if not state.auth.revoke_api_token(token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    if purge:
        state.auth.purge_api_token(token_id)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="token.purge" if purge else "token.revoke",
                    target=token_id, status="ok")
    return {"ok": True}


MODULE = ModuleSpec(id="auth", title="Anmeldung", router=router, nav=False, order=0)
