"""
graphify plugin — gives JARVIS a local, offline knowledge graph of a code project.

Wraps the free, open-source `graphify` CLI (PyPI: graphifyy,
https://github.com/Graphify-Labs/graphify). Only local/algorithmic subcommands
are used here — code is parsed with tree-sitter and the graph is traversed
with plain graph algorithms, so this never calls a paid LLM backend and never
needs an API key. Install once with: pip install -r requirements.txt
(graphifyy is listed there).
"""

import shutil
import subprocess
from pathlib import Path

from memory.config_manager import get_plugin_setting

PLUGIN = {
    "name": "graphify",
    "description": (
        "Builds and queries a local, offline knowledge graph of a code project "
        "with the free 'graphify' tool (tree-sitter based, no LLM, no API key, "
        "nothing leaves the machine). Use for requests like 'map dieses Projekt', "
        "'analysiere die Codebasis', 'was ist mit X verbunden', 'erklär mir X im "
        "Code', 'zeig den Pfad von A zu B', 'was sind die wichtigsten Bausteine'. "
        "Do NOT use this for reading a single uploaded file (use file_processor) "
        "or for anything that requires internet search (use web_search)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: build (index/re-index a folder), query (ask a "
                    "question against the graph), explain (describe one node "
                    "and its neighbors), path (shortest path between two "
                    "nodes), god_nodes (list the most-connected concepts)."
                ),
            },
            "path": {
                "type": "STRING",
                "description": (
                    "Folder of the project to build/use. Optional — falls "
                    "back to the default project folder set in ⚙ → PLUGINS "
                    "→ GRAPHIFY, if configured."
                ),
            },
            "question": {"type": "STRING", "description": "Question to ask, for action=query."},
            "node": {"type": "STRING", "description": "Node/concept name, for action=explain."},
            "from_node": {"type": "STRING", "description": "Start node, for action=path."},
            "to_node": {"type": "STRING", "description": "End node, for action=path."},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "graphify",
    "title": "GRAPHIFY",
    "fields": [
        {
            "key": "default_path",
            "type": "text",
            "label": "Default project folder",
            "placeholder": "e.g. /home/you/projects/myapp",
            "default": "",
        },
    ],
}

_MAX_SPOKEN_CHARS = 900
_BUILD_TIMEOUT = 900   # local AST parsing on a big repo can take a while
_QUERY_TIMEOUT = 60


def _resolve_path(parameters: dict) -> Path | None:
    raw = (parameters.get("path") or "").strip() or get_plugin_setting("graphify", "default_path", "")
    if not raw:
        return None
    return Path(raw).expanduser()


def _run(args: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return False, ("The 'graphify' CLI isn't installed. Run: "
                        "pip install -r requirements.txt (it includes graphifyy).")
    except subprocess.TimeoutExpired:
        return False, f"graphify timed out after {timeout}s."
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if result.returncode != 0:
        return False, output or f"graphify exited with code {result.returncode}."
    return True, output or "Done, no output."


def _speak(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_SPOKEN_CHARS:
        return text
    return text[:_MAX_SPOKEN_CHARS].rsplit("\n", 1)[0] + "\n... (more in the activity log)"


def run(parameters: dict, player=None, session_memory=None) -> str:
    if shutil.which("graphify") is None:
        return ("Sir, graphify isn't installed. Run: pip install -r requirements.txt "
                "to add it (it's free — graphifyy on PyPI, everything runs locally).")

    action = (parameters.get("action") or "").strip().lower()
    project = _resolve_path(parameters)

    if project is None:
        return ("Which project folder should I use? Tell me a path, or set a "
                "default one under Settings → Plugins → Graphify.")
    if not project.is_dir():
        return f"Sir, I can't find the folder: {project}"

    graph_json = project / "graphify-out" / "graph.json"

    if action == "build":
        ok, output = _run(
            ["graphify", "extract", str(project), "--code-only"],
            cwd=project, timeout=_BUILD_TIMEOUT,
        )
        if player:
            try:
                player.write_log(f"JARVIS: [graphify build] {output}")
            except Exception:
                pass
        if not ok:
            return f"Sir, building the graph failed: {_speak(output)}"
        return f"Knowledge graph built for {project.name}. {_speak(output)}"

    if not graph_json.exists():
        return (f"Sir, there's no graph for {project.name} yet — say "
                "'map this project' first.")

    if action == "query":
        question = (parameters.get("question") or "").strip()
        if not question:
            return "What should I ask the graph?"
        ok, output = _run(
            ["graphify", "query", question, "--graph", str(graph_json)],
            cwd=project, timeout=_QUERY_TIMEOUT,
        )
    elif action == "explain":
        node = (parameters.get("node") or "").strip()
        if not node:
            return "Which node should I explain?"
        ok, output = _run(
            ["graphify", "explain", node, "--graph", str(graph_json)],
            cwd=project, timeout=_QUERY_TIMEOUT,
        )
    elif action == "path":
        from_node = (parameters.get("from_node") or "").strip()
        to_node = (parameters.get("to_node") or "").strip()
        if not from_node or not to_node:
            return "I need both a start and an end node for the path."
        ok, output = _run(
            ["graphify", "path", from_node, to_node, "--graph", str(graph_json)],
            cwd=project, timeout=_QUERY_TIMEOUT,
        )
    elif action == "god_nodes":
        ok, output = _run(
            ["graphify", "god-nodes", "--graph", str(graph_json), "--top", "10"],
            cwd=project, timeout=_QUERY_TIMEOUT,
        )
    else:
        return f"Sir, I don't know the graphify action '{action}'. Try build, query, explain, path or god_nodes."

    if player:
        try:
            player.write_log(f"JARVIS: [graphify {action}] {output}")
        except Exception:
            pass
    if not ok:
        return f"Sir, that graphify request failed: {_speak(output)}"
    return _speak(output)
