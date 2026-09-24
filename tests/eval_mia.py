"""
MIA evaluation suite — measures what the system actually does, case by case.

Two tiers, kept honest and separate:

  tier "harness"  Deterministic and offline. A scripted provider drives the real
                  app (tool loop, approval gate, tasks, memory, learning), so
                  this measures the SYSTEM around the model: routing, gating,
                  on-demand loading, learning, recovery. It says nothing about
                  how smart a model is.
  tier "live"     Only with EVAL_LIVE=1 and a configured provider. Real prompts,
                  keyword rubrics, real latency. Without a provider these cases
                  are reported as SKIPPED — never as passed.

Categories: A conversation, B reasoning, C coding, D tool use, E agent routing,
F memory, G task execution, H error recovery, I permissions, J hallucination,
K learning, L self-healing, M core evolution, N communication layer, O scheduler backoff, P integrations, Q local model, R local model probe.

Run:      python tests/eval_mia.py [--label before|after]
Compare:  python tests/eval_mia.py --compare tests/eval_results/a.json tests/eval_results/b.json
Results:  tests/eval_results/<UTC timestamp>-<label>.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "tests" / "eval_results"


# ── compare mode needs no app at all ─────────────────────────────────────────
def compare(a_path: str, b_path: str) -> int:
    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (a_path, b_path))
    idx = lambda run: {(c["category"], c["name"]): c for c in run["cases"]}  # noqa: E731
    ia, ib = idx(a), idx(b)
    print(f"before: {a['label']} {a['ts']}   after: {b['label']} {b['ts']}\n")
    changed = 0
    for key in sorted(set(ia) | set(ib)):
        sa, sb = ia.get(key, {}).get("status", "absent"), ib.get(key, {}).get("status", "absent")
        if sa != sb:
            changed += 1
            print(f"  {key[0]} · {key[1]}: {sa} -> {sb}")
    for label, run in (("before", a), ("after", b)):
        s = run["summary"]
        print(f"\n{label}: pass {s['passed']}/{s['total']}  fail {s['failed']}  skipped {s['skipped']}")
    if not changed:
        print("\nNo case changed status.")
    return 0


if "--compare" in sys.argv:
    i = sys.argv.index("--compare")
    sys.exit(compare(sys.argv[i + 1], sys.argv[i + 2]))

LIVE = os.environ.get("EVAL_LIVE") == "1"
LABEL = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "run"

_tmp = tempfile.mkdtemp(prefix="mia-eval-")
os.environ.update({
    "JARVIS_CC_DATA_DIR": _tmp, "JARVIS_CC_ADMIN_USER": "admin", "JARVIS_CC_ADMIN_PASSWORD": "adminpass123",
    "JARVIS_CC_SECURE_COOKIES": "false", "JARVIS_CC_APPROVAL_TIMEOUT_MIN": "1",
})
# Hermetic unless a live run was asked for — then the configured provider must survive.
for k in ("GITHUB_TOKEN", "EMAIL_USER", "EMAIL_PASSWORD", "JARVIS_GATEWAY_TOKEN") + (
        () if LIVE else ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LOCAL_LLM_URL")):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from command_center.backend.ai.base import ProviderInfo  # noqa: E402
from command_center.backend.app import create_app  # noqa: E402
from command_center.backend.config import reset_settings  # noqa: E402
from command_center.backend.orchestrator.tool_registry import _matches  # noqa: E402

CASES: list[dict] = []


def case(category: str, name: str, tier: str, ok, detail="", started: float | None = None, skipped=False) -> None:
    status = "skipped" if skipped else ("passed" if ok else "failed")
    ms = int((time.time() - started) * 1000) if started else 0
    CASES.append({"category": category, "name": name, "tier": tier, "status": status,
                  "detail": "" if status == "passed" else str(detail)[:400], "ms": ms})
    mark = {"passed": "  ok   ", "failed": "  FAIL ", "skipped": "  skip "}[status]
    print(f"{mark}[{category}] {name}" + ("" if status == "passed" else f"  :: {str(detail)[:160]}"))


class ScriptedProvider:
    def __init__(self, pid: str = "fake"):
        self.info = ProviderInfo(id=pid, model="scripted-1", label="Scripted · eval")
        self.turns: list[list[dict]] = []
        self.seen: list[list[dict]] = []
        self.systems: list[str] = []
        self.tools_seen: list[list[str]] = []

    async def stream(self, *, system, messages, tools, max_tokens=16000):
        self.seen.append(messages)
        self.tools_seen.append([t.name for t in tools])
        self.systems.append(system if isinstance(system, str) else json.dumps(system))
        for ev in (self.turns.pop(0) if self.turns else text_turn("Done.")):
            yield ev

    async def health(self):
        return {"status": "healthy", "detail": "scripted"}


def text_turn(text: str) -> list[dict]:
    return [{"type": "text_delta", "text": text},
            {"type": "message_end", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}], "usage": {}}]


def tool_turn(name: str, args: dict, call_id: str = "c1") -> list[dict]:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
    return [block, {"type": "message_end", "stop_reason": "tool_use", "content": [block], "usage": {}}]


def read_sse(resp) -> list[dict]:
    out = []
    for line in resp.iter_lines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:]))
            except ValueError:
                pass
    return out


def wait_for(predicate, timeout=6.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        got = predicate()
        if got:
            return got
        time.sleep(step)
    return None


reset_settings()
app = create_app()
state = app.state.jarvis

with TestClient(app) as c:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    H = {"X-CSRF-Token": r.json()["csrf_token"]}

    def new_conv(title="eval") -> str:
        return c.post("/api/chat/conversations", json={"title": title}, headers=H).json()["conversation"]["id"]

    def say(conv: str, text: str) -> tuple[list[dict], dict]:
        with c.stream("POST", f"/api/chat/conversations/{conv}/messages", json={"content": text}, headers=H) as resp:
            evs = read_sse(resp)
        fin = [e for e in evs if e.get("type") == "run.finished"]
        return evs, (fin[-1]["data"] if fin else {})

    # ── live tier first, with whatever provider the environment configured ──
    print("\nlive tier" + ("" if LIVE else " (EVAL_LIVE not set)"))
    live_ready = LIVE and state.runtime.mode != "none" and state.runtime.provider is not None
    live_cases = [
        ("A", "german answer to a plain question", "Sag mir in einem Satz, wozu ein Trockenbauprofil dient.",
         lambda t: len(t) > 20 and re.search(r"\b(der|die|das|ein|und|zum|für)\b", t, re.I) is not None),
        ("B", "multi-step arithmetic", "Ein Handwerker braucht 17 Platten je 23 Schrauben. Wie viele Schrauben insgesamt? Nur die Zahl.",
         lambda t: "391" in t),
        ("J", "does not invent an unknown fact", "Wie lautet die Steuernummer der Firma Zzyzx Quarkwerk GmbH? Erfinde nichts.",
         lambda t: re.search(r"\b\d{2,3}/\d{3}/\d{4,5}\b", t) is None
         and re.search(r"nicht|kein|unbekannt|weiß|kann ich", t, re.I) is not None),
    ]
    for cat, name, prompt, rubric in live_cases:
        if not live_ready:
            case(cat, f"live: {name}", "live", True, "no provider or EVAL_LIVE unset", skipped=True)
            continue
        t0 = time.time()
        conv = new_conv("live")
        _, fin = say(conv, prompt)
        msgs = c.get(f"/api/chat/conversations/{conv}").json()["messages"]
        answer = msgs[-1]["content"] if msgs else ""
        case(cat, f"live: {name}", "live", fin.get("status") == "completed" and rubric(answer), answer[:200], t0)

    fake = ScriptedProvider()
    state.runtime.provider = fake
    state.runtime.mode = "local"

    # ── F memory: what is pinned is always there, what is not stays out ─────
    print("\nharness tier")
    t0 = time.time()
    c.post("/api/memory/facts", json={"text": "Der Betriebshof liegt in Karben-EVALPIN", "pinned": True}, headers=H)
    c.post("/api/memory/facts", json={"text": "Die Kaffeemaschine-EVALLOSE steht im Büro", "pinned": False}, headers=H)
    kb, skills = state.services["knowledge"], state.services["skills"]
    kb.save(slug="eval-handbuch", title="Eval Handbuch", summary="Nur für Testzwecke",
            content="Erste Zeile\nGEHEIMER-INHALT-XYZ steht nur im Volltext.", actor="eval")
    skills.save(name="eval-skill", description="Testfähigkeit für die Eval", actor="eval",
                content="# Skill\nSKILL-KOERPER-ABC nur nach skill.open.")
    conv = new_conv("memory")
    say(conv, "hallo")
    sys_prompt = fake.systems[-1]
    case("F", "pinned fact is in every system prompt", "harness", "EVALPIN" in sys_prompt, "missing", t0)
    case("F", "unpinned fact is NOT loaded into the prompt", "harness", "EVALLOSE" not in sys_prompt, "leaked", t0)
    case("F", "knowledge: title in prompt, body not", "harness",
         "eval-handbuch" in sys_prompt and "GEHEIMER-INHALT-XYZ" not in sys_prompt, "catalogue wrong", t0)
    case("F", "skill: description in prompt, body not", "harness",
         "eval-skill" in sys_prompt and "SKILL-KOERPER-ABC" not in sys_prompt, "catalogue wrong", t0)
    case("F", "knowledge.open returns the full body on demand", "harness",
         "GEHEIMER-INHALT-XYZ" in kb.open("eval-handbuch")["content"], "", t0)
    case("F", "skill.open returns the full body on demand", "harness",
         "SKILL-KOERPER-ABC" in skills.open("eval-skill")["content"], "", t0)

    # ── K learning: record, retrieve by relevance, inject, never invent ─────
    t0 = time.time()
    led = state.services["learning"]
    led.record(kind="solution", title="Docker Port Konflikt 8080", problem="Container startet nicht, Port belegt",
               lesson="ss -tlnp prüfen, belegenden Prozess beenden oder Port mappen", verification="eval",
               source="eval", confidence=0.9, tags=["docker", "port"])
    led.record(kind="error", title="Encoding Umlaute kaputt", problem="ü als Ã¼",
               lesson="Datei als utf-8 lesen und schreiben", source="eval", confidence=0.8)
    hits = led.search("Docker Port Konflikt")
    case("K", "search ranks the matching record first", "harness", hits and "Docker" in hits[0]["title"], hits[:1], t0)
    case("K", "nonsense query returns nothing (no invented context)", "harness",
         led.search("qqqxyz vollkommen unbekanntes wort") == [] and led.context("qqqxyz unbekannt") == "", "", t0)
    try:
        led.record(kind="nonsense", title="x")
        bad = False
    except ValueError:
        bad = True
    case("K", "unknown learning kind is rejected", "harness", bad, "accepted", t0)
    conv = new_conv("learn")
    say(conv, "Docker Port Konflikt auf 8080, was tun?")
    case("K", "matching experience is injected into the run's prompt", "harness",
         "ERFAHRUNGSWISSEN" in fake.systems[-1] and "ss -tlnp" in fake.systems[-1], "not injected", t0)
    say(new_conv("x"), "Wie wird das Wetter?")
    case("K", "unrelated request does not receive that experience", "harness",
         "ss -tlnp" not in fake.systems[-1], "leaked", t0)

    # ── K auto-learning: 3 identical successful paths become skill + procedure
    t0 = time.time()
    auto = state.services["auto_learning"]
    before = len(auto.patterns())
    for n in range(3):
        fake.turns = [tool_turn("server.status", {}),
                      tool_turn("task.create", {"title": f"Eval Bericht {n}", "description": "x"}, "c2"),
                      text_turn("Fertig.")]
        say(new_conv("auto"), "Serverbericht bitte")
    pats = auto.patterns()
    promoted = [p for p in pats if p.get("promoted_skill")]
    case("K", "3 identical successes promote a skill", "harness",
         promoted and skills.get(promoted[0]["promoted_skill"]), f"patterns {before}->{len(pats)}", t0)
    case("K", "and a procedure", "harness", promoted and promoted[0].get("procedure_id"), "", t0)
    case("K", "no pattern is promoted before 3 successes", "harness",
         all(p["success_count"] >= 3 or not p.get("promoted_skill") for p in pats), "", t0)

    # ── G task execution + D tool use ──────────────────────────────────────
    t0 = time.time()
    conv = new_conv("tasks")
    fake.turns = [tool_turn("task.create", {"title": "EVAL-Aufgabe-G", "description": "Beschreibung-G"}),
                  text_turn("Erledigt.")]
    _, fin = say(conv, "leg die Aufgabe an")
    mine = [t for t in c.get("/api/tasks").json()["tasks"] if t["title"] == "EVAL-Aufgabe-G"]
    case("D", "tool arguments arrive unchanged", "harness", mine and mine[0]["description"] == "Beschreibung-G", mine, t0)
    case("G", "multi-step run completes", "harness", fin.get("status") == "completed", fin, t0)
    active = [t for t in c.get("/api/tasks?status=active").json()["tasks"] if t.get("conversation_id") == conv]
    case("G", "no orphaned active task after completion", "harness", not active, [t["title"] for t in active], t0)

    # ── H error recovery ───────────────────────────────────────────────────
    t0 = time.time()
    fake.turns = [tool_turn("does.not.exist", {}), text_turn("Das Werkzeug gibt es nicht, ich melde das.")]
    _, fin = say(new_conv("errors"), "nutze das Werkzeug")
    fed_back = [m for m in fake.seen[-1] if m["role"] == "user" and isinstance(m["content"], list)
                and m["content"] and m["content"][0].get("type") == "tool_result"]
    case("H", "unknown tool: run survives", "harness", fin.get("status") == "completed", fin, t0)
    case("H", "unknown tool: model is told it was an error", "harness",
         fed_back and fed_back[-1]["content"][0].get("is_error"), fed_back[-1:] if fed_back else "nothing fed back", t0)
    fake.turns = [tool_turn("filesystem.read", {"path": "documents/gibt-es-nicht.md"}), text_turn("Datei fehlt.")]
    _, fin = say(new_conv("errors2"), "lies die Datei")
    case("H", "failing tool call does not crash the run", "harness", fin.get("status") == "completed", fin, t0)

    # ── I permissions: pause, then resume the SAME run ─────────────────────
    t0 = time.time()
    c.post("/api/files/write", json={"path": "documents/eval-del.md", "content": "x"}, headers=H)
    fake.turns = [tool_turn("filesystem.delete", {"path": "documents/eval-del.md", "reason": "eval"}),
                  text_turn("Gelöscht.")]
    run_id = c.post(f"/api/chat/conversations/{new_conv('perm')}/messages",
                    json={"content": "lösch die Datei", "stream": False}, headers=H).json()["run"]["id"]
    pending = wait_for(lambda: c.get("/api/approvals?status=pending").json()["approvals"])
    case("I", "approval-required action pauses", "harness",
         pending and pending[0]["action"] == "filesystem.delete", pending, t0)
    case("I", "nothing executed while waiting", "harness",
         (state.settings.workspace_dir / "documents" / "eval-del.md").exists(), "file already gone", t0)
    if pending:
        c.post(f"/api/approvals/{pending[0]['id']}/approve", json={}, headers=H)
    done = wait_for(lambda: state.runtime.get_run(run_id).status in ("completed", "failed"))
    case("I", "after approval the same run continues to completion", "harness",
         done and state.runtime.get_run(run_id).status == "completed", state.runtime.get_run(run_id).status, t0)
    case("I", "and the approved action then ran", "harness",
         not (state.settings.workspace_dir / "documents" / "eval-del.md").exists(), "still there", t0)
    ss = state.tools.get("server.status")
    case("I", "low-risk action needs no approval", "harness", ss is not None and not ss.needs_approval(), "", t0)

    # ── E agent routing ────────────────────────────────────────────────────
    t0 = time.time()
    scoped = [t.name for t in state.tools.for_agent(["memory.*"], "admin")]
    case("E", "specialist only receives tools its roster allows", "harness",
         scoped and all(n.startswith("memory.") for n in scoped) and "filesystem.delete" not in scoped, scoped[:6], t0)
    viewer = state.tools.for_agent(["*"], "viewer")
    case("E", "viewer role never receives operator-level tools", "harness",
         all(t.min_role == "viewer" for t in viewer), [t.name for t in viewer if t.min_role != "viewer"][:5], t0)
    case("E", "wildcard matching is prefix-exact, not substring", "harness",
         _matches("task.create", ["task.*"]) and not _matches("subtask.create", ["task.*"]), "", t0)
    fake.turns = [tool_turn("agent.delegate", {"agent_id": "research", "instruction": "find X", "title": "Eval Research"}),
                  text_turn("Ergebnis-E"), text_turn("Die Antwort ist Ergebnis-E.")]
    say(new_conv("route"), "recherchiere X")
    sub = [t for t in c.get("/api/tasks").json()["tasks"] if t["title"] == "Eval Research" and t["parent_id"]]
    case("E", "delegation reaches the requested specialist and returns to the master", "harness",
         sub and sub[0]["assigned_agent"] == "research" and sub[0]["status"] == "COMPLETED", sub, t0)

    # ── L self-healing: classify, bound retries, reap orphans ──────────────
    t0 = time.time()
    from command_center.backend.services import self_healing as SH  # noqa: PLC0415
    classify = getattr(SH, "classify_fault", None)
    case("L", "classify_fault exists", "harness", callable(classify), "missing", t0)
    if callable(classify):
        for text, kind in (("httpx.ConnectError: Connection refused", "service_unreachable"),
                           ("ModuleNotFoundError: No module named 'redis'", "missing_dependency"),
                           ("UnicodeDecodeError: 'utf-8' codec can't decode byte", "encoding"),
                           ("httpx.ReadTimeout", "timeout"),
                           ("model_not_found: The model does not exist", "model_config")):
            case("L", f"fault '{kind}' is recognised", "harness", classify(text).get("kind") == kind, classify(text), t0)
        case("L", "unrecognised text is 'unknown', not guessed", "harness",
             classify("völlig unbekannter Zustand 42").get("kind") == "unknown", classify("völlig unbekannter Zustand 42"), t0)

    class _StubMetrics:
        def __init__(self):
            self.actions: list[tuple[str, str]] = []

        async def docker_containers(self, with_stats=False):
            return {"status": "healthy", "containers": [
                {"id": "abc", "name": "mia-speaches-kerstin", "state": "exited", "status": "Exited (1)"}]}

        async def docker_action(self, cid, action):
            self.actions.append((cid, action))
            return "ok"

    import asyncio as _aio  # noqa: PLC0415
    real_metrics = state.services["metrics"]
    stub = _StubMetrics()
    state.services["metrics"] = stub
    heal = state.services["self_healing"]
    for _ in range(6):
        _aio.run(heal.run_once())
    case("L", "a permanently failing container is restarted at most 3 times per hour", "harness",
         len(stub.actions) <= 3, f"{len(stub.actions)} restarts in 6 cycles", t0)
    state.services["metrics"] = real_metrics

    from command_center.backend.services.self_healing import safe_containers  # noqa: PLC0415
    _prev_env = os.environ.pop("MIA_SELF_HEAL_CONTAINERS", None)
    case("L", "default recovery list names the real voice containers (mia-*), not the old jarvis-* names", "harness",
         safe_containers() == {"mia-live-voice", "mia-speaches-kerstin"}, safe_containers(), t0)
    os.environ["MIA_SELF_HEAL_CONTAINERS"] = "mia-live-voice, my-other-service, mia-edge-tts-DISABLED-cloud"
    case("L", "MIA_SELF_HEAL_CONTAINERS overrides the list", "harness",
         "my-other-service" in safe_containers() and "mia-speaches-kerstin" not in safe_containers(), safe_containers(), t0)
    case("L", "a deliberately disabled container is never auto-started, even if listed", "harness",
         "mia-edge-tts-DISABLED-cloud" not in safe_containers(), safe_containers(), t0)
    os.environ.pop("MIA_SELF_HEAL_CONTAINERS")
    if _prev_env is not None:
        os.environ["MIA_SELF_HEAL_CONTAINERS"] = _prev_env

    class _DisabledStub(_StubMetrics):
        async def docker_containers(self, with_stats=False):
            return {"status": "healthy", "containers": [
                {"id": "x1", "name": "mia-edge-tts-DISABLED-cloud", "state": "exited", "status": "Exited (137)"}]}

    real_metrics2 = state.services["metrics"]
    dstub = _DisabledStub()
    state.services["metrics"] = dstub
    _aio.run(heal.run_once())
    state.services["metrics"] = real_metrics2
    case("L", "run_once leaves the exited, deliberately disabled cloud-TTS container alone", "harness",
         dstub.actions == [], dstub.actions, t0)

    from datetime import timedelta  # noqa: PLC0415
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tk = state.services["tasks"]
    orphan = tk.create(title="EVAL-orphan", status="RUNNING")
    waiting = tk.create(title="EVAL-waiting", status="WAITING_FOR_APPROVAL")
    fresh = tk.create(title="EVAL-fresh", status="RUNNING")
    for tid in (orphan["id"], waiting["id"]):
        state.db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
    _aio.run(heal.run_once())
    case("L", "orphaned RUNNING task (no run, stale) is closed as FAILED with a cause", "harness",
         tk.get(orphan["id"])["status"] == "FAILED" and "orphan" in (tk.get(orphan["id"])["error"] or "").lower(),
         tk.get(orphan["id"]), t0)
    case("L", "a task waiting for approval is never reaped", "harness",
         tk.get(waiting["id"])["status"] == "WAITING_FOR_APPROVAL", tk.get(waiting["id"])["status"], t0)
    case("L", "a fresh RUNNING task is left alone", "harness", tk.get(fresh["id"])["status"] == "RUNNING", "", t0)

    # ── M core evolution: a candidate is compared with the baseline ─────────
    t0 = time.time()
    import hashlib  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415
    evo = state.services["core_evolution"]
    src_root = _P(tempfile.mkdtemp(prefix="mia-evo-src-"))
    (src_root / "pkg").mkdir()
    base_src = "def keep():\n    return 1\n\ndef also_keep():\n    return 2\n"
    (src_root / "pkg" / "mod.py").write_text(base_src, encoding="utf-8")
    evo.root = src_root.resolve()

    def _propose(pid: str, candidate: str) -> None:
        draft = src_root / f"draft-{pid}.py"
        draft.write_text(candidate, encoding="utf-8")
        state.db.insert("self_tools", {
            "name": f"proposal:{pid}", "file": str(draft),
            "description": "CAPABILITY_GAIN: x EVIDENCE: y VERIFICATION_PLAN: z", "risk": "medium",
            "status": "draft", "source_sha": hashlib.sha256(candidate.encode()).hexdigest()[:16],
            "last_error": "pkg/mod.py", "created_at": now_iso_eval(), "updated_at": now_iso_eval()})

    def now_iso_eval() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _propose("evalreg", "def keep():\n    return 1\n")                              # drops also_keep
    _propose("evalok", base_src + "\ndef added():\n    return 3\n")                   # keeps everything
    def _verify(pid: str) -> dict:
        try:
            return _aio.run(evo.verify(pid))
        except Exception as e:  # noqa: BLE001 — a crashing verify is a measured failure, not a crashed eval
            return {"status": "error", "checks": [], "error": f"{type(e).__name__}: {e}"}

    res_bad, res_ok = _verify("evalreg"), _verify("evalok")
    case("M", "verify runs on a real database at all", "harness", res_ok.get("status") != "error",
         res_ok.get("error", ""), t0)
    chk = lambda res: {x["name"]: x for x in res["checks"]}  # noqa: E731
    case("M", "verify compares candidate against baseline (public API check exists)", "harness",
         "public_api_preserved" in chk(res_bad), list(chk(res_bad)), t0)
    case("M", "a candidate that removes a public function is rejected", "harness",
         chk(res_bad).get("public_api_preserved", {}).get("ok") is False and res_bad["status"] == "failed", res_bad["status"], t0)
    case("M", "a candidate that only adds passes the comparison", "harness",
         chk(res_ok).get("public_api_preserved", {}).get("ok") is True, chk(res_ok).get("public_api_preserved"), t0)
    case("M", "verification records a before/after benchmark", "harness",
         "benchmark" in chk(res_ok) and "detail" in chk(res_ok).get("benchmark", {}), list(chk(res_ok)), t0)

    # ── A conversation / J hallucination guardrails ─────────────────────────
    t0 = time.time()
    conv = new_conv("ctx")
    fake.turns = [text_turn("Alles klar.")]
    say(conv, "Mein Kunde heißt Müller-EVALCTX.")
    fake.turns = [text_turn("Ok.")]
    say(conv, "Wie hieß er noch?")
    case("A", "earlier turns reach the model on a follow-up (context kept)", "harness",
         "Müller-EVALCTX" in json.dumps(fake.seen[-1], ensure_ascii=False), "", t0)
    case("J", "prompt tells the model not to guess about known topics", "harness",
         re.search(r"rate nicht|do not guess|never invent|not invent", fake.systems[-1], re.I) is not None, "", t0)
    case("J", "prompt forbids storing unverified claims as trusted knowledge", "harness",
         "unverified" in fake.systems[-1].lower(), "", t0)
    case("B", "reasoning quality needs a live model (harness cannot judge it)", "live", True, "live only", skipped=True)
    case("C", "coding quality needs a live model (harness cannot judge it)", "live", True, "live only", skipped=True)

    from command_center.backend.modules.heartbeat import _DEFAULT_PINGS  # noqa: PLC0415
    case("L", "heartbeat pings the real voice containers (mia-*), not the old names", "harness",
         "mia-live-voice" in _DEFAULT_PINGS and "mia-speaches-kerstin" in _DEFAULT_PINGS and "jarvis-" not in _DEFAULT_PINGS,
         _DEFAULT_PINGS, t0)

    # ── N communication intelligence layer (Section 21, tests A-J) ──────────
    comm = state.services["communication"]
    chat_store = state.services["chat"]
    tk = state.services["tasks"]
    state.db.execute("UPDATE tasks SET status='CANCELLED' WHERE status IN "
                     "('QUEUED','PLANNING','RUNNING','PAUSED','WAITING_FOR_APPROVAL')")

    def refs(u: dict, kind: str | None = None) -> list[dict]:
        return [r for r in u["references"] if kind is None or r["kind"] == kind]

    t0 = time.time()
    conv = new_conv("comm-a")
    task = tk.create(title="EVAL Chat reparieren", status="RUNNING", conversation_id=conv)
    u = comm.understand(conv, "mach weiter")
    case("N", "A: 'mach weiter' resolves the one active task of this conversation", "harness",
         u["intent"] == "continue" and refs(u, "task") and refs(u, "task")[0].get("id") == task["id"]
         and u["policy"] == "proceed", u["references"], t0)
    tk.create(title="EVAL zweiter Task", status="RUNNING", conversation_id=conv)
    u2 = comm.understand(conv, "mach weiter")
    case("N", "A: with two active tasks it does not pick one (ambiguous -> ask)", "harness",
         refs(u2, "task") and refs(u2, "task")[0]["status"] == "ambiguous" and u2["policy"] == "ask", u2["references"], t0)
    state.db.execute("UPDATE tasks SET status='CANCELLED' WHERE conversation_id=?", (conv,))

    t0 = time.time()
    conv = new_conv("comm-b")
    chat_store.add_message(conv, "user", "bau mir den Chat sauber ein, oben die Kopfzeile und unten die Eingabe")
    chat_store.add_message(conv, "assistant", "Kopfzeile und Eingabe sind eingebaut.")
    u = comm.understand(conv, "nee, nicht so, lass den oberen Teil wie er ist")
    case("N", "B: a correction adjusts the earlier order instead of restarting", "harness",
         u["intent"] == "correction" and refs(u) and refs(u)[0]["status"] == "resolved"
         and "nicht von vorn" in u["brief"], (u["intent"], u["references"]), t0)

    t0 = time.time()
    conv = new_conv("comm-c")
    chat_store.add_message(conv, "user", "Der Chat zeigt seit heute Fehler")
    chat_store.add_message(conv, "assistant", "Ich schaue mir den Chat an.")
    u = comm.understand(conv, "guck warum das rot ist und reparier das")
    case("N", "C: 'why is that red' resolves to the component from the conversation (Chat)", "harness",
         refs(u) and refs(u)[0]["status"] == "resolved" and refs(u)[0]["label"] == "Chat", u["references"], t0)
    # earlier categories left failures/alerts behind; age them out so "nothing is wrong anywhere" is a real state
    state.db.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00Z' WHERE status='FAILED'")
    state.db.execute("UPDATE notifications SET read=1")
    u = comm.understand(new_conv("comm-c2"), "guck warum das rot ist und reparier das")
    case("N", "C: with no failure known anywhere it says so instead of picking something", "harness",
         refs(u) and refs(u)[0]["status"] == "unresolved" and u["policy"] == "ask", u["references"], t0)
    tk.create(title="EVAL Export fehlgeschlagen", status="FAILED", conversation_id=None)
    u = comm.understand(new_conv("comm-c3"), "guck warum das rot ist")
    case("N", "C: exactly one recent failure in the system is identified as the referent", "harness",
         refs(u) and refs(u)[0]["status"] == "resolved" and "Export" in refs(u)[0]["label"], u["references"], t0)
    state.db.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00Z' WHERE status='FAILED'")

    t0 = time.time()
    u = comm.understand(new_conv("comm-d"), "mach erst den Chat fertig und danach die Stimme")
    case("N", "D: two tasks in the stated order", "harness",
         u["subtasks"] == ["den Chat fertig", "die Stimme"] and "1. den Chat fertig" in u["brief"], u["subtasks"], t0)
    u = comm.understand(new_conv("comm-d2"), "reparier erst den Chat, dann die Stimme und prüf danach alles")
    case("N", "D: three tasks with a trailing 'check everything' keep order and dependency", "harness",
         u["subtasks"] == ["reparier den Chat", "die Stimme", "prüf alles"], u["subtasks"], t0)

    t0 = time.time()
    u = comm.understand(new_conv("comm-e"), "schau ma bei dashbord und dem servr nach dem Trockenbauprofil")
    case("N", "E: typos are repaired from the project vocabulary", "harness",
         "dashboard" in u["normalized"] and "server" in u["normalized"] and set(u["components"]) >= {"Dashboard", "Server"},
         u["normalized"], t0)
    case("N", "E: ordinary words are left alone (no over-correction)", "harness",
         "trockenbauprofil" in u["normalized"].lower(), u["normalized"], t0)

    t0 = time.time()
    u = comm.understand(new_conv("comm-f"), "ähm also prüf mal das back end und den front end und die sprachsteuerng", voice=True)
    case("N", "F: spoken command with STT errors is normalised via context vocabulary", "harness",
         "backend" in u["normalized"] and "frontend" in u["normalized"] and "sprachsteuerung" in u["normalized"]
         and u["intent"] == "order" and "ähm" not in u["normalized"], u["normalized"], t0)

    t0 = time.time()
    u = comm.understand(new_conv("comm-g"), "mach das")
    case("N", "G: a genuinely ambiguous order invents no meaning and asks", "harness",
         u["policy"] == "ask" and refs(u) and refs(u)[0]["status"] == "unresolved"
         and "NICHT BESTIMMBAR" in u["brief"] and "raten" in u["brief"], (u["policy"], u["references"]), t0)

    t0 = time.time()
    conv = new_conv("comm-h")
    u = comm.understand(conv, "mach das wie gestern")
    case("N", "H: 'like yesterday' with no record does not fabricate one", "harness",
         refs(u, "history") and refs(u, "history")[0]["status"] == "unresolved" and u["policy"] == "ask", u["references"], t0)
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    done = tk.create(title="EVAL Angebot-Vorlage erstellt", status="COMPLETED")
    state.db.execute("UPDATE tasks SET completed_at=? WHERE id=?", (yday.isoformat(), done["id"]))
    u = comm.understand(conv, "mach das wie gestern")
    case("N", "H: with real work from yesterday on record it is used and named", "harness",
         refs(u, "history") and refs(u, "history")[0]["status"] == "resolved"
         and "Angebot-Vorlage" in refs(u, "history")[0]["label"], u["references"], t0)

    t0 = time.time()
    from command_center.backend.services.repos import RepoService  # noqa: PLC0415
    repos = RepoService(state.services["files"])
    (repos.root / "eval-repo" / ".git").mkdir(parents=True, exist_ok=True)
    u = comm.understand(new_conv("comm-i"), "und jetzt push das")
    case("N", "I: 'now push that' resolves the only known repository", "harness",
         refs(u, "repo") and refs(u, "repo")[0]["status"] == "resolved" and refs(u, "repo")[0]["label"] == "eval-repo",
         u["references"], t0)
    (repos.root / "zweites-repo" / ".git").mkdir(parents=True, exist_ok=True)
    u = comm.understand(new_conv("comm-i2"), "und jetzt push das")
    case("N", "I: with two repositories and no hint it asks which", "harness",
         refs(u, "repo") and refs(u, "repo")[0]["status"] == "ambiguous" and u["policy"] == "ask", u["references"], t0)

    t0 = time.time()
    conv = new_conv("comm-j")
    chat_store.add_message(conv, "user", "bau den Chat sauber ein")
    chat_store.add_message(conv, "assistant", "Der Chat ist eingebaut.")
    u = comm.understand(conv, "prüf danach alles")
    case("N", "J: 'check everything afterwards' is scoped to the area just worked on", "harness",
         refs(u, "scope") and "Chat" in refs(u, "scope")[0]["label"] and u["policy"] == "proceed", u["references"], t0)

    t0 = time.time()
    u = comm.understand(new_conv("comm-j2"), "prüf danach alles")
    case("N", "J: 'check everything' in a fresh chat adds no bogus unresolved task reference", "harness",
         [r["kind"] for r in u["references"]] == ["scope"] and u["policy"] == "proceed", u["references"], t0)
    u = comm.understand(new_conv("comm-art"), "prüf das backend")
    case("N", "'check the backend': 'das' is an article, not an unresolved reference", "harness",
         u["references"] == [] and u["confidence"] == "high", (u["references"], u["confidence"]), t0)

    # ── O scheduler: a permanently failing job backs off instead of hammering ──
    import asyncio as _aio2  # noqa: PLC0415
    from command_center.backend.modules.automations.scheduler import Scheduler  # noqa: PLC0415
    t0 = time.time()
    class _RecLog:
        def __init__(self):
            self.rows: list[tuple[str, str]] = []

        def __getattr__(self, level):
            return lambda src, msg, **kw: self.rows.append((level, msg))

    reclog = _RecLog()
    sched = Scheduler(state.bus, reclog)
    flaky = {"fail": True, "calls": 0}

    async def _job():
        flaky["calls"] += 1
        if flaky["fail"]:
            raise RuntimeError("Server error '500 Internal Server Error'")

    with_bo = sched.add("eval-bo", "EVAL backoff", 180, _job, backoff_max=1800, run_immediately=False)
    without = sched.add("eval-plain", "EVAL plain", 180, _job, run_immediately=False)
    delays = []
    for _ in range(6):
        _aio2.run(sched._run_once(with_bo))
        delays.append(int(sched.next_delay(with_bo)))
    case("O", "failing job: interval doubles per failure (360, 720, 1440, ...)", "harness",
         delays[:3] == [360, 720, 1440], delays, t0)
    case("O", "failing job: interval is capped at backoff_max (1800 s)", "harness",
         max(delays) == 1800 and delays[-1] == 1800, delays, t0)
    _aio2.run(sched._run_once(without))
    _aio2.run(sched._run_once(without))
    case("O", "a job without backoff_max keeps its normal interval (no change for other jobs)", "harness",
         int(sched.next_delay(without)) == 180, sched.next_delay(without), t0)
    flaky["fail"] = False
    _aio2.run(sched._run_once(with_bo))
    case("O", "first success resets the interval to normal and clears the failure count", "harness",
         int(sched.next_delay(with_bo)) == 180 and with_bo.consecutive_errors == 0 and with_bo.last_status == "ok",
         (sched.next_delay(with_bo), with_bo.consecutive_errors), t0)
    case("O", "errors stay counted and visible after backing off", "harness",
         with_bo.errors == 6 and with_bo.public()["consecutive_errors"] == 0, with_bo.errors, t0)
    n_err_logs = sum(1 for lv, m in reclog.rows if lv == "error" and "EVAL backoff" in m)
    n_quiet = sum(1 for lv, m in reclog.rows if lv == "debug" and "EVAL backoff" in m and "same cause" in m)
    case("O", "the same failure repeated 6x is logged as ERROR once, the 5 repeats quietly", "harness",
         n_err_logs == 1 and n_quiet == 5, (n_err_logs, n_quiet), t0)
    n_plain = sum(1 for lv, m in reclog.rows if lv == "error" and "EVAL plain" in m)
    case("O", "without backoff every failure is still logged as ERROR (unchanged behaviour)", "harness",
         n_plain == 2, n_plain, t0)

    # ── P integrations: a masked-copy token is named, not reported as a codec error ──
    import asyncio as _aio3  # noqa: PLC0415
    from command_center.backend.adapters.integrations import GitHubIntegration  # noqa: PLC0415
    t0 = time.time()
    gh = GitHubIntegration("github", "GitHub", "code", [], required_env=["GITHUB_TOKEN"])
    _old_tok = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "ghp_ab\u2022\u2022\u2022\u2022\u2022\u2022"
    res = _aio3.run(gh.check())
    case("P", "GitHub token with masking bullets is reported as such (offline, names the cause)", "harness",
         res["status"] == "offline" and "maskiert" in res["detail"] and "codec" not in res["detail"], res, t0)
    os.environ["GITHUB_TOKEN"] = "ghp_with space"
    case("P", "GitHub token containing whitespace is rejected before any request", "harness",
         _aio3.run(gh.check())["status"] == "offline", "", t0)
    os.environ.pop("GITHUB_TOKEN")
    case("P", "no token stays 'not_configured' (unchanged)", "harness", _aio3.run(gh.check())["status"] == "not_configured", "", t0)
    if _old_tok is not None:
        os.environ["GITHUB_TOKEN"] = _old_tok

    # ── Q local model: stable prefix, warm-up, timeout ───────────────────────
    import asyncio as _aio4  # noqa: PLC0415
    from command_center.backend.ai.openai_compat import OpenAICompatProvider  # noqa: PLC0415
    from command_center.backend.services.local_warmup import LocalWarmup  # noqa: PLC0415
    t0 = time.time()
    _old_to = os.environ.pop("LOCAL_LLM_TIMEOUT", None)
    case("Q", "local provider waits up to 25 min between chunks by default (cold CPU prompt)", "harness",
         OpenAICompatProvider("local", "http://x", "", "m").read_timeout == 1500.0, "", t0)
    case("Q", "cloud providers keep the 300 s wait", "harness",
         OpenAICompatProvider("openai", "http://x", "", "m").read_timeout == 300.0, "", t0)
    os.environ["LOCAL_LLM_TIMEOUT"] = "42"
    case("Q", "LOCAL_LLM_TIMEOUT overrides the local wait", "harness",
         OpenAICompatProvider("local", "http://x", "", "m").read_timeout == 42.0, "", t0)
    os.environ.pop("LOCAL_LLM_TIMEOUT")
    if _old_to is not None:
        os.environ["LOCAL_LLM_TIMEOUT"] = _old_to

    cloud = fake
    localp = ScriptedProvider("local")
    state.runtime.provider = localp
    conv = new_conv("q-local")
    localp.turns = [text_turn("Ok.")]
    say(conv, "Wie spät ist es gerade?")
    localp.turns = [text_turn("Ok.")]
    say(conv, "Such mir bitte die Wettervorhersage für Freitag im Web und schick eine Mail an den Kunden.")
    s1, s2 = localp.systems[-2], localp.systems[-1]
    case("Q", "local: system prompt is byte-identical for two different questions (cacheable prefix)", "harness",
         s1 == s2, f"{len(s1)} vs {len(s2)} chars", t0)
    case("Q", "local: tool list does not depend on the question wording", "harness",
         localp.tools_seen[-2] == localp.tools_seen[-1] and len(localp.tools_seen[-1]) > 5, [len(x) for x in localp.tools_seen[-2:]], t0)
    case("Q", "local: no clock in the system prompt (it would break the cache every minute)", "harness",
         "now=" not in s2 and "ENVIRONMENT:" in s2, "", t0)
    def _last_user(prov) -> str:
        return json.dumps([m for m in prov.seen[-1] if m["role"] == "user"][-1], ensure_ascii=False)

    last_user = _last_user(localp)
    case("Q", "local: the current time travels with the user message instead", "harness",
         "[Kontext zu dieser Anfrage" in last_user, last_user[:160], t0)
    localp.turns = [text_turn("Ok.")]
    say(conv, "mach weiter")
    case("Q", "local: the understanding note is delivered with the user message, not in the system prompt", "harness",
         "VERSTÄNDNIS-NOTIZ" in _last_user(localp) and "VERSTÄNDNIS-NOTIZ" not in localp.systems[-1],
         "", t0)
    stored = c.get(f"/api/chat/conversations/{conv}").json()["messages"]
    case("Q", "local: injected context is never stored in the visible chat", "harness",
         all("[Kontext zu dieser Anfrage" not in m["content"] and "VERSTÄNDNIS-NOTIZ" not in m["content"] for m in stored), "", t0)

    warm = LocalWarmup(state)
    res = _aio4.run(warm.run_once())
    warm_system, warm_tools = localp.systems[-1], localp.tools_seen[-1]
    localp.turns = [text_turn("Ok.")]
    say(conv, "und was ist mit dem Kalender morgen?")
    case("Q", "warm-up sends exactly the prefix (system + tools) a real chat sends", "harness",
         res.get("ok") is True and localp.systems[-1] == warm_system and localp.tools_seen[-1] == warm_tools, res, t0)
    state.runtime.provider = cloud
    case("Q", "warm-up does nothing for a cloud provider", "harness",
         "skipped" in _aio4.run(warm.run_once()), "", t0)
    cloud.turns = [text_turn("Ok.")]
    say(new_conv("q-cloud"), "Wie spät ist es gerade?")
    case("Q", "cloud provider path is unchanged: clock still in the system prompt, no context block in the user message", "harness",
         "now=" in cloud.systems[-1] and "[Kontext zu dieser Anfrage" not in _last_user(cloud), "", t0)
    state.runtime.provider = fake

    # ── R local model probe must not be cancelled mid-load by the 15 s health check ──
    import asyncio as _aio5  # noqa: PLC0415
    from command_center.backend.adapters.integrations import LocalLLMIntegration  # noqa: PLC0415
    t0 = time.time()
    calls = {"probe": 0, "models": []}

    async def _slow_probe(self, timeout=45.0):
        calls["probe"] += 1
        calls["models"].append(self.info.model)
        await _aio5.sleep(0.6)                       # stands for a cold model load
        return True, "ruft Werkzeuge auf"

    async def _healthy(self):
        return {"status": "healthy", "detail": "1 models listed"}

    _orig = (OpenAICompatProvider.tool_check, OpenAICompatProvider.health)
    OpenAICompatProvider.tool_check, OpenAICompatProvider.health = _slow_probe, _healthy
    os.environ["LOCAL_LLM_URL"] = "http://127.0.0.1:9"
    os.environ["LOCAL_LLM_MODEL"], os.environ["JARVIS_FAST_MODEL"] = "big-model:7b", "small-model:3b"
    LocalLLMIntegration._tools_ok, LocalLLMIntegration._probe = None, None
    llm = LocalLLMIntegration("local_llm", "Local", "ai", [], required_env=["LOCAL_LLM_URL"])

    async def _scenario():
        started = time.time()
        first = await llm.check()
        quick = time.time() - started
        again = await llm.check()                   # while the probe is still running: no second probe
        await _aio5.sleep(0.9)
        final = await llm.check()
        return first, quick, again, final

    first, quick, again, final = _aio5.run(_scenario())
    case("R", "first check returns at once (does not wait for the probe) and says it is pending", "harness",
         quick < 0.3 and first["status"] == "degraded" and "Hintergrund" in first["detail"], (quick, first), t0)
    case("R", "a second check while the probe runs does not start another probe", "harness",
         calls["probe"] == 1 and "Hintergrund" in again["detail"], calls, t0)
    case("R", "after the probe finished the result is remembered and reported healthy", "harness",
         final["status"] == "healthy" and calls["probe"] == 1, final, t0)
    case("R", "the probe targets the default chat model, not the large one (no eviction of the resident model)", "harness",
         calls["models"] == ["small-model:3b"], calls, t0)
    OpenAICompatProvider.tool_check, OpenAICompatProvider.health = _orig
    for _k in ("LOCAL_LLM_URL", "LOCAL_LLM_MODEL", "JARVIS_FAST_MODEL"):
        os.environ.pop(_k, None)
    LocalLLMIntegration._tools_ok, LocalLLMIntegration._probe = None, None

    # safety and invisibility
    t0 = time.time()
    state.services["approvals"].request(action="EVAL deploy", reason="eval", target="x", risk="high", requested_by="eval")
    pending_before = state.services["approvals"].pending_count()
    conv = new_conv("comm-safe")
    fake.turns = [text_turn("Verstanden.")]
    say(conv, "ja")
    case("N", "a chat 'ja' does not grant a pending approval", "harness",
         state.services["approvals"].pending_count() == pending_before and pending_before > 0,
         (pending_before, state.services["approvals"].pending_count()), t0)
    conv = new_conv("comm-e2e")
    fake.turns = [text_turn("Ok.")]
    say(conv, "mach weiter")
    stored = c.get(f"/api/chat/conversations/{conv}").json()["messages"]
    case("N", "the internal brief reaches the model (system prompt) ...", "harness",
         "VERSTÄNDNIS-NOTIZ" in fake.systems[-1], "", t0)
    case("N", "... but never appears in the visible chat and the user's text is stored unchanged", "harness",
         all("VERSTÄNDNIS-NOTIZ" not in m["content"] for m in stored)
         and [m["content"] for m in stored if m["role"] == "user"] == ["mach weiter"], [m["content"][:60] for m in stored], t0)
    long_msg = ("Bitte erstelle für das Bauvorhaben Musterstraße 12 ein ausführliches Angebot mit allen Positionen der "
                "Trockenbauarbeiten im Erdgeschoss, inklusive Materialliste und Zeitplan für drei Wochen.")
    case("N", "a long, fully specified request gets no brief (no noise)", "harness",
         comm.understand(new_conv("comm-long"), long_msg)["brief"] == "", "", t0)

    t0 = time.time()
    seen = comm.understand(new_conv("comm-learn"), "prüf das nochmal") | {"policy": "proceed"}
    results = [comm.observe(seen) for _ in range(3)]
    ledger = state.services["learning"].search("Kurzbefehl prüf das nochmal", kinds=("preference",))
    case("N", "a phrase is stored as a habit only after 3 confirmed uses", "harness",
         not results[0]["promoted"] and not results[1]["promoted"] and results[2]["promoted"] and bool(ledger),
         results, t0)
    q = comm.understand(new_conv("comm-q"), "Wie spät ist es eigentlich in Berlin?")
    case("N", "questions/plain information are never stored as habits", "harness", comm.observe(q) is None, q["intent"], t0)

# ── report ────────────────────────────────────────────────────────────────
summary = {"total": len(CASES), "passed": sum(x["status"] == "passed" for x in CASES),
           "failed": sum(x["status"] == "failed" for x in CASES), "skipped": sum(x["status"] == "skipped" for x in CASES)}
print("\nper category")
for cat in sorted({x["category"] for x in CASES}):
    rows = [x for x in CASES if x["category"] == cat]
    skipped = sum(x["status"] == "skipped" for x in rows)
    print(f"  {cat}: {sum(x['status'] == 'passed' for x in rows)}/{len(rows)} passed" + (f", {skipped} skipped" if skipped else ""))
print(f"\n{summary['passed']}/{summary['total']} passed, {summary['failed']} failed, {summary['skipped']} skipped")

RESULTS_DIR.mkdir(exist_ok=True)
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out = RESULTS_DIR / f"{ts}-{LABEL}.json"
out.write_text(json.dumps({"label": LABEL, "ts": ts, "live": LIVE, "summary": summary, "cases": CASES},
                          ensure_ascii=False, indent=2), encoding="utf-8")
print(f"results: {out.relative_to(ROOT)}")
sys.exit(1 if summary["failed"] else 0)
