"""
Agency — the multi-agent layer behind the ``agency_agent`` tool.

Where ``actions/dev_agent.py`` is one model doing one job (plan → write → run →
fix), an Agency is several *specialists* that hand work to each other. A run has
three moving parts:

  * **Agents** — a name, a role, free-text instructions, and the subset of JARVIS
    tools that agent is allowed to call. Nothing else distinguishes them.
  * **Flows** — a directed list of ``[sender, receiver]`` pairs. An agent can
    only delegate along a flow it appears in as the sender, so the graph is the
    permission model, not a suggestion in a prompt.
  * **The entry agent** — the one that receives the user's goal and is the only
    one whose answer is spoken back.

Every turn the acting agent returns ONE JSON decision: call a tool, delegate to
a peer, or finish. Delegation recurses (bounded by ``MAX_DEPTH``) and the peer's
answer comes back as an observation, so the caller keeps the thread it started.

Two budgets keep a run finite and cheap: ``max_steps`` caps the total number of
model calls across the whole agency (not per agent — a loop between two agents
would otherwise multiply it), and ``MAX_DEPTH`` caps how deep delegation nests.
Both are hit gracefully: the engine asks for a best-effort answer instead of
returning nothing.

The roster is data, not code. Drop a ``config/agency.json`` next to your API keys
to replace the default team without touching this file — see ``load_roster()``.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
ROSTER_PATH     = BASE_DIR / "config" / "agency.json"
ACTIONS_DIR     = BASE_DIR / "actions"

DEFAULT_MODEL   = "gemini-flash-latest"
MAX_STEPS       = 12      # total model calls per run, across every agent
MAX_DEPTH       = 3       # how deep one delegation chain may nest
OBS_CHARS       = 1200    # per-observation trim kept in an agent's context
ANSWER_CHARS    = 4000    # trim on what an agent hands back

# The agency tool itself is never callable from inside a run — an agent asking
# the agency to run the agency is an unbounded loop wearing a tie.
SELF_TOOL_NAME  = "agency_agent"


# ── Roster ───────────────────────────────────────────────────────────────────

@dataclass
class Agent:
    name: str
    role: str = ""
    instructions: str = ""
    tools: list[str] = field(default_factory=list)   # JARVIS tool names
    model: str = DEFAULT_MODEL
    backend: str = "auto"   # "auto" | "gemini" | "local" — both free to run


DEFAULT_AGENTS: list[Agent] = [
    Agent(
        name="coordinator",
        role="Head of the agency — the only agent that talks to the user.",
        instructions=(
            "Break the goal into the smallest number of concrete sub-tasks and give each "
            "to the specialist best suited to it. Delegate one task at a time and wait for "
            "the answer before deciding the next step. Do not do a specialist's work "
            "yourself when you can delegate it. When you have enough to answer, finish with "
            "a single spoken-language answer for the user — no headings, no bullet lists, "
            "no mention of the agency's internal steps."
        ),
        tools=[],
    ),
    Agent(
        name="researcher",
        role="Finds current facts, prices, news and sources on the web.",
        instructions=(
            "Answer only from what the search tool actually returned. State numbers and "
            "dates exactly as found, and say plainly when something could not be found "
            "rather than filling the gap from memory."
        ),
        tools=["web_search"],
    ),
    Agent(
        name="analyst",
        role="Reads local files and turns raw material into a conclusion.",
        instructions=(
            "Weigh what you are given, name the trade-offs that matter, and commit to a "
            "recommendation. Flag explicitly anything you had to assume."
        ),
        tools=["file_processor"],
    ),
    Agent(
        name="engineer",
        role="Writes, reviews and debugs code, and builds whole projects.",
        instructions=(
            "Use code_helper for review, debugging and single snippets. Use dev_agent only "
            "when the task really is a complete multi-file project — it writes to disk and "
            "opens an editor, so it is never the right tool for a question about code."
        ),
        tools=["code_helper", "dev_agent"],
    ),
]

DEFAULT_FLOWS: list[list[str]] = [
    ["coordinator", "researcher"],
    ["coordinator", "analyst"],
    ["coordinator", "engineer"],
    ["researcher", "analyst"],
]


@dataclass
class Roster:
    agents: dict[str, Agent]
    flows: list[tuple[str, str]]
    entry: str
    source: str = "built-in"


def _coerce_agent(raw: dict) -> Optional[Agent]:
    name = str(raw.get("name", "")).strip()
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", name):
        return None
    tools = [str(t).strip() for t in (raw.get("tools") or []) if str(t).strip()]
    return Agent(
        name=name,
        role=str(raw.get("role", "")).strip(),
        instructions=str(raw.get("instructions", "")).strip(),
        tools=[t for t in tools if t != SELF_TOOL_NAME],
        model=str(raw.get("model") or DEFAULT_MODEL).strip(),
        backend=str(raw.get("backend") or "auto").strip().lower(),
    )


def load_roster(logger: Callable[[str], None] = print) -> Roster:
    """
    Returns the team to run with. ``config/agency.json`` replaces the built-in
    roster when present and readable; anything malformed in it is reported and
    the defaults are used instead, because a half-applied roster (agents from
    the file, flows from the defaults) would silently rewire the permissions.

    Expected shape::

        {
          "entry": "coordinator",
          "agents": [
            {"name": "coordinator", "role": "...", "instructions": "...", "tools": []},
            {"name": "researcher",  "role": "...", "instructions": "...", "tools": ["web_search"]}
          ],
          "flows": [["coordinator", "researcher"]]
        }
    """
    default = Roster(
        agents={a.name: a for a in DEFAULT_AGENTS},
        flows=[(a, b) for a, b in DEFAULT_FLOWS],
        entry=DEFAULT_AGENTS[0].name,
    )

    if not ROSTER_PATH.exists():
        return default

    try:
        raw = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
        agents = [a for a in (_coerce_agent(r) for r in raw.get("agents", [])) if a]
        if not agents:
            raise ValueError("no valid agents in 'agents'")

        by_name = {a.name: a for a in agents}
        flows: list[tuple[str, str]] = []
        for pair in raw.get("flows", []):
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                continue
            sender, receiver = str(pair[0]).strip(), str(pair[1]).strip()
            # A flow naming an agent that does not exist is dropped, not fatal —
            # it grants nothing, so it cannot widen anyone's reach.
            if sender in by_name and receiver in by_name and sender != receiver:
                flows.append((sender, receiver))

        entry = str(raw.get("entry", "")).strip() or agents[0].name
        if entry not in by_name:
            logger(f"Agency: entry '{entry}' is not in the roster — using '{agents[0].name}'.")
            entry = agents[0].name

        logger(f"Agency: roster loaded from {ROSTER_PATH.name} "
               f"({len(by_name)} agents, {len(flows)} flows, entry '{entry}').")
        return Roster(agents=by_name, flows=flows, entry=entry, source=ROSTER_PATH.name)

    except Exception as e:
        logger(f"Agency: {ROSTER_PATH.name} ignored ({e}) — using the built-in roster.")
        return default


# ── Model backend ────────────────────────────────────────────────────────────

def _gemini_key() -> Optional[str]:
    try:
        key = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8")).get("gemini_api_key", "")
        return key.strip() or None
    except Exception:
        return None


def _complete(prompt: str, system: str, model: str, backend: str = "auto") -> str:
    """
    One text completion.

    "auto"   — Gemini when a key is configured, the local model otherwise.
    "gemini" — always Gemini (free tier).
    "local"  — always the local model from core.llm_client (Ollama / LM Studio),
               which runs on the user's own machine and costs nothing per call.

    Neither backend bills anything, which is the point: an agency that quietly
    turns into a metered API call is an agency nobody runs twice.
    """
    backend = (backend or "auto").strip().lower()
    key = None if backend == "local" else _gemini_key()
    if backend == "gemini" and not key:
        raise RuntimeError("this agent is pinned to Gemini, but no API key is configured")
    if key:
        from google import genai   # imported lazily: keeps this module importable
        client = genai.Client(api_key=key)     # without google-genai installed
        resp = client.models.generate_content(
            model=model,
            contents=f"{system}\n\n{prompt}",
        )
        return (resp.text or "").strip()

    from core.llm_client import call_llm_text
    return call_llm_text(prompt=prompt, system=system)


# ── The JARVIS tools an agent may call ───────────────────────────────────────

_ability_registry = None   # cached for the process, like the app's own registries


class _Abilities:
    """Actions and plugins behind one lookup, so an agent's tool whitelist can
    name either. Without this the calendar — a plugin — would be invisible to
    every agent, and the dispatcher's whole job is the working day."""

    def __init__(self, actions, plugins):
        self._actions = actions
        self._plugins = plugins

    def get_tool_declarations(self) -> list[dict]:
        return self._actions.get_tool_declarations() + self._plugins.get_tool_declarations()

    def run(self, name: str, args: dict, ctx: dict) -> str:
        if self._actions.has(name):
            return self._actions.run(name, args, ctx)
        if self._plugins.has(name):
            return self._plugins.run(name, args,
                                     player=ctx.get("player"),
                                     session_memory=ctx.get("session_memory"))
        return f"Tool '{name}' is not available."


def _abilities(logger: Callable[[str], None]):
    """The live tool registries, discovered the way main.py discovers them.
    Modules already imported by the running app are reused, so this is cheap."""
    global _ability_registry
    if _ability_registry is None:
        from core.action_loader import discover_actions
        from core.plugin_loader import discover_plugins
        quiet = lambda m: None           # discovery chatter belongs to startup, not a run
        actions = discover_actions(
            actions_dir=ACTIONS_DIR,
            reserved_names={SELF_TOOL_NAME},
            logger=quiet,
        )
        plugins = discover_plugins(
            plugins_dir=BASE_DIR / "plugins",
            core_tool_names=actions.names() | {SELF_TOOL_NAME},
            logger=quiet,
        )
        _ability_registry = _Abilities(actions, plugins)
    return _ability_registry


def _tool_catalog(agent: Agent, logger: Callable[[str], None]) -> dict[str, dict]:
    """name → declaration, for the tools this agent is actually allowed to use."""
    registry = _abilities(logger)
    declared = {d["name"]: d for d in registry.get_tool_declarations()}
    catalog: dict[str, dict] = {}
    for name in agent.tools:
        if name == SELF_TOOL_NAME:
            continue
        if name in declared:
            catalog[name] = declared[name]
        else:
            logger(f"Agency: agent '{agent.name}' lists unknown tool '{name}' — skipped.")
    return catalog


# ── Decision parsing ─────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\r?\n?|\r?\n?```\s*$")


def _parse_decision(text: str) -> Optional[dict]:
    """Pulls the first JSON object out of a model reply. Returns None if there
    is nothing parseable — the caller turns that into a correction, not a crash."""
    cleaned = _FENCE_RE.sub("", (text or "").strip()).strip()
    candidates = [cleaned]

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# ── Run state and result ─────────────────────────────────────────────────────

@dataclass
class AgencyResult:
    answer: str
    transcript: list[str] = field(default_factory=list)
    steps: int = 0
    exhausted: bool = False


class _RunState:
    def __init__(self, max_steps: int, logger: Callable[[str], None]):
        self.steps_left = max_steps
        self.steps_used = 0
        self.transcript: list[str] = []
        self.logger = logger
        self.exhausted = False

    def spend(self) -> bool:
        if self.steps_left <= 0:
            self.exhausted = True
            return False
        self.steps_left -= 1
        self.steps_used += 1
        return True

    def note(self, line: str) -> None:
        self.transcript.append(line)
        self.logger(line)


class Agency:
    """A roster plus its flow graph, ready to run a goal."""

    def __init__(self, roster: Roster, logger: Callable[[str], None] = print,
                 max_steps: int = MAX_STEPS):
        self.roster = roster
        self.logger = logger
        self.max_steps = max(2, int(max_steps))

    # -- the permission model, in one place --
    def peers_of(self, name: str) -> list[str]:
        return [receiver for sender, receiver in self.roster.flows if sender == name]

    def describe(self) -> str:
        lines = [f"Agency roster ({self.roster.source}), entry point: {self.roster.entry}"]
        for agent in self.roster.agents.values():
            tools = ", ".join(agent.tools) if agent.tools else "no tools"
            peers = ", ".join(self.peers_of(agent.name)) or "no one"
            lines.append(f"  {agent.name} — {agent.role or 'no role set'} "
                         f"[tools: {tools}] [can delegate to: {peers}] "
                         f"[runs on: {agent.backend}]")
        return "\n".join(lines)

    # -- the run --
    def run(self, goal: str) -> AgencyResult:
        state = _RunState(self.max_steps, self.logger)
        entry = self.roster.agents[self.roster.entry]
        state.note(f"Agency: '{entry.name}' took the goal — {goal[:120]}")
        answer = self._run_agent(entry, goal, depth=0, state=state)
        return AgencyResult(answer=answer, transcript=state.transcript,
                            steps=state.steps_used, exhausted=state.exhausted)

    def _system_prompt(self, agent: Agent, catalog: dict[str, dict], depth: int) -> str:
        peers = self.peers_of(agent.name)
        peer_lines = "\n".join(
            f"  - {name}: {self.roster.agents[name].role or 'no role set'}" for name in peers
        ) or "  (none — you must finish this yourself)"

        tool_lines = "\n".join(
            f"  - {name}: {decl.get('description', '')[:240]}\n"
            f"    arguments: {json.dumps(decl.get('parameters', {}).get('properties', {}))[:400]}"
            for name, decl in catalog.items()
        ) or "  (none — you must reason from what you are given)"

        delegation_note = (
            "Delegation is closed at this depth — finish with what you have."
            if depth >= MAX_DEPTH else
            "Delegate when a peer is genuinely better placed; otherwise answer yourself."
        )

        return f"""You are "{agent.name}", one agent in an agency of specialists.
Role: {agent.role or 'unspecified'}
{agent.instructions}

Peers you may delegate to:
{peer_lines}

Tools you may call:
{tool_lines}

{delegation_note}

Reply with ONE JSON object and nothing else — no prose, no markdown fences:
{{"thought": "one short sentence", "action": "tool" | "delegate" | "final",
  "tool": "tool name when action is tool", "arguments": {{}},
  "to": "peer name when action is delegate", "message": "the task for that peer",
  "answer": "your result when action is final"}}

Rules:
1. Exactly one action per reply.
2. Only the tools and peers listed above exist. Anything else fails.
3. "final" ends your part — put the whole result in "answer", it is all the
   caller sees.
4. Never repeat a tool call that already appears in your history with the same
   arguments; use what it returned."""

    def _run_agent(self, agent: Agent, task: str, depth: int, state: _RunState) -> str:
        catalog = _tool_catalog(agent, self.logger)
        system = self._system_prompt(agent, catalog, depth)
        history: list[str] = []
        invalid_streak = 0

        while True:
            if not state.spend():
                return self._forced_answer(agent, task, history, system, state)

            prompt = (
                f"Task given to you: {task}\n\n"
                + ("What has happened so far:\n" + "\n".join(history) + "\n\n" if history else "")
                + "Your JSON decision:"
            )

            try:
                raw = _complete(prompt, system, agent.model, agent.backend)
            except Exception as e:
                state.note(f"Agency: '{agent.name}' could not reach the model — {e}")
                return f"[{agent.name} could not reach the model: {e}]"

            decision = _parse_decision(raw)
            if decision is None:
                invalid_streak += 1
                # A model that ignores the schema twice will ignore it a third
                # time — take its prose as the answer rather than burn the budget.
                if invalid_streak >= 2:
                    state.note(f"Agency: '{agent.name}' stopped answering in JSON — taking its text.")
                    return (raw or "").strip()[:ANSWER_CHARS] or f"[{agent.name} returned nothing]"
                history.append("System: that was not valid JSON. Reply with the JSON object only.")
                continue
            invalid_streak = 0

            action = str(decision.get("action", "")).strip().lower()

            if action == "final":
                answer = str(decision.get("answer", "")).strip()
                if not answer:
                    history.append("System: 'final' needs a non-empty 'answer'.")
                    continue
                state.note(f"Agency: '{agent.name}' finished.")
                return answer[:ANSWER_CHARS]

            if action == "tool":
                name = str(decision.get("tool", "")).strip()
                args = decision.get("arguments")
                args = args if isinstance(args, dict) else {}
                if name not in catalog:
                    history.append(f"System: tool '{name}' is not available to you. "
                                   f"Available: {', '.join(catalog) or 'none'}.")
                    continue
                state.note(f"Agency: '{agent.name}' → {name}({json.dumps(args)[:100]})")
                try:
                    ctx = {"player": None, "speak": None, "response": None, "session_memory": None}
                    output = _abilities(self.logger).run(name, args, ctx)
                except Exception as e:
                    output = f"tool failed: {e}"
                history.append(f"You called {name} and it returned: {str(output)[:OBS_CHARS]}")
                continue

            if action == "delegate":
                to = str(decision.get("to", "")).strip()
                message = str(decision.get("message", "")).strip()
                if depth >= MAX_DEPTH:
                    history.append("System: delegation is closed at this depth. Finish yourself.")
                    continue
                if to not in self.peers_of(agent.name):
                    history.append(f"System: you cannot delegate to '{to}'. "
                                   f"Your peers: {', '.join(self.peers_of(agent.name)) or 'none'}.")
                    continue
                if not message:
                    history.append("System: 'delegate' needs a non-empty 'message'.")
                    continue
                state.note(f"Agency: '{agent.name}' → '{to}': {message[:100]}")
                reply = self._run_agent(self.roster.agents[to], message, depth + 1, state)
                history.append(f"{to} answered: {str(reply)[:OBS_CHARS]}")
                continue

            history.append("System: 'action' must be exactly one of: tool, delegate, final.")

    def _forced_answer(self, agent: Agent, task: str, history: list[str],
                       system: str, state: _RunState) -> str:
        """The budget ran out mid-thread. Spend nothing more on the model and
        hand back what this agent already learned — an empty answer would throw
        away work the user already paid for."""
        state.note(f"Agency: step budget spent while '{agent.name}' was working.")
        if history:
            return ("I ran out of steps before finishing. What I established so far:\n"
                    + "\n".join(history)[-ANSWER_CHARS:])
        return f"[{agent.name} ran out of steps before producing anything]"


def build_agency(logger: Callable[[str], None] = print,
                 max_steps: int = MAX_STEPS,
                 only: Optional[list[str]] = None) -> Agency:
    """
    The roster, optionally narrowed to ``only`` (plus the entry agent, which is
    always kept — without it there is nobody to take the goal). Flows touching a
    dropped agent go with it, so a narrowed agency cannot delegate to someone who
    is not in the room.
    """
    roster = load_roster(logger)

    if only:
        keep = {name for name in only if name in roster.agents}
        unknown = [name for name in only if name not in roster.agents]
        if unknown:
            logger(f"Agency: no such agent(s): {', '.join(unknown)} — ignored.")
        if keep:
            keep.add(roster.entry)
            roster = Roster(
                agents={n: a for n, a in roster.agents.items() if n in keep},
                flows=[(s, r) for s, r in roster.flows if s in keep and r in keep],
                entry=roster.entry,
                source=roster.source,
            )

    return Agency(roster, logger=logger, max_steps=max_steps)
