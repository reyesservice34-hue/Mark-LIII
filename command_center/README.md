# JARVIS Command Center

The server-side operating dashboard for MARK LIII: one authenticated web
interface between you, the Master Agent, specialist agents, automations, tools,
integrations and the server itself.

```
Browser ──HTTPS──▶ reverse proxy ──▶ FastAPI backend (command_center/backend)
                                       ├─ auth (sessions, CSRF, roles, machine tokens)
                                       ├─ event bus → SSE / WebSocket
                                       ├─ Master Agent runtime ─▶ AI provider (Anthropic · OpenAI-compatible · Gemini · local)
                                       │     └─ tool registry ─▶ approval gate ─▶ audit trail
                                       ├─ agent registry (roster = data)
                                       ├─ integrations / workflow adapters (n8n, …)
                                       └─ SQLite (/data/jarvis.db) + workspace (/data/workspace)
Desktop (main.py) ──X-Jarvis-Token──▶ /v1/commands  (same backend, same identity)
```

## Run it

### Docker (recommended on the server)

```bash
cp command_center/.env.example command_center/.env      # edit: admin password, AI key, capabilities
docker compose -f docker-compose.command-center.yml up -d --build
curl http://127.0.0.1:8080/api/health
```

Put your existing reverse proxy in front of `127.0.0.1:8080` with TLS. The app
honours `X-Forwarded-Proto` / `X-Forwarded-For`, marks cookies `Secure` behind
HTTPS automatically and can be mounted under a path prefix with
`JARVIS_CC_ROOT_PATH`. Nothing in the existing stack is touched: the compose
file is standalone and only mounts the docker socket read-only (optional).

### Without Docker

```bash
pip install -r command_center/requirements.txt
(cd command_center/frontend && npm install && npm run build)   # → command_center/backend/static
export JARVIS_CC_ADMIN_USER=admin JARVIS_CC_ADMIN_PASSWORD='…' ANTHROPIC_API_KEY='…'
python -m command_center.backend.main                          # http://localhost:8080
```

Frontend development: `npm run dev` in `command_center/frontend` proxies `/api`
and `/v1` to the backend on port 8080.

### First login

If no user exists the backend creates an admin from
`JARVIS_CC_ADMIN_USER` / `JARVIS_CC_ADMIN_PASSWORD`. Without a password it
generates one and prints it once to the container log.

## Master Agent modes

| mode     | when                                                     | what answers                                   |
|----------|----------------------------------------------------------|------------------------------------------------|
| `local`  | an AI key is set (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` or `LOCAL_LLM_URL`) | the built-in orchestrator: tool loop, delegation to specialists, approval gate, tasks |
| `remote` | `JARVIS_GATEWAY_URL` + `JARVIS_GATEWAY_TOKEN` are set and no AI key | the upstream JARVIS control plane (the contract in `core/control_plane.py`); answers are relayed verbatim |
| `none`   | neither                                                  | nothing — the chat shows exactly that          |

`JARVIS_MASTER_AGENT_MODE` forces a mode. `JARVIS_AI_PROVIDER` / `JARVIS_AI_MODEL`
pick provider and model. Provider secrets are only ever read from the
environment on the server.

## Pairing the desktop (Mark-LIII) with the server

The desktop app already speaks the control-plane contract. To make it a
client of this Command Center:

1. Settings → Machine tokens → **Create token** (role `operator`, actor e.g.
   `mark-liii-windows`). Copy the secret — it is shown once.
2. On the Windows machine set the environment variable
   `JARVIS_GATEWAY_TOKEN=<secret>`.
3. In `config/api_keys.json` (gitignored) set
   `"jarvis_gateway_url": "https://<your-server>/<root-path>"` and
   `"jarvis_actor": "mark-liii-windows"` (see `config/control_plane.example.json`).
4. Start the desktop. Every typed or spoken command is sent to
   `POST /v1/commands` here, the Master Agent runs it, and the answer is read
   back verbatim. Approval requests raised during the run show up in the
   dashboard and are reported to the desktop as `awaiting_approval`.

Conversations created by the desktop appear in the Chat module (owner = the
token), tasks the agent creates appear in Tasks, every tool call lands in the
audit trail.

## What the agents can actually do

Every tool is real or honestly marked unavailable — the Agents page names the
missing credential rather than pretending.

| Tool group | Needs | Notes |
|---|---|---|
| `server.*`, `docker.*`, `logs.search` | nothing (docker socket optional) | restarts are approval-gated and off by default |
| `filesystem.*`, `document.create` | nothing | sandboxed to the workspace, deletes go to `.trash` |
| `task.*`, `agent.delegate`, `memory.*`, `notify.user` | nothing | |
| `web.search`, `web.fetch` | nothing | DuckDuckGo HTML, no key or account |
| `calendar.read/create/move/cancel` | nothing | Google Calendar when configured, otherwise the local store the desktop also reads; cancelling needs approval |
| `email.search/read/draft/send` | `EMAIL_USER` + `EMAIL_PASSWORD` | IMAP/SMTP, servers guessed from the domain; **sending always needs approval** |
| `github.read/issues/commits/repo` | `GITHUB_TOKEN` | read-only |
| `workflow.list/execute/runs` | `N8N_BASE_URL` + `N8N_API_KEY` | trigger is webhook-based |
| `terminal.execute` | `JARVIS_CC_ALLOW_TERMINAL=true` | admin role **and** approval, runs inside the workspace |

The calendar deliberately reuses `plugins/_calendar_core.py`, so a date the
desktop refuses is refused here too and appointments booked from either side
land in the same store.

## Security model

* Sessions: HttpOnly, SameSite=Lax cookie; `Secure` behind HTTPS; scrypt
  password hashes; login rate limiting; CSRF double-submit header on every
  mutating request from a browser session.
* Roles: `viewer` (read), `operator` (chat, tasks, approvals, workflows,
  files), `admin` (users, tokens, agent enable/disable, restarts, terminal).
* Machine tokens: stored as SHA-256 hashes; `X-Jarvis-Token` or Bearer.
* Approval gate: tools at risk `high`/`critical` (configurable via
  `JARVIS_CC_APPROVAL_RISK`) block inside the orchestrator until an operator
  approves; expiry after `JARVIS_CC_APPROVAL_TIMEOUT_MIN`.
* Dangerous capabilities are off by default: `JARVIS_CC_ALLOW_TERMINAL`,
  `JARVIS_CC_ALLOW_DOCKER_ACTIONS`, `JARVIS_CC_ALLOW_SERVICE_RESTART`.
* Files: only the workspace directory is reachable; deletes go to `.trash`.
* Audit trail (`audit_events`): actor, agent, tool, target, status,
  result/error, task and run correlation. Never trimmed by log retention.
* Security headers, no-store on API responses, secrets never serialised.

## Extending

* **Backend module**: add `backend/modules/<name>/__init__.py` exposing
  `MODULE = ModuleSpec(...)` with a router; list it in `JARVIS_CC_MODULES`
  (or `DEFAULT_MODULES`). It appears in navigation and the command palette
  automatically.
* **Frontend page**: `registerModule({ id, component })` in
  `frontend/src/app/modules.ts` + a folder under `frontend/src/modules/`.
* **Tool**: `ToolSpec(...)` registered in `orchestrator/builtin_tools.py` or
  from any module's `on_startup` — schema, risk, min role, handler.
* **Agent**: add to `config/command_center/agents.json` (same shape as
  `config/agency.json`; `replace: true` to drop the built-in roster).
* **Integration**: subclass `IntegrationAdapter` in `adapters/integrations.py`.
* **Workflow engine**: implement `WorkflowAdapter` in `adapters/workflows.py`.
* **AI provider**: implement `LLMProvider` in `ai/` and add it to `_FACTORIES`.

## Tests

```bash
python tests/test_command_center.py     # backend end-to-end with a scripted provider (no network)
python tests/test_control_plane.py      # desktop client contract (unchanged)
cd command_center/frontend && npm run typecheck && npm run lint && npm run build
```
