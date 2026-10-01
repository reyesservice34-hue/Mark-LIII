"""MIA experience memory for solved paths, failed attempts, preferences and teacher lessons."""
from __future__ import annotations

import re
from typing import Iterable

from ..db import Database, dumps, loads, new_id, now_iso

KINDS = {"solution", "error", "strategy", "preference", "self", "teacher", "gap"}


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß0-9_-]{4,}", text or "")}


class LearningLedger:
    def __init__(self, db: Database, bus=None, log=None) -> None:
        self.db = db
        self.bus = bus
        self.log = log

    @staticmethod
    def _public(row) -> dict:
        r = dict(row)
        r["failed_attempts"] = loads(r.get("failed_attempts"), [])
        r["tags"] = loads(r.get("tags"), [])
        r["confidence"] = float(r.get("confidence") or 0)
        return r

    def record(self, *, kind: str, title: str, problem: str = "", lesson: str = "",
               failed_attempts: Iterable[str] | None = None, verification: str = "",
               source: str = "", conversation_id: str | None = None,
               confidence: float = 1.0, tags: Iterable[str] | None = None,
               actor: str = "") -> dict:
        kind = (kind or "").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"Unknown learning kind: {kind}")
        title = " ".join((title or "").strip().split())[:180]
        if not title:
            raise ValueError("Learning record needs a title")
        now = now_iso()
        row = {
            "id": new_id("learn"), "kind": kind, "title": title,
            "problem": (problem or "").strip()[:8000],
            "lesson": (lesson or "").strip()[:16000],
            "failed_attempts": dumps([str(x)[:1200] for x in (failed_attempts or [])][:20]),
            "verification": (verification or "").strip()[:4000],
            "source": (source or actor or "mia").strip()[:120],
            "conversation_id": conversation_id,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "tags": dumps([str(x).strip()[:80] for x in (tags or []) if str(x).strip()][:20]),
            "uses": 0, "created_at": now, "updated_at": now,
        }
        self.db.insert("learning_records", row)
        if self.bus:
            self.bus.publish("learning.recorded", {"id": row["id"], "kind": kind, "title": title})
        if self.log:
            self.log.info("learning", f"{kind}: {title}")
        return self._public(row)

    def search(self, query: str, *, kinds: Iterable[str] | None = None, limit: int = 6) -> list[dict]:
        wanted = {k.strip().lower() for k in (kinds or []) if k.strip()}
        rows = self.db.fetchall("SELECT * FROM learning_records ORDER BY updated_at DESC LIMIT 600")
        qwords = _words(query)
        qlow = (query or "").strip().lower()
        scored: list[tuple[float, dict]] = []
        for raw in rows:
            r = self._public(raw)
            if wanted and r["kind"] not in wanted:
                continue
            hay = " ".join([
                r.get("title", ""), r.get("problem", ""), r.get("lesson", ""),
                " ".join(r.get("failed_attempts") or []), " ".join(r.get("tags") or []),
            ]).lower()
            hits = sum(1 for w in qwords if w in hay)
            phrase = 3 if qlow and qlow in hay else 0
            score = 1.0 if not qwords else hits + phrase
            if score <= 0:
                continue
            score += min(1.0, float(r.get("confidence") or 0))
            scored.append((score, r))
        scored.sort(key=lambda item: (-item[0], item[1]["updated_at"]))
        out = [r for _, r in scored[:max(1, min(int(limit), 20))]]
        for r in out:
            self.db.execute("UPDATE learning_records SET uses=uses+1 WHERE id=?", (r["id"],))
            r["uses"] = int(r.get("uses") or 0) + 1
        return out

    def context(self, query: str, limit: int = 5) -> str:
        rows = self.search(
            query,
            kinds=("solution", "error", "strategy", "preference", "self", "teacher"),
            limit=limit,
        )
        if not rows:
            return ""
        parts = [
            "ERFAHRUNGSWISSEN: Nutze bekannte Lösungswege zuerst. "
            "Wiederhole dokumentierte Fehlversuche nicht blind."
        ]
        for r in rows:
            block = f"[{r['kind'].upper()}] {r['title']}"
            if r.get("problem"):
                block += f"\nProblem: {r['problem'][:700]}"
            if r.get("lesson"):
                block += f"\nGelernt/Lösung: {r['lesson'][:1000]}"
            failed = "; ".join(r.get("failed_attempts") or [])
            if failed:
                block += f"\nNicht wiederholen: {failed[:700]}"
            if r.get("verification"):
                block += f"\nVerifiziert: {r['verification'][:500]}"
            parts.append(block)
        return "\n\n".join(parts)
