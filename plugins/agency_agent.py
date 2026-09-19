"""
agency_agent plugin — gives JARVIS a roster of 280+ specialist personas to
consult for expert-level help (engineering, marketing, sales, security,
finance, healthcare, design, ...).

The personas are vendored, free, MIT-licensed markdown files from
https://github.com/msitarzewski/agency-agents (see plugins/_agency_agents_data/).
Each one is a self-contained system-prompt-style persona. This plugin picks
the best-matching persona for a task (by name if given, otherwise by simple
keyword overlap), asks Gemini to answer *as* that persona, saves the full
consultation to a file, and remembers a short pointer to it so JARVIS can
recall who it asked and when.
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from memory.memory_manager import remember

PLUGIN = {
    "name": "agency_agent",
    "description": (
        "Consults a specialized AI subject-matter expert -- one of 280+ persona-driven "
        "specialists covering engineering, marketing, sales, finance, security, healthcare, "
        "design, product, support, testing, research and more -- for focused, expert-level "
        "advice or a deliverable. Saves the full consultation to a file and remembers a short "
        "summary. Call this whenever a request calls for deep domain expertise rather than a "
        "generic answer, e.g. 'review this database schema', 'write a cold sales email', "
        "'plan an incident response', 'audit this for security issues'. If you already know "
        "which specialist fits, name them in 'agent'; otherwise just describe the task in "
        "'task' and the best match is picked automatically."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {
                "type": "STRING",
                "description": "What you need help with -- as much detail as you have.",
            },
            "agent": {
                "type": "STRING",
                "description": (
                    "Optional: the specialist's name if you already know which one fits, "
                    "e.g. 'Backend Architect' or 'Frontend Developer'."
                ),
            },
            "division": {
                "type": "STRING",
                "description": (
                    "Optional: narrow the search to one division, e.g. 'engineering', "
                    "'marketing', 'security', 'sales', 'healthcare'."
                ),
            },
        },
        "required": ["task"],
    },
}

_DATA_DIR = Path(__file__).resolve().parent / "_agency_agents_data"
_DESKTOP = Path.home() / "Desktop"
_GEMINI_MODEL = "gemini-flash-latest"
_MAX_SPOKEN_CHARS = 900

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
    "help", "me", "please", "can", "you", "i", "need", "want", "do", "does",
    "how", "this", "that", "about", "some", "our", "my",
}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _get_api_key() -> str:
    config_path = _get_base_dir() / "config" / "api_keys.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_client():
    from google import genai
    _c = genai.Client(api_key=_get_api_key())

    class _W:
        def generate_content(self, contents):
            return _c.models.generate_content(model=_GEMINI_MODEL, contents=contents)

    return _W()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)
    meta = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, body


def _build_index() -> list[dict]:
    agents = []
    if not _DATA_DIR.is_dir():
        return agents
    for path in sorted(_DATA_DIR.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, _ = _parse_frontmatter(text)
        name = meta.get("name")
        if not name:
            continue
        division = path.relative_to(_DATA_DIR).parts[0]
        agents.append({
            "name": name,
            "description": meta.get("description", ""),
            "emoji": meta.get("emoji", ""),
            "division": division,
            "slug": path.stem,
            "path": path,
        })
    return agents


_INDEX: list[dict] | None = None


def _get_index() -> list[dict]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _build_index()
    return _INDEX


def _stem(word: str) -> str:
    """Crude suffix stripping so 'tests'/'testing'/'tested' share a token with
    'test' -- good enough for keyword overlap, no NLP dependency needed."""
    for suf in ("ing", "ies", "es", "ed", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[:-3] + "y" if suf == "ies" else word[: -len(suf)]
    return word


def _tokenize(s: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]+", s.lower())
    return {_stem(w) for w in words if len(w) > 2 and w not in _STOPWORDS}


def _find_by_name(hint: str, agents: list[dict]):
    hint_low = hint.strip().lower()
    if not hint_low:
        return None
    for a in agents:
        if a["slug"].lower() == hint_low or a["name"].lower() == hint_low:
            return a
    for a in agents:
        if hint_low in a["name"].lower() or hint_low in a["slug"].lower():
            return a
    return None


def _best_match(task: str, agents: list[dict]):
    if not agents:
        return None
    task_words = _tokenize(task)
    if not task_words:
        return agents[0]
    best, best_score = None, -1.0
    for a in agents:
        agent_words = _tokenize(a["name"] + " " + a["description"])
        overlap = len(task_words & agent_words)
        # F1-style overlap: rewards agents whose *whole* description is about
        # the task, not just ones long enough to coincidentally share a word.
        score = 2 * overlap / (len(task_words) + len(agent_words)) if agent_words else 0.0
        if score > best_score:
            best, best_score = a, score
    return best


def _speak(agent: dict, result: str) -> str:
    prefix = f"{agent['name']}: "
    body = result.strip()
    if len(prefix) + len(body) <= _MAX_SPOKEN_CHARS:
        return prefix + body
    cut = body[: _MAX_SPOKEN_CHARS - len(prefix)].rsplit("\n", 1)[0]
    return f"{prefix}{cut}\n... (full consultation saved to your Desktop)"


def _save_consultation(agent: dict, task: str, result: str) -> Path:
    _DESKTOP.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = _DESKTOP / f"agency_{agent['slug']}_{ts}.md"
    out.write_text(
        f"# {agent.get('emoji', '')} {agent['name']} -- consultation\n\n"
        f"**Task:** {task}\n\n---\n\n{result}\n",
        encoding="utf-8",
    )
    return out


def run(parameters: dict, player=None, session_memory=None) -> str:
    task = (parameters.get("task") or "").strip()
    if not task:
        return "What do you need expert help with, Sir?"

    agents = _get_index()
    if not agents:
        return "Sir, the agency roster isn't installed -- plugins/_agency_agents_data is missing or empty."

    division_hint = (parameters.get("division") or "").strip().lower()
    pool = [a for a in agents if a["division"].lower() == division_hint] if division_hint else agents
    if not pool:
        pool = agents

    agent_hint = (parameters.get("agent") or "").strip()
    chosen = _find_by_name(agent_hint, pool) if agent_hint else None
    if chosen is None:
        chosen = _best_match(task, pool)
    if chosen is None:
        return "Sir, I couldn't find a matching specialist for that."

    try:
        persona = chosen["path"].read_text(encoding="utf-8")
    except Exception as e:
        return f"Sir, I couldn't load the {chosen['name']} agent: {e}"

    prompt = (
        f"{persona}\n\n---\n\n"
        f"A user just asked you (in character, following everything above) for help with:\n"
        f"{task}\n\n"
        "Respond directly and concisely, as this specialist, with concrete, actionable guidance."
    )

    try:
        model = _gemini_client()
        response = model.generate_content(prompt)
        result = (response.text or "").strip()
    except Exception as e:
        return f"Sir, consulting the {chosen['name']} failed: {e}"

    if not result:
        return f"Sir, the {chosen['name']} didn't return anything usable."

    try:
        saved_path = _save_consultation(chosen, task, result)
        remember(
            key=f"agency_{chosen['slug']}_{int(time.time())}",
            value=f"Consulted {chosen['name']} ({chosen['division']}) about: {task[:150]} -- saved: {saved_path.name}",
            category="notes",
        )
    except Exception:
        pass

    if player:
        try:
            player.write_log(f"JARVIS: [{chosen['name']}] {result}")
        except Exception:
            pass

    return _speak(chosen, result)
