"""Integration registry API."""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# Zugangsdaten, die im Dashboard eingegeben werden, liegen hier (nur für den Server lesbar, Modus 0600).
# Die .env auf dem Host kann der Container nicht ändern — und soll es auch nicht.
SECRETS_FILE = Path(os.environ.get("JARVIS_CC_DATA_DIR", "/data")) / "secrets.env"
_KEY = re.compile(r"[A-Z][A-Z0-9_]{1,60}")


def _read_saved() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if _KEY.fullmatch(k.strip()):
                    out[k.strip()] = v
    except OSError:
        pass
    return out


def load_saved_secrets() -> int:
    """Beim Start: früher im Dashboard eingegebene Zugangsdaten wieder in die Umgebung übernehmen."""
    saved = _read_saved()
    os.environ.update(saved)
    return len(saved)


def _write_saved(values: dict[str, str]) -> None:
    cur = _read_saved()
    cur.update(values)
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(f"{k}={v}" for k, v in cur.items()) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(SECRETS_FILE)


load_saved_secrets()


class ConfigBody(BaseModel):
    values: dict[str, str]


@router.get("")
async def list_integrations(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"integrations": state.integrations.list(), "summary": state.integrations.summary()}


@router.post("/{integration_id}/check")
async def check(integration_id: str, state: AppState = Depends(get_state),
                principal: Principal = Depends(require_role("operator"))):
    try:
        result = await state.integrations.check(integration_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Integration not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="integration.check", target=integration_id,
                    status=result["status"], result=result.get("detail", ""))
    return {"integration": result}


@router.put("/{integration_id}/config")
async def set_config(integration_id: str, body: ConfigBody, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("admin"))):
    """Zugangsdaten eines Dienstes eintragen. Nur Administratoren, nur die Variablen, die dieser Dienst kennt.

    Leere Felder bleiben unverändert. Gespeicherte Werte werden nie zurückgegeben — die Antwort sagt nur, ob der
    Dienst danach wirklich erreichbar ist.
    """
    adapter = state.integrations.get(integration_id)
    if not adapter:
        raise HTTPException(status_code=404, detail="Integration not found")
    allowed = set(adapter.required_env) | set(adapter.any_env) | set(adapter.optional_env)
    clean: dict[str, str] = {}
    for k, v in body.values.items():
        if k not in allowed:
            raise HTTPException(status_code=400, detail=f"{k} gehört nicht zu diesem Dienst.")
        v = v.strip()
        if not v:
            continue
        if len(v) > 600 or any(c in v for c in "\r\n\x00"):
            raise HTTPException(status_code=400, detail=f"{k}: ungültiger Wert.")
        clean[k] = v
    if not clean:
        raise HTTPException(status_code=400, detail="Es wurde nichts eingegeben.")
    _write_saved(clean)
    os.environ.update(clean)
    if integration_id.startswith("email"):
        state.services["email"].reload()          # neues Postfach sofort für Jarvis sichtbar
    result = await state.integrations.check(integration_id)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="integration.configure",
                    target=integration_id, status=result["status"], meta={"keys": sorted(clean)})
    return {"saved": sorted(clean), "check": result, "integration": state.integrations.public(adapter)}


@router.post("/check-all")
async def check_all(state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    return {"integrations": await state.integrations.check_all()}


MODULE = ModuleSpec(
    id="integrations", title="Integrationen", router=router, icon="plug", path="/integrations", order=90,
    description="Verbundene Dienste",
)
