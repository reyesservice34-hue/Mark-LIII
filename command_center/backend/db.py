"""
Storage — SQLite in WAL mode behind one small synchronous wrapper.

Why SQLite and not a service: the command center is a single-node control
plane for one server. WAL mode gives concurrent readers, the process is the
only writer, and a single file inside the persistent volume is trivially
backed up. Everything goes through `Database` so a later move to Postgres is
one module, not a hunt through the codebase.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer', display_name TEXT NOT NULL DEFAULT '',
  disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  user_agent TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS api_tokens (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
  actor TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'operator', created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New conversation',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
  actor TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '', blocks TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'complete', run_id TEXT, created_at TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'specialist',
  capabilities TEXT NOT NULL DEFAULT '[]', tools TEXT NOT NULL DEFAULT '[]',
  model TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'built-in',
  config TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  last_activity_at TEXT, stats TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, task_id TEXT, conversation_id TEXT,
  message_id TEXT, parent_run_id TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, error TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '[]',
  usage TEXT NOT NULL DEFAULT '{}', initiated_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON agent_runs(agent_id, started_at);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, parent_id TEXT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '', assigned_agent TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'QUEUED', priority TEXT NOT NULL DEFAULT 'normal',
  created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, updated_at TEXT NOT NULL,
  output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  conversation_id TEXT, run_id TEXT, attachments TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE TABLE IF NOT EXISTS task_logs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL,
  message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_task_logs ON task_logs(task_id, ts);
CREATE TABLE IF NOT EXISTS tools (
  id TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
  permissions TEXT NOT NULL DEFAULT '[]', input_schema TEXT NOT NULL DEFAULT '{}',
  output_schema TEXT NOT NULL DEFAULT '{}', risk TEXT NOT NULL DEFAULT 'low',
  available INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS integrations (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'not_configured', capabilities TEXT NOT NULL DEFAULT '[]',
  last_sync_at TEXT, health TEXT NOT NULL DEFAULT '{}', config_state TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflows (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL, name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0, trigger TEXT NOT NULL DEFAULT '', last_execution_at TEXT,
  next_execution_at TEXT, last_status TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, provider TEXT NOT NULL, external_id TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT, finished_at TEXT, duration_ms INTEGER,
  error TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_wf_runs ON workflow_runs(workflow_id, started_at);
CREATE TABLE IF NOT EXISTS servers (
  id TEXT PRIMARY KEY, hostname TEXT NOT NULL, os TEXT NOT NULL DEFAULT '',
  last_seen_at TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '*', category TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL DEFAULT 'info',
  read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, link TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_notifications ON notifications(user_id, read, created_at);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, task_id TEXT, run_id TEXT, agent_id TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '',
  risk TEXT NOT NULL DEFAULT 'high', requested_by TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, expires_at TEXT,
  decided_at TEXT, decided_by TEXT, decision_note TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}', code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, actor_type TEXT NOT NULL, actor_id TEXT NOT NULL,
  agent_id TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '', task_id TEXT, run_id TEXT, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0,
  mime TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  owner TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'upload', task_id TEXT,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS logs (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL,
  source TEXT NOT NULL, message TEXT NOT NULL, task_id TEXT, agent_id TEXT, run_id TEXT,
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source, level);
-- pinned: gehört ins Hauptgedächtnis. Solche Sätze stehen in JEDEM Gespräch
-- im Systemtext, nicht erst nach einer Suche — deshalb ist ihre Zahl begrenzt.
CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY, text TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
  conversation_id TEXT, created_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS learning_records (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  problem TEXT NOT NULL DEFAULT '', lesson TEXT NOT NULL DEFAULT '',
  failed_attempts TEXT NOT NULL DEFAULT '[]', verification TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '', conversation_id TEXT, confidence REAL NOT NULL DEFAULT 1.0,
  tags TEXT NOT NULL DEFAULT '[]', uses INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_kind_updated ON learning_records(kind, updated_at);
CREATE TABLE IF NOT EXISTS core_evolution_checks (
  proposal_id TEXT PRIMARY KEY, file TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
  checks TEXT NOT NULL DEFAULT '[]', baseline TEXT NOT NULL DEFAULT '{}', candidate TEXT NOT NULL DEFAULT '{}',
  capability_gain TEXT NOT NULL DEFAULT '', verification_plan TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auto_learning_patterns (
  fingerprint TEXT PRIMARY KEY, signature TEXT NOT NULL DEFAULT '', success_count INTEGER NOT NULL DEFAULT 0,
  tools TEXT NOT NULL DEFAULT '[]', last_goal TEXT NOT NULL DEFAULT '', examples TEXT NOT NULL DEFAULT '[]',
  promoted_skill TEXT NOT NULL DEFAULT '', procedure_id TEXT NOT NULL DEFAULT '',
  specialist_notified INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_learning_updated ON auto_learning_patterns(updated_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
  ts TEXT NOT NULL, cpu REAL, ram REAL, disk REAL, load REAL, net_rx REAL, net_tx REAL
);

-- ── desktops the server may drive ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS desktop_devices (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '',
  actions TEXT NOT NULL DEFAULT '[]', registered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS desktop_commands (
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, action TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL, dispatched_at TEXT, finished_at TEXT,
  result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  requested_by TEXT NOT NULL DEFAULT '', agent_id TEXT NOT NULL DEFAULT '',
  task_id TEXT, run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_desktop_cmds ON desktop_commands(device_id, status, created_at);

-- ── teaching: recordings, the procedures distilled from them ────────────────
CREATE TABLE IF NOT EXISTS recordings (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'recording', user_id TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT '', conversation_id TEXT,
  started_at TEXT NOT NULL, ended_at TEXT, procedure_id TEXT, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS recording_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, recording_id TEXT NOT NULL,
  ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '', params TEXT NOT NULL DEFAULT '{}',
  result TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rec_events ON recording_events(recording_id, seq);
CREATE TABLE IF NOT EXISTS procedures (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  goal TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '[]',
  tools TEXT NOT NULL DEFAULT '[]', trigger TEXT NOT NULL DEFAULT '{}',
  agent_id TEXT NOT NULL DEFAULT '', recording_id TEXT, enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '',
  runs INTEGER NOT NULL DEFAULT 0, last_run_at TEXT, last_status TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS learned_agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '',
  capabilities TEXT NOT NULL DEFAULT '[]', tools TEXT NOT NULL DEFAULT '[]',
  icon TEXT NOT NULL DEFAULT 'sparkles', enabled INTEGER NOT NULL DEFAULT 1,
  procedure_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT ''
);

-- Werkzeuge, die JARVIS sich selbst geschrieben hat. Die Quelle liegt als
-- Datei im Arbeitsbereich; hier steht der Zustand, damit sie einen Neustart
-- übersteht und nachvollziehbar bleibt, wer wann was freigegeben hat.
CREATE TABLE IF NOT EXISTS self_tools (
  name TEXT PRIMARY KEY, file TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  risk TEXT NOT NULL DEFAULT 'medium', status TEXT NOT NULL DEFAULT 'draft',
  source_sha TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  written_by TEXT NOT NULL DEFAULT '', activated_by TEXT NOT NULL DEFAULT '',
  activated_at TEXT
);

-- Fremde Werkzeugserver nach dem Model Context Protocol — dieselben, die auch
-- Claude benutzt. Das Token bleibt hier und wird nie ausgeliefert; die
-- Oberfläche erfährt nur, ob eines hinterlegt ist.
CREATE TABLE IF NOT EXISTS mcp_servers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
  token TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown', detail TEXT NOT NULL DEFAULT '',
  tools TEXT NOT NULL DEFAULT '[]', tool_count INTEGER NOT NULL DEFAULT 0,
  server_info TEXT NOT NULL DEFAULT '{}', requires_approval INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, checked_at TEXT, created_by TEXT NOT NULL DEFAULT ''
);

-- Fähigkeiten als Text: eine Anleitung, die er bei Bedarf aufschlägt. Im
-- Systemtext steht nur Name und Zweck, der Inhalt kommt erst, wenn er ihn
-- braucht — sonst füllt jede ungenutzte Fähigkeit jedes Gespräch.
CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'manual',
  uses INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT ''
);
-- Wissensspeicher: was zu lang ist, um in jeder Anfrage mitzureisen.
-- Das Hauptgedächtnis fasst 30 Sätze, weil es JEDES Mal mitgeschickt wird.
-- Ein Systemhandbuch passt da nicht hinein und gehört trotzdem zu dem, was er
-- wissen muss. Also hier: im Systemtext steht nur Titel und Zweck, den vollen
-- Text holt er sich mit knowledge.open, wenn er ihn braucht.
CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT ''
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:20]
    return f"{prefix}_{raw}" if prefix else raw


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(raw: str | None, default: Any = None) -> Any:
    if raw is None or raw == "":
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default if default is not None else {}


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # ── schema ───────────────────────────────────────────────────────────
    # Spalten, die später dazugekommen sind. CREATE TABLE IF NOT EXISTS legt
    # eine bestehende Tabelle nicht an, also fehlt die neue Spalte auf jedem
    # Server, der schon lief — und der Fehler kommt erst beim ersten Zugriff.
    ADDED_COLUMNS: dict[str, dict[str, str]] = {
        "memory": {"pinned": "INTEGER NOT NULL DEFAULT 0"},
        # core_evolution.py und core_watchdog.py schreiben diese Spalten; das
        # ursprüngliche CREATE TABLE kannte sie nicht, verify() scheiterte daher
        # an jeder Datenbank mit "no column named baseline_sha".
        "core_evolution_checks": {
            "baseline_sha": "TEXT NOT NULL DEFAULT ''",
            "candidate_sha": "TEXT NOT NULL DEFAULT ''",
            "stage": "TEXT NOT NULL DEFAULT 'verified'",
            "activation_mode": "TEXT NOT NULL DEFAULT ''",
            "verified_at": "TEXT",
            "applied_at": "TEXT",
            "activated_at": "TEXT",
            "stable_at": "TEXT",
            "rollback_at": "TEXT",
        },
    }

    def _ensure_columns(self) -> None:
        for table, columns in self.ADDED_COLUMNS.items():
            try:
                have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                continue
            if not have:
                continue
            for name, ddl in columns.items():
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._ensure_columns()
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] < SCHEMA_VERSION:
                self._conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))

    # ── primitives ───────────────────────────────────────────────────────
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, [tuple(r) for r in rows])

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    def insert(self, table: str, values: dict[str, Any]) -> None:
        cols = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))

    def upsert(self, table: str, values: dict[str, Any], key: str = "id") -> None:
        cols = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        updates = ", ".join(f"{c}=excluded.{c}" for c in values if c != key)
        self.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT({key}) DO UPDATE SET {updates}",
            list(values.values()),
        )

    def update(self, table: str, key_value: Any, values: dict[str, Any], key: str = "id") -> int:
        if not values:
            return 0
        sets = ", ".join(f"{c}=?" for c in values)
        cur = self.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", [*values.values(), key_value])
        return cur.rowcount

    def transaction(self):
        return _Transaction(self)

    # ── settings k/v ─────────────────────────────────────────────────────
    def get_setting(self, key: str, default: Any = None) -> Any:
        raw = self.scalar("SELECT value FROM settings WHERE key=?", (key,))
        return loads(raw, default) if raw is not None else default

    def set_setting(self, key: str, value: Any) -> None:
        self.upsert("settings", {"key": key, "value": dumps(value)}, key="key")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db._conn.execute("COMMIT")
            else:
                self.db._conn.execute("ROLLBACK")
        finally:
            self.db._lock.release()
        return False
