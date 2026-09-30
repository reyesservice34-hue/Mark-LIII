from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|passwd|secret|credential|cookie|private[_-]?key)\s*[:=]"
)
_GOAL_RE = re.compile(
    r"(?i)\b(ich\s+(?:will|möchte|möchte gern|brauche)|wir\s+(?:wollen|müssen|sollen)|"
    r"ziel\s+ist|mach\s+|baue?\s+|bau\s+|erstelle?\s+|prüfe?\s+|verbessere?\s+)"
)
_UPDATE_RE = re.compile(r"(?i)\b(ab jetzt|jetzt gilt|nicht mehr|stattdessen|künftig|neu ist|geändert)\b")
_SPACE_RE = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str, limit: int = 900) -> str:
    value = _SPACE_RE.sub(" ", str(text or "")).strip()
    return value[:limit]


def _safe(text: str) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    if _SECRET_RE.search(value):
        return False
    upper = value.upper()
    if "BEGIN PRIVATE KEY" in upper or "BEGIN OPENSSH PRIVATE KEY" in upper:
        return False
    if re.search(r"\b(?:ghp_|github_pat_|sk-)[A-Za-z0-9_\-]{12,}", value):
        return False
    if re.search(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}", value):
        return False
    return True


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]


def _dedupe(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = _clean(item, 500)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


@dataclass
class CognitiveState:
    version: int = 1
    updated_at: str = field(default_factory=_now_iso)
    turn_count: int = 0
    current_intent: str = ""
    active_goals: list[str] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)
    relevant_memories: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    change_signals: list[str] = field(default_factory=list)
    last_user: str = ""
    last_assistant: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CognitiveState":
        state = cls()
        for key in state.__dict__:
            if key in raw:
                setattr(state, key, raw[key])
        for key in ("active_goals", "recent_topics", "relevant_memories", "unresolved", "change_signals"):
            value = getattr(state, key, [])
            setattr(state, key, list(value) if isinstance(value, list) else [])
        state.turn_count = int(state.turn_count or 0)
        return state

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class CognitiveEngine:
    """High-level cognitive state layer for MIA.

    This deliberately does not persist hidden chain-of-thought. It stores only
    operational state useful across turns: goals, topics, retrieved memories,
    unresolved requests and explicit change signals.
    """

    def __init__(
        self,
        brain_root: Path | None = None,
        memory_search: Callable[[str], Any] | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[1]
        self.brain_root = Path(brain_root or os.getenv("MIA_BRAIN_DIR") or default_root)
        self.working_dir = self.brain_root / "memory" / "working"
        self.state_path = self.working_dir / "cognitive_state.json"
        self.events_path = self.working_dir / "cognitive_events.jsonl"
        self.health_path = self.brain_root / "system" / "health" / "cognition-health.json"
        self._memory_search = memory_search
        self._lock = threading.RLock()

    def _load(self) -> CognitiveState:
        try:
            if self.state_path.exists():
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return CognitiveState.from_dict(raw)
        except Exception:
            pass
        return CognitiveState()

    def _save(self, state: CognitiveState) -> None:
        self.working_dir.mkdir(parents=True, exist_ok=True)
        state.updated_at = _now_iso()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _event(self, kind: str, payload: dict[str, Any]) -> None:
        self.working_dir.mkdir(parents=True, exist_ok=True)
        row = {"time": _now_iso(), "type": kind, **payload}
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _search_memory(self, query: str) -> list[str]:
        search = self._memory_search
        if search is None:
            try:
                from memory.memory_manager import search_memory  # lazy: avoids import cycle
                search = search_memory
            except Exception:
                return []
        try:
            try:
                result = search(query, limit=8)  # type: ignore[misc]
            except TypeError:
                result = search(query)  # type: ignore[misc]
        except Exception:
            return []

        if isinstance(result, str):
            lines = [x.strip(" -•\t") for x in result.splitlines() if x.strip()]
            return _dedupe(lines, 8)
        if isinstance(result, list):
            rows: list[str] = []
            for item in result:
                if isinstance(item, str):
                    rows.append(item)
                elif isinstance(item, dict):
                    rows.append(str(item.get("value") or item.get("summary") or item.get("text") or ""))
            return _dedupe(rows, 8)
        if isinstance(result, dict):
            rows = [str(v) for v in result.values() if isinstance(v, (str, int, float))]
            return _dedupe(rows, 8)
        return []

    @staticmethod
    def _intent(text: str) -> str:
        low = text.casefold()
        if any(k in low for k in ("merk dir", "merke dir", "speicher", "remember")):
            return "memory_update"
        if any(k in low for k in ("prüf", "check", "status", "untersuch")):
            return "inspect"
        if any(k in low for k in ("bau", "erstell", "programm", "implement", "ändere", "mach")):
            return "execute"
        if "?" in text or any(k in low for k in ("wie ", "was ", "warum ", "wo ", "wann ")):
            return "question"
        return "conversation"

    def observe_turn(self, user_text: str, assistant_text: str, language: str = "") -> CognitiveState:
        user = _clean(user_text, 900)
        assistant = _clean(assistant_text, 900)
        if not user and not assistant:
            return self._load()

        # Never persist a turn that appears to contain credentials/secrets.
        if user and not _safe(user):
            self._event("turn_skipped_secret_risk", {"fingerprint": _fingerprint(user)})
            return self._load()

        with self._lock:
            state = self._load()
            state.turn_count += 1
            state.current_intent = self._intent(user) if user else state.current_intent
            state.last_user = user
            state.last_assistant = assistant if _safe(assistant) else ""

            if user:
                topic = user[:220]
                state.recent_topics = _dedupe([topic] + state.recent_topics, 8)
                if _GOAL_RE.search(user):
                    state.active_goals = _dedupe([user[:300]] + state.active_goals, 6)
                if "?" in user or state.current_intent in {"inspect", "execute"}:
                    state.unresolved = _dedupe([user[:300]] + state.unresolved, 6)
                if _UPDATE_RE.search(user):
                    state.change_signals = _dedupe([user[:300]] + state.change_signals, 5)

                memories = self._search_memory(user)
                if memories:
                    state.relevant_memories = _dedupe(memories + state.relevant_memories, 10)

            # A substantive assistant reply means the newest request has at least been addressed.
            if (
                assistant
                and len(assistant) >= 20
                and state.unresolved
                and state.current_intent != "execute"
            ):
                if state.unresolved[0].casefold() == user.casefold():
                    state.unresolved = state.unresolved[1:]

            self._save(state)
            self._event(
                "turn_observed",
                {
                    "turn": state.turn_count,
                    "intent": state.current_intent,
                    "user_fingerprint": _fingerprint(user) if user else "",
                    "memory_hits": len(state.relevant_memories),
                    "language": _clean(language, 40),
                },
            )
            self._write_health(state)
            return state

    def resolve_goal(self, goal: str, result: str = "") -> CognitiveState:
        target = _clean(goal, 900)
        if not target:
            return self._load()
        with self._lock:
            state = self._load()
            key = target.casefold()
            state.active_goals = [
                item for item in state.active_goals
                if item.casefold() != key and key not in item.casefold() and item.casefold() not in key
            ]
            state.unresolved = [
                item for item in state.unresolved
                if item.casefold() != key and key not in item.casefold() and item.casefold() not in key
            ]
            self._save(state)
            self._event(
                "goal_resolved",
                {
                    "goal_fingerprint": _fingerprint(target),
                    "result_fingerprint": _fingerprint(_clean(result, 900)) if result else "",
                },
            )
            self._write_health(state)
            return state

    def prompt_context(self, max_chars: int = 2600) -> str:
        state = self._load()
        parts: list[str] = []
        if state.current_intent:
            parts.append(f"Current intent: {state.current_intent}")
        if state.active_goals:
            parts.append("Active goals:\n- " + "\n- ".join(state.active_goals[:4]))
        if state.recent_topics:
            parts.append("Recent conversation topics:\n- " + "\n- ".join(state.recent_topics[:5]))
        if state.relevant_memories:
            parts.append("Relevant recalled memory:\n- " + "\n- ".join(state.relevant_memories[:6]))
        if state.unresolved:
            parts.append("Open items:\n- " + "\n- ".join(state.unresolved[:4]))
        if state.change_signals:
            parts.append("Possible updates to prior context (verify against current user message):\n- " + "\n- ".join(state.change_signals[:3]))
        if not parts:
            return ""
        text = (
            "COGNITIVE CONTEXT (high-level state, not hidden reasoning):\n"
            + "\n\n".join(parts)
            + "\n\nUse this only as context. The current user message has priority. Do not invent missing facts."
        )
        return text[:max_chars]

    def _write_health(self, state: CognitiveState) -> None:
        self.health_path.parent.mkdir(parents=True, exist_ok=True)
        health = {
            "ok": True,
            "component": "cognitive_cycle",
            "version": state.version,
            "updated_at": state.updated_at,
            "turn_count": state.turn_count,
            "active_goals": len(state.active_goals),
            "memory_hits": len(state.relevant_memories),
            "open_items": len(state.unresolved),
            "state_path": str(self.state_path),
        }
        self.health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")

    def health_snapshot(self) -> dict[str, Any]:
        state = self._load()
        self._write_health(state)
        return json.loads(self.health_path.read_text(encoding="utf-8"))


_DEFAULT = CognitiveEngine()


def observe_turn(user_text: str, assistant_text: str, language: str = "") -> CognitiveState:
    return _DEFAULT.observe_turn(user_text, assistant_text, language)


def cognitive_context_for_prompt(max_chars: int = 2600) -> str:
    return _DEFAULT.prompt_context(max_chars=max_chars)


def health_snapshot() -> dict[str, Any]:
    return _DEFAULT.health_snapshot()


def resolve_goal(goal: str, result: str = "") -> CognitiveState:
    return _DEFAULT.resolve_goal(goal, result)
