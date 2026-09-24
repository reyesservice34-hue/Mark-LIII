"""Abgleich mit MIA-KNOWLEDGE-01: Sicherung der Erinnerungen und Archiv der Gespräche.

Erinnerungen werden an sieben Stellen geschrieben. Statt jede einzeln anzufassen, vergleicht dieser Job
regelmäßig die Tabellen mit einem Zustandsprotokoll (knowledge_sync: Art, ID, Prüfsumme) und schickt nur,
was neu, geändert oder gelöscht ist. Fällt KNOWLEDGE-01 aus, merkt MIA nichts davon: Der Lauf bricht ab,
der nächste versucht es wieder, und im Status steht, was schiefging.

* Erinnerungen -> memory_service (POST /memory/bulk, /memory/delete). Gelöschtes bleibt dort als Markierung.
* Gespräche   -> session_archive (PUT /sessions/<id>). Das Archiv behält den größeren Stand: Fielen hier
  Nachrichten weg, lehnt es das Überschreiben ab (409), und der Abgleich lässt es dabei.

Beide Teile laufen unabhängig: Ein Fehler bei den Erinnerungen hält das Archiv nicht auf und umgekehrt.
Ein einzelner abgelehnter Eintrag (HTTP 400/409/413/422) wird übersprungen und gemeldet, er blockiert nichts.

Schutz vor Massenlöschung: Fehlt lokal plötzlich viel oder alles (kaputte, zurückgesetzte oder absichtlich
geleerte Datenbank), gibt der Job die Löschung nicht weiter, sondern meldet sie einmal. Wer das wirklich
will, bestätigt es (confirm_deletes) und der nächste Lauf gibt sie weiter.

Ein Gespräch mit laufender Antwort (status "streaming") wird in diesem Lauf übersprungen; sonst würde die
leere Antwort archiviert und der fertige Text nie nachgeholt. Die Prüfsumme deckt deshalb auch die
Textlänge und die jüngste Nachricht ab, nicht nur Titel, Zeitstempel und Anzahl.

Ist KNOWLEDGE-01 zurückgesetzt (weniger da, als hier als gesendet gilt), wird neu gesendet.

Einschalten: MIA_KNOWLEDGE_TOKEN setzen. Adressen: MIA_KNOWLEDGE_MEMORY_URL / MIA_KNOWLEDGE_ARCHIVE_URL
(Vorgabe: der SSH-Tunnel des Hosts). Abschalten: MIA_KNOWLEDGE_SYNC=0.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

BATCH = 100
CONVERSATIONS_PER_RUN = 25       # ein Lauf bleibt kurz; der Rest folgt beim nächsten
MAX_MESSAGES = 2000              # so viel nimmt das Archiv pro Gespräch an
STREAMING_GRACE_MINUTES = 15     # so lange gilt eine Antwort im Status "streaming" als noch laufend
MASS_DELETE_MIN = 5              # ab dieser Zahl gilt eine Löschung als "viel" ...
MASS_DELETE_SHARE = 0.5          # ... und ab diesem Anteil der gesicherten Erinnerungen als Massenlöschung
SKIPPABLE = {400, 409, 413, 422}  # der Server lehnt genau diesen Eintrag ab: überspringen, nicht abbrechen
DEFAULT_MEMORY_URL = "http://host.docker.internal:18001"
DEFAULT_ARCHIVE_URL = "http://host.docker.internal:18004"
ARCHIVED_ROLES = ("user", "assistant")

_SCHEMA = ("CREATE TABLE IF NOT EXISTS knowledge_sync ("
           "kind TEXT NOT NULL, id TEXT NOT NULL, hash TEXT NOT NULL, synced_at TEXT NOT NULL, "
           "PRIMARY KEY (kind, id))")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _streaming_cutoff() -> str:
    """Eine Antwort, die länger als das im Status "streaming" hängt, ist abgebrochen (Neustart, Absturz)
    und blockiert das Archiv nicht. Format wie db.now_iso(), damit der Textvergleich stimmt."""
    then = datetime.now(timezone.utc) - timedelta(minutes=STREAMING_GRACE_MINUTES)
    return then.strftime("%Y-%m-%dT%H:%M:%S.") + f"{then.microsecond // 1000:03d}Z"


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, default=str).encode()).hexdigest()[:32]


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def token() -> str:
    return os.environ.get("MIA_KNOWLEDGE_TOKEN", "").strip()


def enabled() -> bool:
    return os.environ.get("MIA_KNOWLEDGE_SYNC", "1") != "0" and bool(token())


def _urls() -> tuple[str, str]:
    return ((os.environ.get("MIA_KNOWLEDGE_MEMORY_URL") or DEFAULT_MEMORY_URL).rstrip("/"),
            (os.environ.get("MIA_KNOWLEDGE_ARCHIVE_URL") or DEFAULT_ARCHIVE_URL).rstrip("/"))


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0, headers={"Authorization": "Bearer " + token()})


async def _send_batch(send: Callable[[list], Awaitable[None]], batch: list) -> tuple[list, list]:
    """Sendet einen Stapel. Lehnt der Server ihn ab, geht es einzeln weiter. -> (angenommen, abgelehnt)."""
    try:
        await send(batch)
        return list(batch), []
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code not in SKIPPABLE:
            raise
        if len(batch) == 1:
            return [], [(batch[0], code)]
    accepted, rejected = [], []
    for item in batch:
        ok, bad = await _send_batch(send, [item])
        accepted += ok
        rejected += bad
    return accepted, rejected


class KnowledgeSync:
    def __init__(self, db, log=None, client_factory: Callable[[], httpx.AsyncClient] | None = None):
        self.db = db
        self.log = log
        self._client_factory = client_factory or _default_client
        self._lock = asyncio.Lock()
        self._failing = False
        self._held_seen = 0
        self._confirmed = False
        self.status: dict[str, Any] = {"last_run": None, "ok": None, "error": None, "memory_sent": 0,
                                       "memory_deleted": 0, "memory_skipped": 0, "held_back_deletes": 0,
                                       "conversations_sent": 0, "archive_kept": 0, "conversations_skipped": 0}

    def confirm_deletes(self) -> None:
        """Der nächste Lauf gibt auch eine Massenlöschung an KNOWLEDGE-01 weiter."""
        self._confirmed = True

    # ── Zustandsprotokoll ────────────────────────────────────────────────
    def _synced(self, kind: str) -> dict[str, str]:
        return {r["id"]: r["hash"] for r in self.db.fetchall("SELECT id, hash FROM knowledge_sync WHERE kind = ?", (kind,))}

    def _mark(self, kind: str, pairs: list[tuple[str, str]]) -> None:
        now = _now()
        self.db.executemany("INSERT OR REPLACE INTO knowledge_sync (kind, id, hash, synced_at) VALUES (?, ?, ?, ?)",
                            [(kind, i, h, now) for i, h in pairs])

    def _unmark(self, kind: str, ids: list[str]) -> None:
        self.db.executemany("DELETE FROM knowledge_sync WHERE kind = ? AND id = ?", [(kind, i) for i in ids])

    def _forget_all(self, kind: str) -> None:
        self.db.execute("DELETE FROM knowledge_sync WHERE kind = ?", (kind,))

    def _say(self, level: str, message: str) -> None:
        if self.log is not None:
            getattr(self.log, level)("knowledge_sync", message)

    @staticmethod
    async def _get_json(http: httpx.AsyncClient, url: str) -> dict:
        resp = await http.get(url)
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, dict) else {}

    # ── Erinnerungen ─────────────────────────────────────────────────────
    async def _sync_memory(self, http: httpx.AsyncClient, base: str, run: dict) -> None:
        rows = self.db.fetchall("SELECT id, text, actor, pinned, created_at FROM memory")
        synced = self._synced("memory")
        skipped = self._synced("memory_skip")     # vom Server abgelehnt: zählt nicht als gesichert
        live = (await self._get_json(http, base + "/memory/count")).get("live")
        if isinstance(live, int) and live < len(synced):
            self._say("info", f"KNOWLEDGE-01 hat nur {live} von {len(synced)} gesicherten Erinnerungen - sende neu")
            self._forget_all("memory")
            synced = {}
        current = {r["id"]: _digest(r["text"], r["actor"], r["pinned"], r["created_at"]) for r in rows}
        known = {**synced, **skipped}
        self._unmark("memory_skip", [i for i in skipped if i not in current])

        async def send(items: list) -> None:
            body = {"items": [{"id": r["id"], "text": r["text"], "actor": r["actor"] or "",
                               "pinned": bool(r["pinned"]), "created_at": r["created_at"] or ""} for r in items]}
            (await http.post(base + "/memory/bulk", json=body)).raise_for_status()

        for batch in _chunks([r for r in rows if known.get(r["id"]) != current[r["id"]]], BATCH):
            accepted, rejected = await _send_batch(send, batch)
            self._mark("memory", [(r["id"], current[r["id"]]) for r in accepted])
            self._unmark("memory_skip", [r["id"] for r in accepted])
            self._unmark("memory", [r["id"] for r, _ in rejected])
            self._mark("memory_skip", [(r["id"], current[r["id"]]) for r, _ in rejected])
            run["memory_sent"] += len(accepted)
            run["memory_skipped"] += len(rejected)
            for row, code in rejected:
                self._say("warning", f"Erinnerung {row['id']} wurde von KNOWLEDGE-01 abgelehnt (HTTP {code}) - übersprungen")

        gone = [i for i in synced if i not in current]
        big = bool(gone) and (not current or (len(gone) >= MASS_DELETE_MIN and len(gone) >= MASS_DELETE_SHARE * len(synced)))
        held = len(gone) if big and not self._confirmed else 0
        if held != self._held_seen and held:
            self._say("warning", f"{held} von {len(synced)} gesicherten Erinnerungen fehlen lokal - Löschung nicht "
                                 "weitergegeben (Schutz vor Massenlöschung). Bestätigen: POST /api/knowledge-sync/confirm-deletes")
        self._held_seen = held
        run["held_back_deletes"] = held
        if held:
            gone = []
        for batch in _chunks(gone, BATCH):
            (await http.post(base + "/memory/delete", json={"ids": batch})).raise_for_status()
            self._unmark("memory", batch)
            run["memory_deleted"] += len(batch)
        if self._confirmed:
            self._confirmed = False

    # ── Gespräche ────────────────────────────────────────────────────────
    def _changed_conversations(self, synced: dict[str, str]) -> list[tuple[dict, str]]:
        marks = ",".join("?" for _ in ARCHIVED_ROLES)
        convs = self.db.fetchall(
            "SELECT c.id, c.title, c.actor, c.created_at, c.updated_at, COUNT(m.id) AS n, "
            "COALESCE(SUM(LENGTH(m.content)), 0) AS size, COALESCE(MAX(m.rowid), 0) AS last, "
            "COALESCE(SUM(m.status = 'streaming' AND m.created_at > ?), 0) AS streaming "
            f"FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id AND m.role IN ({marks}) "
            "GROUP BY c.id ORDER BY c.updated_at DESC, c.id", (_streaming_cutoff(), *ARCHIVED_ROLES))
        todo = []
        for c in convs:
            if c["n"] == 0 or c["streaming"]:
                continue
            digest = _digest(c["title"], c["updated_at"], c["n"], c["size"], c["last"])
            if synced.get(c["id"]) != digest:
                todo.append((c, digest))
        return todo[:CONVERSATIONS_PER_RUN]

    async def _sync_conversations(self, http: httpx.AsyncClient, base: str, run: dict) -> None:
        synced = self._synced("conversation")
        skipped = self._synced("conversation_skip")   # abgelehnt oder vom größeren Archivstand verdrängt
        total = (await self._get_json(http, base + "/sessions?limit=1")).get("total")
        if isinstance(total, int) and total < len(synced):
            self._say("info", f"Das Archiv hat nur {total} von {len(synced)} Gesprächen - sende neu")
            self._forget_all("conversation")
            synced = {}
        marks = ",".join("?" for _ in ARCHIVED_ROLES)
        for c, digest in self._changed_conversations({**synced, **skipped}):
            msgs = self.db.fetchall(
                f"SELECT role, content, created_at FROM messages WHERE conversation_id = ? AND role IN ({marks}) "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (c["id"], *ARCHIVED_ROLES, MAX_MESSAGES))[::-1]
            body = {"title": c["title"] or "", "actor": c["actor"] or "", "created_at": c["created_at"] or "",
                    "updated_at": c["updated_at"] or "", "messages": msgs}
            try:
                (await http.put(f"{base}/sessions/{c['id']}", json=body)).raise_for_status()
                run["conversations_sent"] += 1
                self._mark("conversation", [(c["id"], digest)])
                self._unmark("conversation_skip", [c["id"]])
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code not in SKIPPABLE:
                    raise
                if code == 409:      # das Archiv hat mehr Nachrichten: es behält seinen Stand
                    run["archive_kept"] += 1
                else:
                    run["conversations_skipped"] += 1
                    self._say("warning", f"Gespräch {c['id']} wurde vom Archiv abgelehnt (HTTP {code}) - übersprungen")
                self._unmark("conversation", [c["id"]])
                self._mark("conversation_skip", [(c["id"], digest)])

    # ── Lauf ─────────────────────────────────────────────────────────────
    async def run_once(self) -> dict[str, Any]:
        """Ein Abgleich. Wirft nie; Fehler stehen im Status."""
        if self._lock.locked():
            return self.status
        async with self._lock:
            mem_url, arch_url = _urls()
            run = {"memory_sent": 0, "memory_deleted": 0, "memory_skipped": 0, "held_back_deletes": 0,
                   "conversations_sent": 0, "archive_kept": 0, "conversations_skipped": 0}
            errors: list[str] = []
            try:
                self.db.execute(_SCHEMA)
                async with self._client_factory() as http:
                    for name, part, base in (("Erinnerungen", self._sync_memory, mem_url),
                                             ("Gespräche", self._sync_conversations, arch_url)):
                        try:
                            await part(http, base, run)
                        except (httpx.HTTPError, OSError) as exc:
                            errors.append(f"{name}: {type(exc).__name__}: {exc}")
                        except Exception as exc:  # noqa: BLE001 - ein Fehler hier darf MIA nie stören
                            errors.append(f"{name}: unerwartet: {type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
            error = "; ".join(errors)[:400] or None
            if error and not self._failing:
                self._say("warning", f"Abgleich mit KNOWLEDGE-01 gestört: {error}")
            elif not error and self._failing:
                self._say("info", "Abgleich mit KNOWLEDGE-01 läuft wieder")
            self._failing = bool(error)
            self.status = {"last_run": _now(), "ok": error is None, "error": error, **run}
            return self.status

    def overview(self) -> dict[str, Any]:
        counts = {r["kind"]: r["n"] for r in self.db.fetchall(
            "SELECT kind, COUNT(*) AS n FROM knowledge_sync GROUP BY kind")} if self._table_exists() else {}
        return {**self.status, "backed_up_memories": counts.get("memory", 0),
                "archived_conversations": counts.get("conversation", 0)}

    def _table_exists(self) -> bool:
        return bool(self.db.scalar("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_sync'"))
