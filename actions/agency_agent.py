"""
agency_agent — JARVIS's multi-agent mode.

One tool call, several specialists. The coordinator takes the goal, hands pieces
of it to the agents that own the relevant tools, and returns a single answer;
everything about who exists and who may talk to whom lives in ``core/agency.py``
(and in ``config/agency.json`` when you want your own team).

This file is deliberately thin — it is the seam between the assistant and the
agency: it validates what Gemini passed, mirrors progress into the HUD log, and
turns an ``AgencyResult`` into one spoken sentence-shaped string.

When NOT to use it: a single question with a single obvious tool is faster and
cheaper through that tool directly. The agency earns its cost when a goal needs
research *and* judgement *and* code, not when it needs one of them.
"""
from __future__ import annotations

from core.agency import MAX_STEPS, build_agency


def _make_logger(player=None):
    """Progress goes to the console always, and to the HUD activity log when the
    UI is there — a run takes long enough that silence reads as a hang."""
    def log(message: str) -> None:
        print(f"[Agency] {message}")
        if player is not None:
            try:
                player.write_log(f"JARVIS: {message}")
            except Exception:
                pass
    return log


def agency_agent(
    parameters: dict,
    player=None,
    speak=None,
    response=None,
    session_memory=None,
) -> str:
    p = parameters or {}
    goal = str(p.get("goal", "")).strip()
    mode = str(p.get("action", "run")).strip().lower() or "run"

    raw_agents = p.get("agents") or []
    if isinstance(raw_agents, str):          # a model that sends "a, b" instead of a list
        raw_agents = [part for part in raw_agents.replace(",", " ").split() if part]
    only = [str(a).strip() for a in raw_agents if str(a).strip()]

    try:
        max_steps = int(p.get("max_steps") or MAX_STEPS)
    except (TypeError, ValueError):
        max_steps = MAX_STEPS
    max_steps = max(2, min(max_steps, 40))   # a runaway budget is the one thing
                                             # that turns a wrong plan expensive
    log = _make_logger(player)

    try:
        agency = build_agency(logger=log, max_steps=max_steps, only=only or None)
    except Exception as e:
        return f"Sir, I could not assemble the agency: {e}"

    if mode == "roster" or not goal:
        return agency.describe()

    log(f"Running the agency on: {goal[:120]}")
    try:
        result = agency.run(goal)
    except Exception as e:
        return f"Sir, the agency run failed: {e}"

    log(f"Done in {result.steps} step{'s' if result.steps != 1 else ''}.")

    answer = (result.answer or "").strip() or "The agency finished without producing an answer."
    if result.exhausted:
        answer += (f"\n\n(I stopped at the {max_steps}-step limit — "
                   f"ask again with a higher max_steps if you want it taken further.)")
    return answer


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "agency_agent",
    "description": (
        "Runs a team of specialist agents (coordinator, researcher, analyst, engineer) on one "
        "goal: they delegate to each other and use tools like web_search, file_processor, "
        "code_helper and dev_agent, then return a single answer. Use for goals that need "
        "several different kinds of work at once — research plus judgement plus code, a "
        "written comparison built from live data, a plan drawn from a file and the web. "
        "Do NOT use it for something one tool already does: for a plain web lookup use "
        "web_search, to build a project use dev_agent, to review code use code_helper. "
        "Set action='roster' to list the agents and who may delegate to whom."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": "The complete goal for the agency, in one sentence or two"
            },
            "action": {
                "type": "STRING",
                "description": "run (default) | roster — 'roster' just lists the team"
            },
            "agents": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional: restrict the run to these agents (the coordinator is always included)"
            },
            "max_steps": {
                "type": "INTEGER",
                "description": "Total model calls allowed across the whole run (default 12, max 40)"
            }
        },
        "required": [
            "goal"
        ]
    },
    "handler": agency_agent,
}
