"""Remote approve/reject for core/confirm.py's confirmation gate.

The gate was built for the desktop HUD (core/confirm.py's `bind()`), which is
fine when someone is looking at the screen. On a headless server there is no
screen: main.py still binds the Qt callbacks (they exist, just off-screen),
so a confirmation currently sits there until TIMEOUT_SECONDS and is silently
abandoned — nobody could ever have answered it. This exposes the same
request()/resolve() gate over the dashboard so it can actually be approved or
rejected from the phone or a browser, wherever the model is talking to.
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from core import confirm as confirm_gate


def install_confirm(app, authenticate) -> None:
    @app.get("/api/confirm/pending")
    async def confirm_pending(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        info = confirm_gate.pending_info()
        return JSONResponse(info or {"pending": False},
                             headers={"Cache-Control": "no-store"})

    @app.post("/api/confirm/decide")
    async def confirm_decide(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            body = await req.json()
        except Exception:
            body = {}
        confirm_gate.resolve(bool(body.get("accepted")))
        return JSONResponse({"ok": True})
