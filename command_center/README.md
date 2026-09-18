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
Desktop agent      ──X-Jarvis-Token──▶ /v1/desktop/{register,poll,result}  (outbound only)
```

Every directory, file and what it is for: [STRUKTUR.md](STRUKTUR.md) (in German).

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
| `composio.apps/tools/run` | `COMPOSIO_API_KEY` | a few hundred services behind one key; `composio.run` acts in real accounts and is approval-gated |
| `terminal.execute` | `JARVIS_CC_ALLOW_TERMINAL=true` | admin role **and** approval, runs inside the workspace |
| `desktop.devices/open_app/run` | a paired PC | drives the desktop's own actions; `desktop.run` is approval-gated |
| `teach.start/note/stop/learn`, `procedure.list/run` | nothing (learning needs a local AI provider) | records a demonstration and turns it into a procedure |

Voice is the same deal: set `JARVIS_CC_STT_URL` (and optionally
`JARVIS_CC_TTS_URL`) to any OpenAI-compatible audio endpoint —
faster-whisper-server, whisper.cpp, Speaches, LocalAI, Kokoro-FastAPI, or
OpenAI itself — and the microphone in the chat records and transcribes for
real. With neither set, the button stays disabled and says so; no audio is
ever stored on the server.

The calendar deliberately reuses `plugins/_calendar_core.py`, so a date the
desktop refuses is refused here too and appointments booked from either side
land in the same store.

## Driving the PC from the server

Ask for it in chat ("mach mir Excel auf", "schließ das Fenster") and the paired
desktop does it. The direction of the connection matters: the server never
dials into your machine. JARVIS on the PC holds a long poll open outward,
picks up one command at a time, runs it through the actions it already has
(`open_app`, `computer_control`, `browser_control`, `computer_settings`,
`desktop_control`, …) and posts the result back. No port forwarding, no VPN,
nothing listening on your network.

Start it either way:

```bash
python desktop_agent.py     # remote control only
python main.py              # the normal app; the runner starts with it when a token is configured
```

It needs the same machine token as the rest of the pairing. `desktop.run` asks
for your approval by default — set `JARVIS_CC_DESKTOP_REQUIRE_APPROVAL=false`
once you trust the setup. Actions the PC should never expose remotely go in
`"desktop_blocked_actions"` in `config/api_keys.json`; `dev_agent`,
`agency_agent` and `shutdown_jarvis` are blocked already.

If the PC is asleep, a command is **refused with that reason** rather than
queued forever, so JARVIS never claims to have opened something it did not.

## Teaching it by demonstration

Start a recording in the Teach page (or say so in chat: JARVIS calls
`teach.start`), then do the job once as you normally would. What gets written
down is what actually happened: what you said, every tool that ran with its
arguments and result, every action executed on your PC, plus any note you add
about *why* a step happens.

Stop it, then **Learn**. The model reads the trace and writes a procedure: a
name, a goal, ordered steps, the tools each step needs, and placeholders for
the values that change next time. Two rules keep it honest:

* a step may only reference a tool that actually exists — an invented one is
  dropped, not stored as a promise;
* a real tool that merely lacks credentials is kept, and the procedure says it
  still needs setting up.

Optionally it also creates a **specialist agent** with the instructions the
model wrote, limited to the procedure's tools. It appears in the Agents page,
survives restarts, and `procedure.run` hands work straight to it. Give a
procedure a schedule and it runs by itself — that is the proactive half. Every
lesson also goes into memory, so the master agent can recall it in conversation
and offer to run it instead of improvising the same job twice.

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
* Composio: the key stays on this server and the OAuth tokens stay at
  Composio — neither ever reaches the browser. `composio.run` acts in real
  accounts, so it is `high` risk and passes the approval gate, and a toolkit
  with no live connection is refused by name instead of attempted.
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
