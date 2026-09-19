"""
Wissensspeicher: was er dauerhaft wissen soll, aber nicht dauernd mitschleppen.

Das Gedächtnis hatte bisher zwei Plätze, und beiden fehlte etwas:

  Hauptgedächtnis   30 Sätze, bei JEDER Anfrage mitgeschickt. Deshalb kurz —
                    ein Systemhandbuch passt da nicht hinein.
  Gemerktes         einzelne Tatsachen, die gesucht werden. Auch kurz.

Was fehlte, war der dritte Fall: ein längerer Text, den er kennen MUSS, der
aber nicht in jede Anfrage gehört — die Architektur dieses Servers, die
Eigenheiten der Anlage, ein Konflikt, der noch zu klären ist. Der lag bisher
als Datei im Repository und war damit für ihn unsichtbar: Er liest keine
Dateien, die niemand in seinen Systemtext stellt.

Also zweistufig, wie bei den Fähigkeiten: Im Systemtext stehen nur Titel und
Zweck; den vollen Text holt er mit `knowledge.open`, wenn er ihn braucht.
Der Unterschied zu einer Fähigkeit ist nicht technisch, sondern inhaltlich —
und er entscheidet, wo etwas hingehört:

    Fähigkeit       WIE etwas zu tun ist.    „So schreibst du ein Angebot."
    Wissen          WAS der Fall ist.        „Der Server liegt unter /root/jarvis."

Deshalb steht dieser Speicher unter Gedächtnis und nicht unter Fähigkeiten:
Wer wissen will, was JARVIS weiß, sucht dort.
"""
from __future__ import annotations

import re

from ..db import Database, new_id, now_iso

SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,60}$")
MAX_CONTENT = 200_000        # ein Handbuch darf lang sein, ein Roman nicht
MAX_SUMMARY = 300


class KnowledgeError(RuntimeError):
    """Grund, der in die Oberfläche gehört."""


def slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "eintrag")[:60]


class KnowledgeBase:
    def __init__(self, db: Database, bus=None) -> None:
        self.db = db
        self.bus = bus

    # ── lesen ────────────────────────────────────────────────────────────
    def all(self, *, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM knowledge"
        if enabled_only:
            sql += " WHERE enabled=1"
        return [self._public(r) for r in self.db.fetchall(sql + " ORDER BY title")]

    def get(self, slug: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM knowledge WHERE slug=?", ((slug or "").strip().lower(),))
        return dict(row) if row else None

    @staticmethod
    def _public(row) -> dict:
        r = dict(row)
        return {"slug": r["slug"], "title": r["title"], "summary": r["summary"],
                "enabled": bool(r["enabled"]), "uses": r["uses"],
                "bytes": len(r["content"] or ""),
                "lines": (r["content"] or "").count("\n") + 1,
                "updated_at": r["updated_at"], "created_by": r["created_by"]}

    def catalogue(self) -> str:
        """Was im Systemtext steht: Titel und Zweck, nie der volle Text."""
        rows = self.all(enabled_only=True)
        if not rows:
            return ""
        lines = [f"- {r['slug']}: {r['title']} — {r['summary']}" for r in rows]
        return ("WISSENSSPEICHER (was du über diese Anlage und ihre Umgebung weißt. "
                "Rufe knowledge.open mit dem Namen auf, BEVOR du über eines dieser "
                "Themen sprichst oder daran arbeitest — rate nicht):\n" + "\n".join(lines))

    def open(self, slug: str) -> dict:
        row = self.get(slug)
        if not row:
            bekannt = ", ".join(r["slug"] for r in self.all(enabled_only=True)) or "keines"
            raise KnowledgeError(f"Ein Wissensdokument '{slug}' gibt es nicht. Vorhanden: {bekannt}")
        if not row["enabled"]:
            raise KnowledgeError(f"Das Wissensdokument '{slug}' ist abgeschaltet.")
        self.db.execute("UPDATE knowledge SET uses=uses+1 WHERE slug=?", (row["slug"],))
        return {"slug": row["slug"], "title": row["title"], "content": row["content"]}

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Wo steht etwas dazu? Gibt Fundstellen mit Umgebung, nicht ganze Texte."""
        begriff = (query or "").strip().lower()
        if not begriff:
            return []
        treffer: list[dict] = []
        for row in self.db.fetchall("SELECT * FROM knowledge WHERE enabled=1"):
            zeilen = (row["content"] or "").splitlines()
            for i, zeile in enumerate(zeilen):
                if begriff in zeile.lower():
                    umgebung = "\n".join(zeilen[max(0, i - 1):i + 2])
                    treffer.append({"slug": row["slug"], "title": row["title"],
                                    "line": i + 1, "excerpt": umgebung[:500]})
                    break                      # eine Fundstelle je Dokument reicht zum Finden
            if len(treffer) >= limit:
                break
        return treffer

    # ── schreiben ────────────────────────────────────────────────────────
    def save(self, *, title: str, content: str, summary: str = "", slug: str = "",
             actor: str = "") -> dict:
        title = (title or "").strip()
        if not title:
            raise KnowledgeError("Ohne Titel nicht — er steht später im Systemtext.")
        content = content or ""
        if len(content) > MAX_CONTENT:
            raise KnowledgeError(
                f"Der Text ist {len(content)} Zeichen lang, erlaubt sind {MAX_CONTENT}. "
                f"Lieber auf mehrere Dokumente aufteilen — dann holt er sich auch nur das, "
                f"was er gerade braucht.")
        slug = (slug or slugify(title)).strip().lower()
        if not SLUG_RE.match(slug):
            raise KnowledgeError(
                f"'{slug}' geht als Name nicht: klein, beginnend mit einem Buchstaben, "
                f"nur Buchstaben, Ziffern, - und _.")
        if not summary.strip():
            # Lieber die erste sinnvolle Zeile als ein leeres Feld: Ohne Zweck
            # im Katalog weiß er nicht, wann er das Dokument aufschlagen soll.
            for zeile in content.splitlines():
                z = zeile.strip().lstrip("#").strip()
                if z and not z.startswith(("---", ">", "|")):
                    summary = z
                    break
        summary = summary.strip()[:MAX_SUMMARY]

        vorhanden = self.get(slug)
        jetzt = now_iso()
        if vorhanden:
            self.db.update("knowledge", vorhanden["id"], {
                "title": title, "summary": summary, "content": content, "updated_at": jetzt})
        else:
            self.db.insert("knowledge", {
                "id": new_id("kn"), "slug": slug, "title": title, "summary": summary,
                "content": content, "enabled": 1, "uses": 0,
                "created_at": jetzt, "updated_at": jetzt, "created_by": actor})
        if self.bus:
            self.bus.publish("memory.knowledge", {"slug": slug, "created": not vorhanden})
        return self._public(self.db.fetchone("SELECT * FROM knowledge WHERE slug=?", (slug,)))

    def set_enabled(self, slug: str, enabled: bool) -> dict | None:
        row = self.get(slug)
        if not row:
            return None
        self.db.update("knowledge", row["id"], {"enabled": 1 if enabled else 0,
                                                "updated_at": now_iso()})
        if self.bus:
            self.bus.publish("memory.knowledge", {"slug": slug, "enabled": enabled})
        return self._public(self.db.fetchone("SELECT * FROM knowledge WHERE slug=?", (slug,)))

    def remove(self, slug: str) -> bool:
        row = self.get(slug)
        if not row:
            return False
        self.db.execute("DELETE FROM knowledge WHERE slug=?", (row["slug"],))
        if self.bus:
            self.bus.publish("memory.knowledge", {"slug": slug, "removed": True})
        return True


__all__ = ["KnowledgeBase", "KnowledgeError", "slugify"]
