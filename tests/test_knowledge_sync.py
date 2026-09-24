"""Abgleich mit MIA-KNOWLEDGE-01 - offline: KNOWLEDGE-01 wird durch eine Attrappe ersetzt.

Geprüft wird, was hier zählt: dass nur Neues und Geändertes gesendet wird, dass Gelöschtes ankommt, dass eine
Massenlöschung angehalten wird, dass ein Ausfall MIA nicht stört und beim nächsten Lauf aufgeholt wird, und dass
Gespräche vollständig, aber ohne Werkzeugmüll archiviert werden.
"""
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from command_center.backend.db import Database, new_id, now_iso  # noqa: E402
from command_center.backend.services import knowledge_sync as ks  # noqa: E402

FAILS = []
TOKEN = "test-token"
os.environ["MIA_KNOWLEDGE_TOKEN"] = TOKEN
os.environ["MIA_KNOWLEDGE_MEMORY_URL"] = "http://mem.test"
os.environ["MIA_KNOWLEDGE_ARCHIVE_URL"] = "http://arch.test"


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


class Log:
    def __init__(self):
        self.lines = []

    def info(self, source, message, **kw):
        self.lines.append(("info", message))

    def warning(self, source, message, **kw):
        self.lines.append(("warning", message))

    def levels(self, level):
        return [m for lv, m in self.lines if lv == level]


class Remote:
    """Attrappe von KNOWLEDGE-01: memory_service + session_archive, mit Anmeldung."""

    def __init__(self):
        self.memories, self.sessions, self.calls, self.gets = {}, {}, [], []
        self.reject_ids, self.conflict_sessions, self.fail_memory = set(), set(), False
        self.down = False
        self.status = None        # feste Fehlerantwort
        self.fail_after_bulk = None
        self.delay = 0.0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        import json
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.down:
            raise httpx.ConnectError("Verbindung abgelehnt", request=request)
        if request.headers.get("Authorization") != "Bearer " + TOKEN:
            return httpx.Response(401, json={"error": "unauthorized"})
        if self.status:
            return httpx.Response(self.status, json={"error": "kaputt"})
        path, body = request.url.path, (json.loads(request.content) if request.content else {})
        if request.method == "GET":
            self.gets.append(path)
            if path == "/memory/count":
                return httpx.Response(200, json={"live": sum(1 for m in self.memories.values() if not m["deleted"])})
            if path == "/sessions":
                return httpx.Response(200, json={"total": len(self.sessions), "sessions": []})
            return httpx.Response(404)
        self.calls.append((request.method, path))
        if self.fail_memory and path.startswith("/memory/"):
            return httpx.Response(500, json={"error": "Speicher kaputt"})
        if path == "/memory/bulk":
            if self.reject_ids & {it["id"] for it in body["items"]}:
                return httpx.Response(400, json={"error": "ungültiger Eintrag"})
            if self.fail_after_bulk is not None and sum(1 for c in self.calls if c[1] == "/memory/bulk") > self.fail_after_bulk:
                return httpx.Response(500, json={"error": "zweiter Stapel"})
            for it in body["items"]:
                self.memories[it["id"]] = {**it, "deleted": 0}
            return httpx.Response(200, json={"stored": len(body["items"])})
        if path == "/memory/delete":
            for i in body["ids"]:
                if i in self.memories:
                    self.memories[i]["deleted"] = 1
            return httpx.Response(200, json={"deleted": len(body["ids"])})
        if path.startswith("/sessions/") and request.method == "PUT":
            if path.split("/")[-1] in self.conflict_sessions:
                return httpx.Response(409, json={"error": "Archiv hat mehr Nachrichten"})
            self.sessions[path.split("/")[-1]] = body
            return httpx.Response(200, json={"id": path.split("/")[-1]})
        return httpx.Response(404)


def make(remote):
    db = Database(":memory:")
    log = Log()
    sync = ks.KnowledgeSync(db, log, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(remote.handler), headers={"Authorization": "Bearer " + TOKEN}))
    return db, log, sync


def add_memory(db, text, mid=None, pinned=0):
    mid = mid or new_id("mem")
    db.insert("memory", {"id": mid, "text": text, "actor": "user", "created_at": now_iso(), "pinned": pinned})
    return mid


_clock = [0]


def add_conversation(db, msgs, title="Test", cid=None):
    cid = cid or new_id("conv")
    _clock[0] += 1
    stamp = f"2026-09-24T10:{_clock[0]:02d}:00Z"
    db.insert("conversations", {"id": cid, "user_id": "u1", "title": title, "created_at": stamp, "updated_at": stamp, "actor": "user"})
    for i, (role, content) in enumerate(msgs):
        add_message(db, cid, role, content, f"{stamp[:-1]}.{i:03d}Z")
    return cid


def add_message(db, cid, role, content, at=None):
    db.insert("messages", {"id": new_id("msg"), "conversation_id": cid, "role": role, "content": content,
                           "created_at": at or now_iso()})


def run(coro):
    return asyncio.run(coro)


def main():
    print("Erinnerungen")
    remote = Remote()
    db, log, sync = make(remote)
    a, b = add_memory(db, "Christoph hat keinen Führerschein"), add_memory(db, "Preise nie ohne Aufschlag", pinned=1)
    st = run(sync.run_once())
    check("erster Lauf sendet alles", st["ok"] and st["memory_sent"] == 2 and len(remote.memories) == 2, st)
    check("Felder kommen an (pinned, actor)", remote.memories[b]["pinned"] is True and remote.memories[a]["actor"] == "user")
    remote.calls.clear()
    st = run(sync.run_once())
    check("zweiter Lauf sendet nichts", st["memory_sent"] == 0 and not remote.calls, (st, remote.calls))

    db.update("memory", a, {"text": "Christoph hat keinen Führerschein und kein Auto"})
    remote.calls.clear()
    st = run(sync.run_once())
    check("Änderung wird nachgesendet, nur diese", st["memory_sent"] == 1 and "kein Auto" in remote.memories[a]["text"], st)

    db.execute("DELETE FROM memory WHERE id = ?", (b,))
    st = run(sync.run_once())
    check("Löschung kommt an (als Markierung)", st["memory_deleted"] == 1 and remote.memories[b]["deleted"] == 1, st)
    remote.calls.clear()
    run(sync.run_once())
    check("Löschung wird nicht wiederholt", not [c for c in remote.calls if c[1] == "/memory/delete"], remote.calls)

    print("Schutz vor Massenlöschung")
    remote = Remote()
    db, log, sync = make(remote)
    ids = [add_memory(db, f"Fakt {i}") for i in range(25)]
    run(sync.run_once())
    for i in ids[:3]:
        db.execute("DELETE FROM memory WHERE id = ?", (i,))
    st = run(sync.run_once())
    check("wenige Löschungen laufen durch", st["memory_deleted"] == 3 and st["held_back_deletes"] == 0, st)
    for i in ids[3:23]:
        db.execute("DELETE FROM memory WHERE id = ?", (i,))
    remote.calls.clear()
    st = run(sync.run_once())
    check("Massenlöschung wird angehalten", st["held_back_deletes"] == 20 and st["memory_deleted"] == 0, st)
    check("... es geht keine Löschung raus", not [c for c in remote.calls if c[1] == "/memory/delete"], remote.calls)
    check("... und es wird gewarnt", any("Massenlöschung" in m for m in log.levels("warning")), log.lines)
    check("... die Sicherung bleibt vollständig", sum(1 for m in remote.memories.values() if m["deleted"] == 0) == 22)

    print("Leere Datenbank")
    remote = Remote()
    db, log, sync = make(remote)
    for i in range(30):
        add_memory(db, f"Fakt {i}")
    run(sync.run_once())
    db.execute("DELETE FROM memory")
    st = run(sync.run_once())
    check("komplett leere Tabelle räumt die Sicherung nicht leer",
          st["held_back_deletes"] == 30 and all(m["deleted"] == 0 for m in remote.memories.values()), st)

    print("Ausfall")
    remote = Remote()
    db, log, sync = make(remote)
    add_memory(db, "Wichtig")
    remote.down = True
    st = run(sync.run_once())
    check("Ausfall wirft nicht und steht im Status", st["ok"] is False and "ConnectError" in st["error"], st)
    st = run(sync.run_once())
    check("Ausfall wird nur einmal gewarnt", len(log.levels("warning")) == 1, log.lines)
    check("nichts gilt als gesendet", db.scalar("SELECT COUNT(*) FROM knowledge_sync") == 0)
    remote.down = False
    st = run(sync.run_once())
    check("danach wird aufgeholt", st["ok"] and st["memory_sent"] == 1 and len(remote.memories) == 1, st)
    check("Wiederkehr wird gemeldet", any("läuft wieder" in m for m in log.levels("info")), log.lines)

    print("Fehlerantworten")
    for code in (401, 500):
        remote = Remote()
        db, log, sync = make(remote)
        add_memory(db, "Wichtig")
        remote.status = code
        st = run(sync.run_once())
        check(f"HTTP {code}: Fehler im Status, nichts als gesendet markiert",
              st["ok"] is False and str(code) in st["error"] and db.scalar("SELECT COUNT(*) FROM knowledge_sync") == 0, st)
    remote = Remote()
    db, log, sync = make(remote)
    add_memory(db, "Wichtig")
    sync._client_factory = ks._default_client
    st = run(sync.run_once())
    check("Verbindung zur Adresse ohne Server ist ein Fehler, keine Ausnahme", st["ok"] is False, st)
    check("Standard-Client sendet das Token", ks._default_client().headers["Authorization"] == "Bearer " + TOKEN)

    print("Teilweiser Ausfall mitten im Senden")
    old = ks.BATCH
    ks.BATCH = 2
    remote = Remote()
    db, log, sync = make(remote)
    for i in range(5):
        add_memory(db, f"Fakt {i}")
    remote.fail_after_bulk = 1
    st = run(sync.run_once())
    check("erster Stapel ist gesichert, Lauf meldet Fehler und die bereits gesendete Zahl",
          st["ok"] is False and st["memory_sent"] == 2 and db.scalar("SELECT COUNT(*) FROM knowledge_sync") == 2, st)
    remote.fail_after_bulk = None
    remote.calls.clear()
    st = run(sync.run_once())
    check("Wiederholung sendet nur den Rest", st["ok"] and st["memory_sent"] == 3 and len(remote.memories) == 5, st)
    ks.BATCH = old

    print("Gespräche")
    remote = Remote()
    db, log, sync = make(remote)
    c1 = add_conversation(db, [("user", "Wer holt Christoph ab?"), ("tool", "{riesiges json}"), ("assistant", "Ich rufe ein Taxi.")], "Fahrdienst")
    c2 = add_conversation(db, [("tool", "nur Werkzeug")], "Nur Werkzeug")
    st = run(sync.run_once())
    check("Gespräch wird archiviert, eines ohne Nachrichten nicht", st["conversations_sent"] == 1 and set(remote.sessions) == {c1}, (st, list(remote.sessions)))
    sent = remote.sessions[c1]
    check("nur Nutzer- und Assistenten-Nachrichten, in Reihenfolge",
          [m["role"] for m in sent["messages"]] == ["user", "assistant"] and sent["title"] == "Fahrdienst", sent)
    remote.calls.clear()
    check("unverändert wird nichts erneut gesendet", run(sync.run_once())["conversations_sent"] == 0 and not remote.calls)
    add_message(db, c1, "user", "Danke")
    db.update("conversations", c1, {"updated_at": "2099-01-01T00:00:00Z"})
    st = run(sync.run_once())
    check("neue Nachricht: Gespräch wird erneut gesendet", st["conversations_sent"] == 1 and len(remote.sessions[c1]["messages"]) == 3, st)

    print("Begrenzung pro Lauf")
    old = ks.CONVERSATIONS_PER_RUN
    ks.CONVERSATIONS_PER_RUN = 2
    remote = Remote()
    db, log, sync = make(remote)
    for i in range(3):
        add_conversation(db, [("user", f"Frage {i}")])
    first = run(sync.run_once())["conversations_sent"]
    second = run(sync.run_once())["conversations_sent"]
    check("zwei im ersten Lauf, der Rest im zweiten", (first, second) == (2, 1) and len(remote.sessions) == 3, (first, second))
    ks.CONVERSATIONS_PER_RUN = old

    print("Gleichzeitige Läufe")
    remote = Remote()
    remote.delay = 0.05
    db, log, sync = make(remote)
    add_memory(db, "Nur einmal senden")

    async def twice():
        return await asyncio.gather(sync.run_once(), sync.run_once())

    run(twice())
    check("zwei gleichzeitige Läufe senden nicht doppelt", sum(1 for c in remote.calls if c[1] == "/memory/bulk") == 1, remote.calls)

    print("Streaming-Nachrichten (Befund H1)")
    remote = Remote()
    db, log, sync = make(remote)
    cid = add_conversation(db, [("user", "Frage")])
    mid = new_id("msg")
    db.insert("messages", {"id": mid, "conversation_id": cid, "role": "assistant", "content": "", "status": "streaming",
                           "created_at": now_iso()})
    st = run(sync.run_once())
    check("Gespräch mit laufender Antwort wird übersprungen", st["conversations_sent"] == 0 and cid not in remote.sessions, st)
    db.update("messages", mid, {"content": "Fertige Antwort", "status": "complete"})   # updated_at bleibt unverändert
    st = run(sync.run_once())
    got = remote.sessions.get(cid, {}).get("messages", [])
    check("fertige Antwort wird nachgeholt, obwohl Zeitstempel und Titel gleich blieben",
          st["conversations_sent"] == 1 and [m["content"] for m in got] == ["Frage", "Fertige Antwort"], (st, got))
    db.update("messages", mid, {"content": "Fertige Antwort, jetzt bearbeitet und länger"})
    st = run(sync.run_once())
    check("bearbeitete Nachricht (gleiche Anzahl) wird erneut gesendet",
          st["conversations_sent"] == 1 and "länger" in remote.sessions[cid]["messages"][-1]["content"], st)

    print("Abgebrochene Antwort blockiert das Archiv nicht")
    remote = Remote()
    db, log, sync = make(remote)
    cid = add_conversation(db, [("user", "Frage")])
    db.insert("messages", {"id": new_id("msg"), "conversation_id": cid, "role": "assistant", "content": "halbe Antwort",
                           "status": "streaming", "created_at": "2026-09-21T19:31:13.624Z"})
    st = run(sync.run_once())
    check("seit Tagen auf 'streaming' hängende Nachricht: Gespräch wird trotzdem archiviert",
          st["conversations_sent"] == 1 and sorted(m["content"] for m in remote.sessions[cid]["messages"]) == ["Frage", "halbe Antwort"], st)
    check("Schwelle ist das Alter: gerade begonnene Antwort bleibt draußen",
          ks._streaming_cutoff() < now_iso() and ks._streaming_cutoff() > "2026-09-21T19:31:13.624Z")

    print("Reihenfolge bei gleichem Zeitstempel (Befund M4)")
    remote = Remote()
    db, log, sync = make(remote)
    cid = add_conversation(db, [])
    for text in ("eins", "zwei", "drei"):
        add_message(db, cid, "user", text, "2026-09-24T12:00:00Z")
    run(sync.run_once())
    check("Nachrichten mit identischem Zeitstempel bleiben in Einfüge-Reihenfolge",
          [m["content"] for m in remote.sessions[cid]["messages"]] == ["eins", "zwei", "drei"], remote.sessions.get(cid))

    print("Kleine Bestände und Bestätigung (Befund M1)")
    remote = Remote()
    db, log, sync = make(remote)
    ids = [add_memory(db, f"Fakt {i}") for i in range(3)]
    run(sync.run_once())
    db.execute("DELETE FROM memory")
    st = run(sync.run_once())
    check("kleiner Bestand komplett geleert: wird angehalten", st["held_back_deletes"] == 3 and st["memory_deleted"] == 0, st)
    run(sync.run_once())
    check("... und nur einmal gewarnt", len([m for m in log.levels("warning") if "Massenlöschung" in m]) == 1, log.lines)
    sync.confirm_deletes()
    st = run(sync.run_once())
    check("nach Bestätigung kommt die Löschung an", st["memory_deleted"] == 3 and st["held_back_deletes"] == 0
          and all(m["deleted"] == 1 for m in remote.memories.values()), st)
    ids = [add_memory(db, f"Neu {i}") for i in range(10)]
    run(sync.run_once())
    for i in ids[:2]:
        db.execute("DELETE FROM memory WHERE id = ?", (i,))
    st = run(sync.run_once())
    check("Bestätigung gilt nur einmal, zwei von zehn laufen normal durch", st["memory_deleted"] == 2 and st["held_back_deletes"] == 0, st)
    for i in ids[2:7]:
        db.execute("DELETE FROM memory WHERE id = ?", (i,))
    st = run(sync.run_once())
    check("fünf von acht (>= 5 und >= 50 %) werden angehalten", st["held_back_deletes"] == 5 and st["memory_deleted"] == 0, st)

    print("Abgelehnter Einzeleintrag (Befund H3)")
    remote = Remote()
    db, log, sync = make(remote)
    good1, bad, good2 = add_memory(db, "gut 1"), add_memory(db, "schlecht", mid="bad-1"), add_memory(db, "gut 2")
    remote.reject_ids = {"bad-1"}
    st = run(sync.run_once())
    check("ein schlechter Eintrag blockiert den Stapel nicht", st["ok"] and st["memory_sent"] == 2 and st["memory_skipped"] == 1
          and {good1, good2} <= set(remote.memories) and "bad-1" not in remote.memories, st)
    check("... und wird gemeldet", any("bad-1" in m for m in log.levels("warning")), log.lines)
    remote.calls.clear()
    run(sync.run_once())
    check("... und nicht bei jedem Lauf erneut versucht", not [c for c in remote.calls if c[1] == "/memory/bulk"], remote.calls)

    print("Erinnerungen und Archiv laufen unabhängig (Befund H3)")
    remote = Remote()
    db, log, sync = make(remote)
    add_memory(db, "Fakt")
    cid = add_conversation(db, [("user", "Hallo")])
    remote.fail_memory = True
    st = run(sync.run_once())
    check("Fehler bei den Erinnerungen hält das Archiv nicht auf", st["ok"] is False and "Erinnerungen" in st["error"]
          and cid in remote.sessions and st["conversations_sent"] == 1, st)

    print("Archiv behält den größeren Stand (Befund H4)")
    remote = Remote()
    db, log, sync = make(remote)
    cid = add_conversation(db, [("user", "Hallo")])
    remote.conflict_sessions = {cid}
    st = run(sync.run_once())
    check("409 ist kein Fehler: gezählt, nichts überschrieben", st["ok"] and st["archive_kept"] == 1 and cid not in remote.sessions, st)
    remote.calls.clear()
    run(sync.run_once())
    check("... und nicht bei jedem Lauf erneut versucht", not [c for c in remote.calls if c[0] == "PUT"], remote.calls)

    print("KNOWLEDGE-01 zurückgesetzt (Befund H2)")
    remote = Remote()
    db, log, sync = make(remote)
    for i in range(3):
        add_memory(db, f"Fakt {i}")
    add_conversation(db, [("user", "A")])
    add_conversation(db, [("user", "B")])
    run(sync.run_once())
    remote.memories.clear()
    remote.sessions.clear()
    st = run(sync.run_once())
    check("leerer Server wird komplett neu befüllt", st["ok"] and len(remote.memories) == 3 and len(remote.sessions) == 2, (st, len(remote.memories)))
    remote.calls.clear()
    run(sync.run_once())
    check("danach ist wieder Ruhe", not remote.calls, remote.calls)

    print("Intervall aus der Umgebung (Befund M6)")
    from command_center.backend.modules import knowledge_sync as ks_module
    for raw, want in (("abc", 300), ("10", 60), ("600", 600)):
        os.environ["MIA_KNOWLEDGE_SYNC_INTERVAL"] = raw
        check(f"MIA_KNOWLEDGE_SYNC_INTERVAL={raw!r} -> {want}", ks_module._interval() == want, ks_module._interval())
    del os.environ["MIA_KNOWLEDGE_SYNC_INTERVAL"]

    print("Einschalten")
    saved = os.environ.pop("MIA_KNOWLEDGE_TOKEN")
    check("ohne Token aus", ks.enabled() is False)
    os.environ["MIA_KNOWLEDGE_TOKEN"] = saved
    check("mit Token an", ks.enabled() is True)
    os.environ["MIA_KNOWLEDGE_SYNC"] = "0"
    check("MIA_KNOWLEDGE_SYNC=0 schaltet ab", ks.enabled() is False)
    del os.environ["MIA_KNOWLEDGE_SYNC"]

    print("Übersicht")
    remote = Remote()
    db, log, sync = make(remote)
    check("Übersicht vor dem ersten Lauf wirft nicht", sync.overview()["backed_up_memories"] == 0)
    add_memory(db, "x")
    add_conversation(db, [("user", "y")])
    run(sync.run_once())
    ov = sync.overview()
    check("Übersicht zählt Gesichertes", ov["backed_up_memories"] == 1 and ov["archived_conversations"] == 1, ov)

    print()
    if FAILS:
        print(f"{len(FAILS)} FEHLGESCHLAGEN: {FAILS}")
        sys.exit(1)
    print("alle Prüfungen bestanden")


if __name__ == "__main__":
    main()
