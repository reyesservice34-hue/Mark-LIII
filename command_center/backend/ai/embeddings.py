"""Semantische Suche im Gedächtnis: nach Bedeutung statt nach Wörtern.

Jeder Gedächtniseintrag bekommt einen Vektor (Embedding), der seine Bedeutung abbildet. Eine Frage wird genauso
umgewandelt; gefunden werden die Einträge, deren Vektor der Frage am nächsten liegt. "Wer kann nicht selbst
fahren?" findet so "Christoph hat keinen Führerschein", obwohl kein Wort gleich ist.

Der Zugang ist derselbe wie beim Sprachmodell (LOCAL_LLM_URL / LOCAL_LLM_API_KEY, z. B. OpenRouter), das Modell
kommt aus JARVIS_EMBED_MODEL. Geht etwas schief (kein Netz, kein Guthaben, Modell weg), liefern alle Funktionen
None zurück und die Aufrufer fallen auf die Wortsuche zurück. Das Gedächtnis funktioniert also weiter, nur
weniger klug. Abschalten: JARVIS_SEMANTIC_MEMORY=0.

Die Vektoren stehen in einer eigenen Tabelle (memory_embeddings); die Tabelle memory bleibt unverändert.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import httpx

DEFAULT_MODEL = "openai/text-embedding-3-small"
# Local embedding models can time out on large first-run batches, especially
# while the model is still warming up. Smaller batches keep indexing reliable
# and make progress visible without changing the resulting vectors.
BATCH = max(1, min(int(os.environ.get("JARVIS_EMBED_BATCH", "8")), 64))
MAX_CHARS = 2000
CHUNK_CHARS = 1100        # ein Abschnitt eines Dokuments: groß genug für Sinn, klein genug für den Systemtext


def _model() -> str:
    return os.environ.get("JARVIS_EMBED_MODEL", "").strip() or DEFAULT_MODEL


def _endpoint() -> tuple[str, str]:
    base = (os.environ.get("JARVIS_EMBED_URL") or os.environ.get("LOCAL_LLM_URL") or "").rstrip("/")
    key = os.environ.get("JARVIS_EMBED_KEY") or os.environ.get("LOCAL_LLM_API_KEY") or ""
    return base, key


def enabled() -> bool:
    base, key = _endpoint()
    return os.environ.get("JARVIS_SEMANTIC_MEMORY", "1") != "0" and bool(base and key)


def ensure(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS memory_embeddings ("
               "id TEXT PRIMARY KEY, model TEXT NOT NULL, n INTEGER NOT NULL, vec TEXT NOT NULL)")


def _unit(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


async def embed(texts: list[str], timeout: float = 20.0) -> list[list[float]] | None:
    """Vektoren für die Texte, oder None, wenn der Dienst nicht antwortet. Nie eine Ausnahme."""
    if not texts or not enabled():
        return None
    base, key = _endpoint()
    out: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            for i in range(0, len(texts), BATCH):
                chunk = [t[:MAX_CHARS] or " " for t in texts[i:i + BATCH]]
                r = await http.post(base + "/v1/embeddings", headers={"Authorization": "Bearer " + key},
                                    json={"model": _model(), "input": chunk})
                if r.status_code != 200:
                    return None
                data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
                if len(data) != len(chunk):
                    return None
                out.extend(_unit(d["embedding"]) for d in data)
    except Exception:  # noqa: BLE001
        return None
    return out


def _store(db, rows: list[dict], vecs: list[list[float]]) -> None:
    model = _model()
    for row, vec in zip(rows, vecs):
        db.execute("INSERT OR REPLACE INTO memory_embeddings (id, model, n, vec) VALUES (?,?,?,?)",
                   (row["id"], model, len(row["text"]), json.dumps(vec)))


async def index_missing(db, limit: int = 500) -> int:
    """Alles nachtragen, was noch keinen (aktuellen) Vektor hat: neue Einträge, geänderte, anderes Modell."""
    ensure(db)
    rows = db.fetchall(
        "SELECT m.id, m.text FROM memory m LEFT JOIN memory_embeddings e "
        "ON e.id = m.id AND e.model = ? AND e.n = length(m.text) WHERE e.id IS NULL LIMIT ?", (_model(), limit))
    if not rows:
        return 0
    vecs = await embed([r["text"] for r in rows])
    if not vecs:
        return 0
    _store(db, rows, vecs)
    return len(rows)


async def index_one(db, memory_id: str, text: str) -> bool:
    ensure(db)
    vecs = await embed([text], timeout=8.0)
    if not vecs:
        return False   # bleibt liegen, index_missing holt es beim nächsten Suchen nach
    _store(db, [{"id": memory_id, "text": text}], vecs)
    return True


async def search(db, query: str, limit: int = 6, min_score: float = 0.30, include_pinned: bool = False,
                 timeout: float = 20.0) -> list[dict] | None:
    """Die Einträge, die der Frage inhaltlich am nächsten liegen (beste zuerst), oder None, wenn nicht möglich."""
    if not enabled() or not (query or "").strip():
        return None
    try:
        await index_missing(db)
        qv = await embed([query], timeout=timeout)
        if not qv:
            return None
        rows = db.fetchall(
            "SELECT m.text, m.created_at, m.pinned, e.vec FROM memory m JOIN memory_embeddings e "
            "ON e.id = m.id AND e.model = ? AND e.n = length(m.text)"
            + ("" if include_pinned else " WHERE m.pinned = 0"), (_model(),))
    except Exception:  # noqa: BLE001
        return None
    q = qv[0]
    hits = []
    for r in rows:
        try:
            score = sum(a * b for a, b in zip(q, json.loads(r["vec"])))
        except Exception:  # noqa: BLE001
            continue
        if score >= min_score:
            hits.append({"text": r["text"], "created_at": r["created_at"], "score": round(score, 3)})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


# ── Ein Gedächtnis: Fakten, Hauptgedächtnis-Dokumente und Firmenwissen in einem Index ────────────────────────────
#
# Das Hauptgedächtnis besteht aus drei Teilen: den angehefteten Fakten (stehen ohnehin in jedem Systemtext), den
# großen Dokumenten in der Tabelle `knowledge` (Hauptgedächtnis, Stehende Anweisungen …) und dem Firmenwissen
# (abteilungen/firma.md). Früher kannte die Suche nur die Fakten; die Dokumente kamen nur, wenn jemand das Werkzeug
# memory.knowledge aufrief. Jetzt werden auch sie in Abschnitte zerlegt und nach Bedeutung durchsucht — eine
# Frage findet den passenden Abschnitt, egal in welchem Teil er steht.

def chunk_markdown(text: str, size: int = CHUNK_CHARS) -> list[dict]:
    """Ein Markdown-Dokument in Abschnitte teilen: an Überschriften, zu lange Abschnitte an Absätzen.
    Jeder Abschnitt trägt seine Überschrift mit, damit er für sich allein verständlich bleibt."""
    out: list[dict] = []
    for sec in re.split(r"(?m)^(?=#{1,4} )", text or ""):
        sec = sec.strip()
        if not sec:
            continue
        first = sec.split("\n", 1)[0]
        head = first.lstrip("# ").strip() if first.startswith("#") else ""
        if len(sec) <= size:
            pieces = [sec]
        else:
            pieces, cur = [], ""
            for para in re.split(r"\n\s*\n", sec):
                para = para.strip()
                while len(para) > size:                       # ein einzelner Riesenabsatz: an der Zeile trennen
                    cut = para.rfind("\n", 0, size) if "\n" in para[:size] else para.rfind(" ", 0, size)
                    cut = cut if cut > size // 3 else size
                    if cur:
                        pieces.append(cur); cur = ""
                    pieces.append(para[:cut].strip()); para = para[cut:].strip()
                if cur and len(cur) + len(para) + 2 > size:
                    pieces.append(cur); cur = ""
                cur = (cur + "\n\n" + para).strip() if cur else para
            if cur:
                pieces.append(cur)
            if head:                                          # Folgestücke behalten die Überschrift
                pieces = [p if i == 0 or p.startswith("#") else f"## {head} (Forts.)\n{p}" for i, p in enumerate(pieces)]
        out.extend({"head": head, "text": p} for p in pieces if len(p) >= 40)
    return out


def _doc_sources(db) -> list[dict]:
    """Alle Dokumente, die zum Gedächtnis gehören, mit einer Marke, die sich ändert, sobald sich der Inhalt ändert."""
    docs: list[dict] = []
    try:
        for r in db.fetchall("SELECT id, title, content, updated_at FROM knowledge WHERE COALESCE(enabled, 1) != 0"):
            docs.append({"doc": "k:" + r["id"], "title": r["title"], "text": r["content"] or "",
                         "h": f"{r['updated_at']}:{len(r['content'] or '')}"})
    except Exception:  # noqa: BLE001  — keine Tabelle: dann eben nur die Fakten
        pass
    ws = os.environ.get("JARVIS_CC_WORKSPACE_DIR", "")
    if ws:
        f = Path(ws) / "abteilungen" / "firma.md"
        try:
            st = f.stat()
            docs.append({"doc": "f:firma", "title": "Firmenwissen Reyes Service", "text": f.read_text(encoding="utf-8"),
                         "h": f"{st.st_mtime_ns}:{st.st_size}"})
        except OSError:
            pass
    return docs


def ensure_chunks(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks ("
               "id TEXT PRIMARY KEY, doc TEXT NOT NULL, title TEXT NOT NULL, head TEXT NOT NULL, text TEXT NOT NULL, "
               "h TEXT NOT NULL, model TEXT NOT NULL, vec TEXT NOT NULL)")


async def sync_knowledge(db) -> int:
    """Geänderte, neue oder entfernte Dokumente im Index nachziehen. Ein unveränderter Bestand kostet nichts."""
    ensure_chunks(db)
    docs = _doc_sources(db)
    model = _model()
    have = {r["doc"]: r for r in db.fetchall("SELECT doc, MIN(h) lo, MAX(h) hi, MIN(model) m FROM knowledge_chunks GROUP BY doc")}
    live = {d["doc"] for d in docs}
    for gone in set(have) - live:                            # Dokument abgeschaltet oder gelöscht
        db.execute("DELETE FROM knowledge_chunks WHERE doc = ?", (gone,))
    todo = [d for d in docs if not (d["doc"] in have and have[d["doc"]]["lo"] == have[d["doc"]]["hi"] == d["h"]
                                    and have[d["doc"]]["m"] == model)]
    n = 0
    for d in todo:
        chunks = chunk_markdown(d["text"])
        if not chunks:
            continue
        vecs = await embed([f"{d['title']}\n{c['text']}" for c in chunks])
        if not vecs:
            return n                                         # später wieder; bis dahin gilt der alte Stand
        db.execute("DELETE FROM knowledge_chunks WHERE doc = ?", (d["doc"],))
        for i, (c, v) in enumerate(zip(chunks, vecs)):
            db.execute("INSERT INTO knowledge_chunks (id, doc, title, head, text, h, model, vec) VALUES (?,?,?,?,?,?,?,?)",
                       (f"{d['doc']}#{i}", d["doc"], d["title"], c["head"], c["text"], d["h"], model, json.dumps(v)))
        n += len(chunks)
    return n


async def search_all(db, query: str, limit: int = 6, min_score: float = 0.30, include_pinned: bool = False,
                     timeout: float = 20.0) -> list[dict] | None:
    """Die Suche über das ganze Gedächtnis: Fakten UND Dokumentabschnitte, beste zuerst. None, wenn nicht möglich.

    Jeder Treffer sagt, woher er kommt (source): "Gedächtnis", "Hauptgedächtnis" (angeheftet) oder der Titel des
    Dokuments mit Abschnitt. Angeheftete Fakten stehen ohnehin im Systemtext; sie kommen nur mit include_pinned.
    """
    if not enabled() or not (query or "").strip():
        return None
    try:
        await index_missing(db)
        await sync_knowledge(db)
        qv = await embed([query], timeout=timeout)
        if not qv:
            return None
        facts = db.fetchall(
            "SELECT m.text, m.created_at, m.pinned, e.vec FROM memory m JOIN memory_embeddings e "
            "ON e.id = m.id AND e.model = ? AND e.n = length(m.text)"
            + ("" if include_pinned else " WHERE m.pinned = 0"), (_model(),))
        chunks = db.fetchall("SELECT title, head, text, vec FROM knowledge_chunks WHERE model = ?", (_model(),))
    except Exception:  # noqa: BLE001
        return None
    q = qv[0]
    hits = []
    for r in facts:
        try:
            score = sum(a * b for a, b in zip(q, json.loads(r["vec"])))
        except Exception:  # noqa: BLE001
            continue
        if score >= min_score:
            hits.append({"text": r["text"], "created_at": r["created_at"], "score": round(score, 3),
                         "source": "Hauptgedächtnis" if r["pinned"] else "Gedächtnis"})
    for r in chunks:
        try:
            score = sum(a * b for a, b in zip(q, json.loads(r["vec"])))
        except Exception:  # noqa: BLE001
            continue
        if score >= min_score:
            hits.append({"text": r["text"], "created_at": "", "score": round(score, 3),
                         "source": r["title"] + (f" › {r['head']}" if r["head"] else "")})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


async def recall_text(db, goal: str, limit: int = 6) -> str:
    """Der Textblock für den Systemtext: alles aus dem einen Gedächtnis, was inhaltlich zur Anfrage passt.
    Leer, wenn nichts passt.

    Kurze Timeouts: Das läuft vor jeder Antwort, und ein träger Embedding-Dienst darf sie nicht aufhalten.
    """
    if len((goal or "").strip()) < 12:
        return ""
    hits = await search_all(db, goal, limit=limit, min_score=0.33, include_pinned=False, timeout=3.0)
    if not hits:
        return ""
    lines = [f"- [{h['source']}] {h['text'][:700 if h['source'] not in ('Gedächtnis', 'Hauptgedächtnis') else 300]}"
             for h in hits]
    return ("AUS DEM GEDÄCHTNIS — Fakten, Hauptgedächtnis und Firmenwissen, nach Bedeutung zu dieser Anfrage gefunden. "
            "Nutze es zuerst, bevor du rätst oder nachfragst:\n" + "\n".join(lines))
