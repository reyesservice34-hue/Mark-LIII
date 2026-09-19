"""
JARVIS Command Center — the server-side operating dashboard for MARK LIII.

Layout:
  command_center/backend   FastAPI application (API gateway, orchestrator, registries)
  command_center/frontend  Vite + React dashboard (built into backend/static at deploy time)

The desktop app in this repository (main.py) stays a *client* of the server:
core/control_plane.py speaks the /v1/commands contract, which the backend here
implements in modules/gateway, so a desktop can be paired with the server by
pointing its gateway URL at this service.
"""
