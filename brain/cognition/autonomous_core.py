from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATES = {
    "DETECTED", "PLANNED", "QUEUED", "EXECUTING", "VERIFYING",
    "DONE", "WAITING_FOR_DATA", "WAITING_FOR_APPROVAL", "BLOCKED",
    "FAILED", "CANCELLED",
}
_STATUS = {
    "DETECTED": "detected", "PLANNED": "planned", "QUEUED": "pending",
    "EXECUTING": "in_progress", "VERIFYING": "verifying", "DONE": "done",
    "WAITING_FOR_DATA": "waiting_for_data",
    "WAITING_FOR_APPROVAL": "waiting_approval", "BLOCKED": "blocked",
    "FAILED": "failed", "CANCELLED": "cancelled",
}
_RISK_RE = re.compile(
    r"(?i)\b(send(?:e|en|est|et)?|versend\w*|verschick\w*|e-?mail(?:en)?|kundenmail|whatsapp|bestell|kauf|bezah|zahlung|"
    r"überweis|ueberweis|vertrag|preiszusag|preis zusag|lösch|loesch|delete|"
    r"shutdown|restart|reboot|deploy|publish|credential|passwort|password|token)\b"
)
_PROMISE_RE = re.compile(
    r"(?i)\b(ich werde|werde ich|ich versuche es|ich mache das jetzt|"
    r"i will|i'll|let me|i am going to|i'm going to|will now use)\b"
)
_FAILURE_MARKERS = (
    "fehler", "error", "fehlgeschlagen", "failed", "couldn't get",
    "could not get", "could not be retrieved", "returned false",
    "resource_exhausted", "quota", "nicht erreichbar", "unreachable",
    "timeout", "unbekanntes tool", "unknown tool", "search failed",
)
_GENERIC_REPORT_WORDS = {
    "aktuell", "aktuelle", "aktueller", "aktuellen", "antwortet", "ausgefuehrt", "ausgeführt",
    "auslastung", "cpu-auslastung", "ram-auslastung", "ram-auslastungen", "erfolgreich", "ergebnis",
    "festplatte", "gefunden", "gemessen", "gemessenen", "geprueft", "geprüft", "intern", "interne",
    "konkret", "meldung", "server", "status", "system", "systems", "systemstatus", "tool", "verfugbaren",
    "verfügbaren", "verwendet", "werkzeug", "dienst", "zustand", "active", "checked", "current",
    "result", "service", "successful",
}
_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9_.:/%\-]{3,}")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?\s*(?:%|gb|h|°c)?", re.IGNORECASE)


def _fold_token(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _grounding_audit(answer: str, evidence_text: str) -> dict[str, Any]:
    """Deterministic guard against concrete facts invented after a tool call.

    Numeric measurements are strong evidence and count more than prose anchors.
    Generic reporting language is ignored. Unsupported concrete claims still
    push the score down, so a fluent answer cannot outrank the tool evidence.
    """
    evidence_folded = _fold_token(evidence_text)
    text_anchors: list[str] = []
    text_supported: list[str] = []
    text_unsupported: list[str] = []

    def _num(value: str) -> str:
        normalized = re.sub(r"\s+", "", str(value or "").casefold()).replace(",", ".")
        return re.sub(r"(?:%|gb|h|°c)$", "", normalized)

    evidence_numbers = {_num(raw) for raw in _NUMBER_RE.findall(evidence_text or "")}
    answer_numbers: list[str] = []
    for raw in _NUMBER_RE.findall(answer or ""):
        number = _num(raw)
        if number and number not in answer_numbers:
            answer_numbers.append(number)
    numbers_supported = [number for number in answer_numbers if number in evidence_numbers]
    numbers_unsupported = [number for number in answer_numbers if number not in evidence_numbers]

    for raw in _TOKEN_RE.findall(answer or ""):
        token = _fold_token(raw.strip(".,;:!?()[]{}\"'"))
        if not token or token in _GENERIC_REPORT_WORDS:
            continue
        if any(ch.isdigit() for ch in token):
            continue
        if len(token) < 7 or token in text_anchors:
            continue
        text_anchors.append(token)
        prefix = token[:6]
        if token in evidence_folded or prefix in evidence_folded:
            text_supported.append(token)
        else:
            text_unsupported.append(token)

    numeric_weight = 3
    total_weight = numeric_weight * len(answer_numbers) + len(text_anchors)
    supported_weight = numeric_weight * len(numbers_supported) + len(text_supported)
    checked = total_weight >= 3
    score = (supported_weight / total_weight) if total_weight else 1.0
    return {
        "checked": checked,
        "anchors": len(answer_numbers) + len(text_anchors),
        "supported": len(numbers_supported) + len(text_supported),
        "unsupported": len(numbers_unsupported) + len(text_unsupported),
        "score": round(score, 3),
        "numeric_anchors": len(answer_numbers),
        "numeric_supported": len(numbers_supported),
        "unsupported_sample": (numbers_unsupported + text_unsupported)[:8],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _clean(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]

def _task_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _parse_due_at(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("run_at is required")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(timezone.utc)

@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str
    evidence: dict[str, Any]
    fallback_result: str = ""

class AutonomousBrain:
    """Operational autonomy layer for MIA.

    It stores observable state only: events, task lifecycle, policy decisions,
    execution evidence and health. It never persists hidden reasoning.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.root = Path(project_root or Path(__file__).resolve().parents[2])
        self.queue = self.root / "tasks" / "queue"
        self.done = self.root / "tasks" / "done"
        self.inbox = self.root / "brain" / "events" / "inbox"
        self.processed = self.root / "brain" / "events" / "processed"
        self.working = self.root / "brain" / "memory" / "working"
        self.triggers_path = self.working / "proactive_triggers.json"
        self.audit_path = self.working / "autonomy_audit.jsonl"
        self.health_path = self.root / "brain" / "system" / "health" / "autonomy-health.json"
        for path in (self.queue, self.done, self.inbox, self.processed, self.working, self.health_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def _audit(self, kind: str, payload: dict[str, Any]) -> None:
        row = {"time": _now(), "type": kind, **payload}
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def classify_action(self, goal: str, requested: str = "auto", source: str = "system") -> str:
        requested = str(requested or "auto").lower()
        if requested in {"advice", "advice_only"}:
            return "advice_only"
        if requested in {"approval", "confirm"}:
            return "approval"
        # A direct user request is itself an instruction. Tool-specific confirmation
        # rules still remain authoritative for sending, buying, deleting, etc.
        if source == "user":
            return "auto"
        if _RISK_RE.search(goal or ""):
            return "approval"
        return "auto"

    def create_task(
        self,
        goal: str,
        *,
        source: str = "system",
        autonomy: str = "auto",
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal = _clean(goal, 4000)
        if not goal:
            raise ValueError("goal is required")
        tid = _task_id()
        policy = self.classify_action(goal, autonomy, source)
        state = "WAITING_FOR_APPROVAL" if policy == "approval" else "QUEUED"
        task = {
            "id": tid,
            "goal": goal,
            "state": state,
            "status": _STATUS[state],
            "created": time.time(),
            "created_at": _now(),
            "updated_at": _now(),
            "started_at": None,
            "finished_at": None,
            "attempts": 0,
            "result": None,
            "source": source,
            "policy": policy,
            "reason": _clean(reason, 1000),
            "metadata": metadata or {},
            "execution_evidence": {},
        }
        (self.queue / f"{tid}.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._audit("task_created", {"task_id": tid, "state": state, "source": source, "policy": policy})
        self.write_health()
        return task

    def transition(self, task: dict[str, Any], state: str, **extra: Any) -> dict[str, Any]:
        state = str(state).upper()
        if state not in STATES:
            raise ValueError(f"invalid task state: {state}")
        old = str(task.get("state") or "").upper()
        task["state"] = state
        task["status"] = _STATUS[state]
        task["updated_at"] = _now()
        if state == "EXECUTING" and not task.get("started_at"):
            task["started_at"] = _now()
        if state in {"DONE", "FAILED", "CANCELLED"}:
            task["finished_at"] = _now()
            task["finished"] = time.time()
        task.update(extra)
        self._audit("task_transition", {"task_id": task.get("id"), "from": old, "to": state})
        return task

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "system",
        priority: int = 50,
        dedupe_key: str = "",
    ) -> dict[str, Any]:
        payload = payload or {}
        raw = json.dumps({"type": event_type, "payload": payload, "source": source}, sort_keys=True, ensure_ascii=False)
        if dedupe_key:
            eid = "evt-" + hashlib.sha256(str(dedupe_key).encode("utf-8")).hexdigest()[:16]
            for folder in (self.inbox, self.processed):
                existing = folder / f"{eid}.json"
                if existing.exists():
                    try:
                        data = json.loads(existing.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        pass
        else:
            eid = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha256(raw.encode()).hexdigest()[:8]
        event = {
            "id": eid, "type": _clean(event_type, 120), "source": _clean(source, 120),
            "priority": int(priority), "created_at": _now(), "payload": payload, "state": "DETECTED",
        }
        (self.inbox / f"{eid}.json").write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        self._audit("event_detected", {"event_id": eid, "event_type": event["type"], "source": event["source"]})
        return event

    def _load_triggers(self) -> list[dict[str, Any]]:
        try:
            if self.triggers_path.exists():
                data = json.loads(self.triggers_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [row for row in data if isinstance(row, dict)]
        except Exception:
            pass
        return []

    def _save_triggers(self, rows: list[dict[str, Any]]) -> None:
        self.triggers_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.triggers_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.triggers_path)

    def register_time_trigger(
        self,
        goal: str,
        run_at: str,
        *,
        source: str = "system",
        autonomy: str = "auto",
        reason: str = "scheduled_followup",
        priority: int = 60,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal = _clean(goal, 4000)
        if not goal:
            raise ValueError("goal is required")
        due = _parse_due_at(run_at)
        trigger = {
            "id": "trg-" + uuid.uuid4().hex[:12],
            "kind": "time",
            "goal": goal,
            "run_at": due.isoformat(timespec="seconds"),
            "source": _clean(source, 120) or "system",
            "autonomy": str(autonomy or "auto").lower(),
            "reason": _clean(reason, 300),
            "priority": int(priority),
            "metadata": metadata or {},
            "state": "SCHEDULED",
            "created_at": _now(),
            "fired_at": None,
            "event_id": None,
        }
        rows = self._load_triggers()
        rows.append(trigger)
        self._save_triggers(rows)
        self._audit("trigger_registered", {"trigger_id": trigger["id"], "run_at": trigger["run_at"], "source": trigger["source"]})
        self.write_health()
        return trigger

    def list_triggers(self, include_finished: bool = False) -> list[dict[str, Any]]:
        rows = self._load_triggers()
        if include_finished:
            return rows
        return [row for row in rows if str(row.get("state") or "").upper() == "SCHEDULED"]

    def cancel_trigger(self, trigger_id: str) -> bool:
        trigger_id = str(trigger_id or "").strip()
        rows = self._load_triggers()
        changed = False
        for row in rows:
            if str(row.get("id") or "") != trigger_id:
                continue
            if str(row.get("state") or "").upper() != "SCHEDULED":
                return False
            row["state"] = "CANCELLED"
            row["cancelled_at"] = _now()
            changed = True
            break
        if changed:
            self._save_triggers(rows)
            self._audit("trigger_cancelled", {"trigger_id": trigger_id})
            self.write_health()
        return changed

    def scan_time_triggers(self, limit: int = 20) -> int:
        rows = self._load_triggers()
        now = datetime.now(timezone.utc)
        fired = 0
        changed = False
        for row in rows:
            if fired >= limit:
                break
            if str(row.get("state") or "").upper() != "SCHEDULED":
                continue
            try:
                due = _parse_due_at(str(row.get("run_at") or ""))
            except Exception:
                row["state"] = "INVALID"
                row["error"] = "invalid run_at"
                changed = True
                continue
            if due > now:
                continue
            trigger_id = str(row.get("id") or "")
            payload = {
                "goal": str(row.get("goal") or ""),
                "actionable": True,
                "autonomy": str(row.get("autonomy") or "auto"),
                "trigger_id": trigger_id,
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            }
            event = self.emit_event(
                "scheduled_followup_due",
                payload,
                source=str(row.get("source") or "scheduler"),
                priority=int(row.get("priority") or 60),
                dedupe_key=f"time-trigger:{trigger_id}",
            )
            row["state"] = "FIRED"
            row["fired_at"] = _now()
            row["event_id"] = event.get("id")
            changed = True
            fired += 1
            self._audit("trigger_fired", {"trigger_id": trigger_id, "event_id": event.get("id")})
        if changed:
            self._save_triggers(rows)
        return fired

    def verify_execution(self, answer: str, history: list[dict[str, Any]]) -> VerificationResult:
        answer = str(answer or "").strip()
        tool_results = [
            str(m.get("content") or "").strip()
            for m in history
            if isinstance(m, dict) and m.get("role") == "tool"
        ]
        successful = [
            row for row in tool_results
            if row and not any(marker in row.lower() for marker in _FAILURE_MARKERS)
        ]
        promise_only = bool(_PROMISE_RE.search(answer))
        substance = len(answer) > 40 and not promise_only
        grounding = _grounding_audit(answer, "\n".join(successful)) if successful else {
            "checked": False, "anchors": 0, "supported": 0, "unsupported": 0,
            "score": 0.0, "unsupported_sample": [],
        }
        grounded = (not grounding["checked"]) or grounding["score"] >= 0.34
        ok = bool(successful) and substance and grounded
        if not successful:
            reason = "no_successful_tool_execution"
        elif not substance:
            reason = "non_substantive_completion"
        elif not grounded:
            reason = "ungrounded_completion"
        else:
            reason = "verified_tool_execution"
        fallback = successful[-1][:1600] if successful else ""
        return VerificationResult(
            ok=ok,
            reason=reason,
            evidence={
                "tool_calls": len(tool_results),
                "successful_tool_calls": len(successful),
                "promise_only": promise_only,
                "answer_chars": len(answer),
                "grounding": grounding,
            },
            fallback_result=fallback,
        )

    def recover_stale_tasks(self, stale_after_seconds: int = 1800) -> int:
        recovered = 0
        now = time.time()
        for path in self.queue.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            state = str(task.get("state") or "").upper()
            if not state:
                state = "EXECUTING" if task.get("status") == "in_progress" else "QUEUED"
            if state != "EXECUTING":
                continue
            started = task.get("started_epoch") or task.get("created") or now
            try:
                age = now - float(started)
            except Exception:
                age = 0
            if age < stale_after_seconds:
                continue
            task["recoveries"] = int(task.get("recoveries", 0)) + 1
            self.transition(task, "QUEUED", last_recovery_reason="stale_execution")
            path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            recovered += 1
        return recovered

    def scan_health_events(self) -> int:
        """Convert newly degraded internal health snapshots into read-only diagnostic work."""
        health_dir = self.root / "brain" / "system" / "health"
        seen_path = self.working / "autonomy_seen_health.json"
        try:
            seen = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
        except Exception:
            seen = {}
        if not isinstance(seen, dict):
            seen = {}

        emitted = 0
        for health_file in sorted(health_dir.glob("*.json")):
            if health_file == self.health_path:
                continue
            try:
                data = json.loads(health_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or data.get("ok") is not False:
                continue
            component = _clean(data.get("component") or health_file.stem, 120)
            signal = json.dumps(
                {
                    "component": component,
                    "updated_at": data.get("updated_at"),
                    "error": data.get("error") or data.get("reason") or data.get("status"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            fingerprint = hashlib.sha256(signal.encode("utf-8")).hexdigest()[:16]
            if seen.get(health_file.name) == fingerprint:
                continue
            goal = (
                f"Pruefe den internen MIA-Systemzustand der Komponente '{component}'. "
                "Ermittle mit verfuegbaren lokalen Status- und Diagnosewerkzeugen die "
                "konkrete Ursache und dokumentiere ein nachpruefbares Ergebnis. "
                "Nimm dabei keine externen, destruktiven oder irreversiblen Aenderungen vor."
            )
            self.emit_event(
                "health_degraded",
                {"goal": goal, "actionable": True, "autonomy": "auto", "health_file": health_file.name},
                source="health_monitor",
                priority=80,
            )
            seen[health_file.name] = fingerprint
            emitted += 1

        seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
        return emitted

    def _task_for_event(self, event_id: str) -> dict[str, Any] | None:
        event_id = str(event_id or "").strip()
        if not event_id:
            return None
        for folder in (self.queue, self.done):
            for task_path in folder.glob("*.json"):
                try:
                    task = json.loads(task_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                if str(metadata.get("event_id") or "") == event_id:
                    return task
        return None

    def process_events(self, limit: int = 20) -> dict[str, int]:
        created = 0
        approvals = 0
        processed = 0
        files = sorted(self.inbox.glob("*.json"))[:limit]
        for path in files:
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                goal = _clean(payload.get("goal"), 4000)
                if goal and payload.get("actionable", True):
                    event_id = str(event.get("id") or "")
                    task = self._task_for_event(event_id)
                    created_now = task is None
                    if task is None:
                        task = self.create_task(
                            goal,
                            source=f"proactive:{event.get('source') or 'system'}",
                            autonomy=str(payload.get("autonomy") or "auto"),
                            reason=f"event:{event.get('type')}",
                            metadata={
                                "event_id": event_id,
                                "event_type": event.get("type"),
                                "trigger_id": payload.get("trigger_id"),
                                **(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
                            },
                        )
                    event["task_id"] = task["id"]
                    if created_now:
                        if task["state"] == "WAITING_FOR_APPROVAL":
                            approvals += 1
                        else:
                            created += 1
                    else:
                        self._audit("event_task_reused", {"event_id": event_id, "task_id": task.get("id")})
                event["state"] = "PROCESSED"
                event["processed_at"] = _now()
                target = self.processed / path.name
                target.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
                path.unlink(missing_ok=True)
                processed += 1
            except Exception as exc:
                self._audit("event_processing_error", {"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
        return {"processed": processed, "tasks_created": created, "waiting_approval": approvals}

    def pulse(self) -> dict[str, Any]:
        recovered = self.recover_stale_tasks()
        time_events = self.scan_time_triggers()
        health_events = self.scan_health_events()
        events = self.process_events()
        snapshot = self.write_health(
            extra={
                "recovered_stale_tasks": recovered,
                "time_triggers_fired": time_events,
                "health_events_emitted": health_events,
                **events,
            }
        )
        self._audit("brain_pulse", {k: snapshot[k] for k in ("queue", "waiting_approval", "done")})
        return snapshot

    def write_health(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        queued = 0
        waiting = 0
        executing = 0
        for path in self.queue.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            state = str(task.get("state") or "").upper()
            if state == "WAITING_FOR_APPROVAL" or task.get("status") == "waiting_approval":
                waiting += 1
            elif state == "EXECUTING" or task.get("status") == "in_progress":
                executing += 1
            else:
                queued += 1
        health = {
            "ok": True,
            "component": "autonomous_core",
            "updated_at": _now(),
            "queue": queued,
            "executing": executing,
            "waiting_approval": waiting,
            "done": len(list(self.done.glob("*.json"))),
            "event_inbox": len(list(self.inbox.glob("*.json"))),
            "scheduled_triggers": len(self.list_triggers()),
        }
        if extra:
            health.update(extra)
        self.health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        return health

_DEFAULT = AutonomousBrain()

def create_task(goal: str, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT.create_task(goal, **kwargs)

def emit_event(event_type: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT.emit_event(event_type, payload, **kwargs)

def register_time_trigger(goal: str, run_at: str, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT.register_time_trigger(goal, run_at, **kwargs)

def list_triggers(include_finished: bool = False) -> list[dict[str, Any]]:
    return _DEFAULT.list_triggers(include_finished=include_finished)

def cancel_trigger(trigger_id: str) -> bool:
    return _DEFAULT.cancel_trigger(trigger_id)

def pulse() -> dict[str, Any]:
    return _DEFAULT.pulse()

def autonomy_health() -> dict[str, Any]:
    return _DEFAULT.write_health()
