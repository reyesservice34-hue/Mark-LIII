"""Semantisches Gedächtnis — offline: kein Netz, kein Schlüssel, kein Konto.

Der Embedding-Dienst wird durch eine feste Begriffs-Abbildung ersetzt. Geprüft wird, was hier zählt: dass nach
Bedeutung gefunden wird, dass Neues und Geändertes nachgetragen wird, dass ein Ausfall nicht stört und dass
Abschalten wirkt.
"""
import asyncio
import os
import sqlite3
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from command_center.backend.ai import embeddings as emb  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


class DB:
    """Nur das, was das Modul braucht: dieselbe Schnittstelle wie command_center.backend.db.Database."""

    def __init__(self):
        self.c = sqlite3.connect(":memory:", check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.c.execute("CREATE TABLE memory (id TEXT PRIMARY KEY, text TEXT, created_at TEXT, pinned INTEGER DEFAULT 0)")

    def execute(self, sql, params=()):
        with self.lock:
            return self.c.execute(sql, tuple(params))

    def fetchall(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, tuple(params)).fetchall()]

    def add(self, i, text, pinned=0):
        self.execute("INSERT INTO memory (id, text, created_at, pinned) VALUES (?,?,?,?)", (i, text, "2026-09-20", pinned))


# Begriffswelten statt eines echten Modells: gleiche Bedeutung → gleiche Richtung, egal welche Wörter.
CONCEPTS = [("führerschein", "steuer"), ("material", "globus", "baumarkt", "einkauf"),
            ("name", "david", "heißt", "anrede"), ("unterbrech", "aussprechen", "ins wort", "reden"),
            ("fahrzeug", "vivaro", "bipper", "transporter"), ("server", "docker", "n8n")]
CALLS = []


async def fake_embed(texts, timeout=20.0):
    CALLS.append(list(texts))
    out = []
    for t in texts:
        low = t.lower()
        v = [float(sum(low.count(w) for w in group)) for group in CONCEPTS]
        # "kann nicht selbst" o. ä. gehört in dieselbe Welt wie "Führerschein": Synonyme ohne gemeinsames Wort
        if "nicht selbst" in low or "kein führerschein" in low:
            v[0] += 1.0
        if "ins wort fallen" in low:
            v[3] += 1.0
        # Was zu keiner Begriffswelt gehört, bekommt eine eigene Richtung — nicht "ein bisschen von allem".
        out.append(emb._unit(v + [0.0] if any(v) else [0.0] * len(CONCEPTS) + [1.0]))
    return out


async def main():
    os.environ["LOCAL_LLM_URL"], os.environ["LOCAL_LLM_API_KEY"] = "http://x", "k"
    os.environ.pop("JARVIS_SEMANTIC_MEMORY", None)
    emb.embed = fake_embed
    db = DB()
    db.add("a", "Christoph hat keinen Führerschein und wohnt in Reichelsheim.")
    db.add("b", "Material holt Paul bei Globus ab, Einkauf nur nach Rücksprache.")
    db.add("c", "Der Name lautet David, nicht Davis.")
    db.add("d", "Hauptregel: Jarvis lässt den Nutzer aussprechen.", pinned=1)

    check("aktiv, wenn Zugang gesetzt", emb.enabled())

    n = await emb.index_missing(db)
    check("alle 4 Einträge bekommen einen Vektor", n == 4 and db.fetchall("SELECT COUNT(*) c FROM memory_embeddings")[0]["c"] == 4, n)
    check("zweiter Lauf trägt nichts nach", await emb.index_missing(db) == 0)

    hits = await emb.search(db, "Wer kann nicht selbst fahren?")
    check("findet nach Bedeutung (kein gemeinsames Wort mit dem Treffer)",
          bool(hits) and hits[0]["text"].startswith("Christoph"), hits)
    check("gibt nur Passendes zurück, nicht alles", hits is not None and len(hits) == 1, hits)
    check("Hauptgedächtnis wird beim Rückruf nicht doppelt geliefert (steht ohnehin im Text)",
          all(not h["text"].startswith("Hauptregel") for h in await emb.search(db, "Darf ich ihm ins Wort fallen?")))
    check("mit include_pinned wird es gefunden",
          any(h["text"].startswith("Hauptregel") for h in await emb.search(db, "Darf ich ihm ins Wort fallen?", include_pinned=True)))

    text = await emb.recall_text(db, "Wer kann heute nicht selbst fahren zur Baustelle?")
    check("recall_text liefert Block mit Treffer", "Christoph" in text and text.startswith("AUS DEM GEDÄCHTNIS"), text)
    check("recall_text schweigt bei Kurzem ('Hallo')", await emb.recall_text(db, "Hallo") == "")
    check("recall_text schweigt, wenn nichts passt", await emb.recall_text(db, "Wie wird das Wetter morgen in Karben?") == "")

    # Neu und geändert
    db.add("e", "Neuer Fahrer ohne Führerschein: Yusuf.")
    check("neuer Eintrag wird beim Suchen nachgetragen",
          any("Yusuf" in h["text"] for h in await emb.search(db, "Wer kann nicht selbst fahren?")))
    db.execute("UPDATE memory SET text=? WHERE id='c'", ("Der Name lautet David Reyes, nicht Davis, bitte immer so schreiben.",))
    check("geänderter Eintrag wird neu berechnet", await emb.index_missing(db) == 1)

    # ── Ein Gedächtnis: Dokumente (Hauptgedächtnis, Stehende Anweisungen, Firmenwissen) im selben Index ──
    long_sec = "## Lang\n" + "\n\n".join(f"Absatz {i}: " + "wort " * 60 for i in range(12))
    parts = emb.chunk_markdown("# Titel\n\n## Kurz\nEin kurzer Abschnitt mit genug Text für einen Treffer.\n\n" + long_sec)
    check("Abschnitte: zu lange werden geteilt, keiner ist zu groß", len(parts) > 3 and all(len(c["text"]) <= 1100 + 40 for c in parts), [len(c["text"]) for c in parts])
    check("Abschnitte: Folgestücke behalten ihre Überschrift", all(c["text"].startswith("## Lang") for c in parts if c["head"] == "Lang"))
    check("Abschnitte: die bloße Überschrift allein ist kein Abschnitt", not any(c["text"].strip() == "# Titel" for c in parts))

    db.execute("CREATE TABLE knowledge (id TEXT PRIMARY KEY, slug TEXT, title TEXT, content TEXT, enabled INTEGER DEFAULT 1, updated_at TEXT)")
    doc = ("# Hauptgedächtnis\n\n## Fahrzeuge\nDer Opel Vivaro ist das große Fahrzeug, der Peugeot Bipper das kleine Fahrzeug.\n\n"
           "## Server\nDer Server läuft auf Docker, dazu kommt n8n für die Abläufe.\n")
    db.execute("INSERT INTO knowledge VALUES ('k1','haupt','JARVIS Hauptgedächtnis',?,1,'t1')", (doc,))
    import tempfile as _tf
    ws = _tf.mkdtemp(); os.makedirs(os.path.join(ws, "abteilungen"))
    open(os.path.join(ws, "abteilungen", "firma.md"), "w", encoding="utf-8").write(
        "# Firma\n\n## Team\nChristoph hat keinen Führerschein, deshalb fährt ihn Jürgen oder Bernd mit.\n")
    os.environ["JARVIS_CC_WORKSPACE_DIR"] = ws
    n = await emb.sync_knowledge(db)
    check("Dokumente werden in Abschnitte zerlegt und indiziert (2 + 1)", n == 3, n)
    before = len(CALLS)
    check("unveränderte Dokumente kosten keinen Aufruf", await emb.sync_knowledge(db) == 0 and len(CALLS) == before)

    hits = await emb.search_all(db, "Welches Fahrzeug ist das große?")
    check("gemeinsame Suche findet den Dokumentabschnitt", bool(hits) and hits[0]["source"] == "JARVIS Hauptgedächtnis › Fahrzeuge", hits)
    hits = await emb.search_all(db, "Läuft der Server mit Docker?")
    check("… und einen anderen Abschnitt desselben Dokuments", bool(hits) and hits[0]["source"].endswith("› Server"), hits)
    hits = await emb.search_all(db, "Wer kann nicht selbst fahren?")
    srcs = {h["source"] for h in hits}
    check("eine Frage findet Fakten UND Firmenwissen zugleich", "Gedächtnis" in srcs and "Firmenwissen Reyes Service › Team" in srcs, srcs)
    check("jeder Treffer nennt seine Herkunft", all(h.get("source") for h in hits))
    pinned = await emb.search_all(db, "Darf ich ihm ins Wort fallen?", include_pinned=True)
    check("angeheftete Fakten sind Hauptgedächtnis", any(h["source"] == "Hauptgedächtnis" for h in pinned), pinned)
    text = await emb.recall_text(db, "Welches Fahrzeug nehme ich für die große Baustelle morgen?")
    check("Rückruf-Block nennt die Quelle", "[JARVIS Hauptgedächtnis › Fahrzeuge]" in text and text.startswith("AUS DEM GEDÄCHTNIS"), text)

    db.execute("UPDATE knowledge SET content=?, updated_at='t2' WHERE id='k1'", (doc.replace("Opel Vivaro", "Renault Master"),))
    check("geändertes Dokument wird neu zerlegt", await emb.sync_knowledge(db) == 2)
    check("… und der alte Stand ist weg", not any("Opel Vivaro" in r["text"] for r in db.fetchall("SELECT text FROM knowledge_chunks")))
    db.execute("UPDATE knowledge SET enabled=0 WHERE id='k1'")
    await emb.sync_knowledge(db)
    check("abgeschaltetes Dokument verschwindet aus dem Index", not db.fetchall("SELECT 1 FROM knowledge_chunks WHERE doc='k:k1'"))
    os.remove(os.path.join(ws, "abteilungen", "firma.md"))
    await emb.sync_knowledge(db)
    check("gelöschte Datei verschwindet aus dem Index", not db.fetchall("SELECT 1 FROM knowledge_chunks"))
    os.environ.pop("JARVIS_CC_WORKSPACE_DIR")

    # Ausfall: kein Absturz, None → Aufrufer nutzt Wortsuche
    async def broken(texts, timeout=20.0):
        return None
    emb.embed = broken
    db.add("f", "Ein Eintrag, der nicht indiziert werden kann.")
    check("Ausfall des Dienstes → None statt Ausnahme", await emb.search(db, "Wer kann nicht selbst fahren?") is None)
    check("index_one meldet Misserfolg, wirft nicht", await emb.index_one(db, "f", "x") is False)
    emb.embed = fake_embed

    # Abschalten
    os.environ["JARVIS_SEMANTIC_MEMORY"] = "0"
    before = len(CALLS)
    check("abgeschaltet → None", await emb.search(db, "Wer kann nicht selbst fahren?") is None and not emb.enabled())
    check("abgeschaltet → kein einziger Aufruf an den Dienst", len(CALLS) == before)
    os.environ.pop("JARVIS_SEMANTIC_MEMORY")
    os.environ["LOCAL_LLM_API_KEY"] = ""
    check("ohne Schlüssel → aus, ohne Fehler", not emb.enabled() and await emb.search(db, "irgendwas hier") is None)

    # echte Einbindung: die Werkzeuge nutzen das Modul
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "command_center", "backend",
                            "orchestrator", "builtin_tools.py"), encoding="utf-8").read()
    check("memory.search nutzt die gemeinsame semantische Suche", "_emb.search_all(" in src)
    check("memory.remember legt sofort einen Vektor an", "_emb.index_one(" in src)
    rt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "command_center", "backend",
                           "orchestrator", "runtime.py"), encoding="utf-8").read()
    check("der Rückruf vor jeder Antwort nutzt sie", "_emb.recall_text(" in rt)


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
