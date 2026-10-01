"""
Fähigkeiten: Anleitungen, die er bei Bedarf aufschlägt.

Eine Fähigkeit ist Text — wie ein Handgriff, den man einmal aufschreibt: „So
schreibst du ein Angebot für Reyes Service", „So legst du einen Bautermin an".
Kein Code, keine Anbindung, nur Wissen in einer Form, die jedes Modell lesen
kann. Genau darin liegt der Punkt: Das funktioniert mit Anthropic, OpenAI,
Gemini und einem lokalen Modell gleichermaßen, weil nichts daran
anbieterspezifisch ist.

Aufgeschlagen wird in zwei Stufen, so wie Claude es mit seinen Skills macht:
Im Systemtext steht nur, welche Fähigkeiten es gibt und wofür sie da sind. Der
volle Text kommt erst, wenn er `skill.open` aufruft. Andernfalls würde jede
ungenutzte Anleitung jedes Gespräch verteuern und verwässern.
"""
from __future__ import annotations

import re

from ..db import Database, new_id, now_iso

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")
MAX_CONTENT = 60_000


class SkillError(RuntimeError):
    """Grund, der in die Oberfläche gehört."""


def parse_front_matter(text: str) -> tuple[dict, str]:
    """YAML-Kopf einer SKILL.md lesen, ohne eine YAML-Bibliothek zu verlangen.

    Es geht nur um `name:` und `description:` in den ersten Zeilen — das ist,
    was eine Fähigkeit im Umlauf ausmacht. Alles Übrige bleibt Teil des Textes.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip("'\"")
    return meta, body.lstrip("\n")


class SkillLibrary:
    def __init__(self, state) -> None:
        self.state = state
        self.db: Database = state.db

    # ── Bestand ──────────────────────────────────────────────────────────
    def all(self, *, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM skills" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY name"
        return [self._public(r) for r in self.db.fetchall(sql)]

    def get(self, name: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM skills WHERE name=?", (name,))
        return self._public(row) if row else None

    @staticmethod
    def _public(row: dict) -> dict:
        return {
            "id": row["id"], "name": row["name"], "title": row["title"],
            "description": row["description"], "content": row["content"],
            "enabled": bool(row["enabled"]), "source": row["source"], "uses": row["uses"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "created_by": row["created_by"], "length": len(row["content"]),
        }

    # ── Anlegen und ändern ───────────────────────────────────────────────
    def save(self, *, name: str = "", content: str = "", title: str = "", description: str = "",
             actor: str = "", source: str = "manual") -> dict:
        """Anlegen oder überschreiben. Ein Kopf in der Datei gewinnt über die Felder.

        Wer eine SKILL.md aus einem anderen Werkzeug einfügt, soll sie nicht
        auseinandernehmen müssen — Name und Zweck stehen dort schon drin.
        """
        meta, body = parse_front_matter(content or "")
        name = (meta.get("name") or name or "").strip().lower().replace(" ", "-")
        description = (meta.get("description") or description or "").strip()
        title = (meta.get("title") or title or "").strip()
        if not NAME_RE.match(name):
            raise SkillError("Der Name darf nur Kleinbuchstaben, Ziffern, - und _ enthalten "
                             "und muss mit einem Buchstaben beginnen.")
        if not body.strip():
            raise SkillError("Eine Fähigkeit ohne Text kann er nicht aufschlagen.")
        if len(body) > MAX_CONTENT:
            raise SkillError(f"Der Text ist zu lang ({len(body)} Zeichen, erlaubt {MAX_CONTENT}).")
        if not description:
            # Ohne Zweck weiß er nie, wann er sie aufschlagen soll. Dann lieber
            # die erste sinnvolle Zeile nehmen als ein leeres Feld.
            first = next((ln.strip(" #").strip() for ln in body.splitlines() if ln.strip()), "")
            description = first[:200]

        now = now_iso()
        existing = self.db.fetchone("SELECT id, created_at, uses FROM skills WHERE name=?", (name,))
        row = {
            "id": existing["id"] if existing else new_id("skill"),
            "name": name, "title": title or name.replace("-", " ").title(),
            "description": description[:400], "content": body, "enabled": 1, "source": source,
            "uses": existing["uses"] if existing else 0,
            "created_at": existing["created_at"] if existing else now, "updated_at": now,
            "created_by": actor,
        }
        self.db.upsert("skills", row)
        self.state.log.audit(actor_type="user", actor_id=actor,
                             action="skill.update" if existing else "skill.create",
                             target=name, status="ok")
        return self._public(row)

    def set_enabled(self, name: str, enabled: bool) -> dict | None:
        if not self.db.fetchone("SELECT id FROM skills WHERE name=?", (name,)):
            return None
        self.db.execute("UPDATE skills SET enabled=?, updated_at=? WHERE name=?",
                        (1 if enabled else 0, now_iso(), name))
        return self.get(name)

    def remove(self, name: str, actor: str = "") -> bool:
        if not self.db.fetchone("SELECT id FROM skills WHERE name=?", (name,)):
            return False
        self.db.execute("DELETE FROM skills WHERE name=?", (name,))
        self.state.log.audit(actor_type="user", actor_id=actor, action="skill.remove",
                             target=name, status="ok")
        return True

    # ── Benutzung ────────────────────────────────────────────────────────
    def catalogue(self) -> str:
        """Was im Systemtext steht: Namen und Zweck, sonst nichts."""
        rows = self.all(enabled_only=True)
        if not rows:
            return ""
        lines = [f"- {r['name']}: {r['description']}" for r in rows]
        return ("FÄHIGKEITEN (Anleitungen, die du bei Bedarf aufschlägst — rufe skill.open mit dem "
                "Namen auf, bevor du eine Aufgabe dieser Art angehst):\n" + "\n".join(lines))

    def open(self, name: str) -> dict:
        row = self.get((name or "").strip().lower())
        if not row:
            known = ", ".join(r["name"] for r in self.all(enabled_only=True)) or "keine"
            raise SkillError(f"Die Fähigkeit '{name}' gibt es nicht. Vorhanden: {known}")
        if not row["enabled"]:
            raise SkillError(f"Die Fähigkeit '{name}' ist abgeschaltet.")
        self.db.execute("UPDATE skills SET uses=uses+1 WHERE name=?", (row["name"],))
        return {"name": row["name"], "title": row["title"], "content": row["content"]}


__all__ = ["SkillLibrary", "SkillError", "parse_front_matter"]
