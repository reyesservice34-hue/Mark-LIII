import json
import re
import threading
import hashlib
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"

BRAIN_DIR        = BASE_DIR / "brain"
BRAIN_MEMORY_DIR = BRAIN_DIR / "memory"
EPISODIC_PATH    = BRAIN_MEMORY_DIR / "episodic" / "sessions.jsonl"
SEMANTIC_PATH    = BRAIN_MEMORY_DIR / "semantic" / "archive.jsonl"
LEARNING_INBOX_PATH = BRAIN_DIR / "ingestion" / "inbox" / "candidates.jsonl"
LEARNING_VALIDATED_PATH = BRAIN_DIR / "ingestion" / "validated" / "processed.jsonl"
LEARNING_REJECTED_PATH = BRAIN_DIR / "ingestion" / "rejected" / "rejected.jsonl"
RETRIEVAL_DB_PATH = BRAIN_DIR / "indexes" / "memory_fts.sqlite3"

_lock            = Lock()
MAX_VALUE_LENGTH = 380

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect — so growing the memory grew
# every single request. When it filled, _trim_to_limit() deleted the oldest
# entries and printed one line to a console nobody reads. A memory described as
# "deeply remembers projects, preferences and personal context" was in practice
# two pages long, and quietly forgot your sister's name after a few weeks.
#
# Storage and prompt budget are now separate concerns:
#
#   MEMORY_MAX_CHARS  — a runaway guard, not a feature limit. Nothing normal
#                       reaches it; a bug writing in a loop does.
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#                       Smaller than the old whole-memory dump, so sessions
#                       start *faster* than before, not slower.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6


def _append_brain_record(path: Path, payload: dict) -> None:
    """Append one durable record to the MIA brain as UTF-8 JSONL."""
    if not isinstance(payload, dict):
        return

    row = dict(payload)
    row.setdefault(
        "archived_at",
        datetime.now().isoformat(timespec="seconds"),
    )

    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_brain_records(path: Path) -> list[dict]:
    """Read valid JSONL records. Broken individual lines are ignored."""
    if not path.exists():
        return []

    records: list[dict] = []

    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except Exception as exc:
        print(f"[Memory] Brain archive read error: {exc}")

    return records



_SEMANTIC_CATEGORIES = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
_SECRET_KEY_RE = re.compile(r"api[_-]?key|token|password|passwd|secret|credential|cookie|private[_-]?key", re.I)


def _semantic_safe(category: str, key: str, value: str) -> bool:
    text_key = f"{category}/{key}"
    text_val = str(value or "")
    if _SECRET_KEY_RE.search(text_key):
        return False
    if "BEGIN PRIVATE KEY" in text_val or "BEGIN OPENSSH PRIVATE KEY" in text_val:
        return False
    if text_val.startswith("sk-") or text_val.startswith("Bearer "):
        return False
    return True


def _archive_semantic_fact(category: str, key: str, value: str, source: str = "memory") -> None:
    category = str(category or "").strip()
    key = str(key or "").strip()
    value = _truncate_value(str(value or "").strip())
    if category not in _SEMANTIC_CATEGORIES or not key or not value:
        return
    if not _semantic_safe(category, key, value):
        return
    current = _semantic_latest().get((category, key))
    if current and str(current.get("value", "") or "").strip() == value:
        return
    updated = datetime.now().strftime("%Y-%m-%d")
    _append_brain_record(
        SEMANTIC_PATH,
        {
            "type": "semantic_fact",
            "op": "upsert",
            "category": category,
            "key": key,
            "value": value,
            "source": source,
            "updated": updated,
        },
    )
    _index_record(
        f"semantic:{category}:{key}",
        kind="semantic",
        category=category,
        key=key,
        value=value,
        source=source,
        updated=updated,
    )


def _archive_semantic_delete(category: str, key: str) -> None:
    category = str(category or "").strip()
    key = str(key or "").strip()
    if category not in _SEMANTIC_CATEGORIES or not key:
        return
    _append_brain_record(
        SEMANTIC_PATH,
        {
            "type": "semantic_fact",
            "op": "delete",
            "category": category,
            "key": key,
            "updated": datetime.now().strftime("%Y-%m-%d"),
        },
    )
    _delete_index_record(f"semantic:{category}:{key}")



def _learning_text_safe(text: str) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    if _SECRET_KEY_RE.search(value):
        return False
    if "BEGIN PRIVATE KEY" in value or "BEGIN OPENSSH PRIVATE KEY" in value:
        return False
    if value.strip().startswith("sk-") or "Bearer " in value:
        return False
    return True


def _queue_learning_candidate(text: str, *, source: str, language: str = "", kind: str = "session_summary") -> None:
    text = str(text or "").strip()
    if not _learning_text_safe(text):
        return
    _append_brain_record(
        LEARNING_INBOX_PATH,
        {
            "type": "learning_candidate",
            "kind": kind,
            "status": "pending_validation",
            "source": source,
            "language": str(language or "").strip(),
            "text": text,
        },
    )



def _candidate_id(item: dict) -> str:
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _processed_learning_ids() -> set[str]:
    out: set[str] = set()
    for path in (LEARNING_VALIDATED_PATH, LEARNING_REJECTED_PATH):
        for item in _read_brain_records(path):
            cid = str(item.get("candidate_id", "") or "").strip()
            if cid:
                out.add(cid)
    return out


def _extract_explicit_memory_fact(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    user_part = value
    if "User:" in value:
        user_part = value.split("User:", 1)[1].split("\nMIA:", 1)[0].strip()
    patterns = [
        r"(?is)\b(?:merk(?:e)?\s+dir|speicher(?:e)?|behalte(?:\s+dir)?|remember(?:\s+that)?)\s*(?:bitte\s*)?[:,-]?\s+(.{3,400})$",
        r"(?is)\b(?:das\s+sollst\s+du\s+dir\s+merken)\s*[:,-]?\s+(.{3,400})$",
    ]
    for pattern in patterns:
        m = re.search(pattern, user_part)
        if m:
            fact = re.sub(r"\s+", " ", m.group(1)).strip(" .")
            if _learning_text_safe(fact):
                return fact[:380]
    return ""


def process_learning_inbox() -> dict:
    """Validate pending learning candidates without inventing facts.

    Explicit user memory instructions become semantic notes. All other safe
    conversation candidates stay episodic-only. Secret-like content is rejected.
    """
    processed = _processed_learning_ids()
    stats = {"promoted": 0, "episodic_only": 0, "rejected": 0, "skipped": 0}

    for item in _read_brain_records(LEARNING_INBOX_PATH):
        cid = _candidate_id(item)
        if cid in processed:
            stats["skipped"] += 1
            continue
        text = str(item.get("text", "") or "").strip()
        base = {
            "candidate_id": cid,
            "source": str(item.get("source", "") or ""),
            "kind": str(item.get("kind", "") or ""),
            "processed_at": datetime.now().isoformat(timespec="seconds"),
        }
        if not _learning_text_safe(text):
            _append_brain_record(LEARNING_REJECTED_PATH, {**base, "status": "rejected_secret_risk"})
            stats["rejected"] += 1
            processed.add(cid)
            continue

        fact = _extract_explicit_memory_fact(text)
        if fact:
            key = "explicit_" + hashlib.sha256(fact.lower().encode("utf-8")).hexdigest()[:12]
            _archive_semantic_fact("notes", key, fact, source=base["source"] or "learning_inbox")
            _append_brain_record(
                LEARNING_VALIDATED_PATH,
                {**base, "status": "promoted_semantic", "category": "notes", "key": key, "value": fact},
            )
            stats["promoted"] += 1
        else:
            _append_brain_record(
                LEARNING_VALIDATED_PATH,
                {**base, "status": "episodic_only"},
            )
            stats["episodic_only"] += 1
        processed.add(cid)
    return stats



def _index_record(record_id: str, *, kind: str, category: str, key: str,
                  value: str, source: str = "", updated: str = "") -> None:
    try:
        from memory.retrieval_index import upsert_record
        upsert_record(
            RETRIEVAL_DB_PATH,
            record_id,
            kind=kind,
            category=category,
            key=key,
            value=value,
            source=source,
            updated=updated,
        )
    except Exception as exc:
        print(f"[Memory] retrieval index write failed: {exc}")


def _delete_index_record(record_id: str) -> None:
    try:
        from memory.retrieval_index import delete_record
        delete_record(RETRIEVAL_DB_PATH, record_id)
    except Exception as exc:
        print(f"[Memory] retrieval index delete failed: {exc}")


def rebuild_retrieval_index() -> dict:
    try:
        from memory.retrieval_index import reset, upsert_record
        reset(RETRIEVAL_DB_PATH)
        semantic_count = 0
        episodic_count = 0
        for (cat, key), item in _semantic_latest().items():
            val = str(item.get("value", "") or "").strip()
            if not val:
                continue
            upsert_record(
                RETRIEVAL_DB_PATH,
                f"semantic:{cat}:{key}",
                kind="semantic",
                category=cat,
                key=key,
                value=val,
                source=str(item.get("source", "") or ""),
                updated=str(item.get("updated", "") or ""),
            )
            semantic_count += 1
        for item in _read_brain_records(EPISODIC_PATH):
            summary = str(item.get("summary", "") or "").strip()
            if not summary:
                continue
            rid = str(item.get("record_id", "") or "").strip()
            if not rid:
                raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
                rid = "episodic:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
            upsert_record(
                RETRIEVAL_DB_PATH,
                rid,
                kind="episodic",
                category="episodic",
                key=str(item.get("date", "") or "session"),
                value=summary,
                source=str(item.get("type", "") or "episodic"),
                updated=str(item.get("date", "") or ""),
            )
            episodic_count += 1
        return {"semantic": semantic_count, "episodic": episodic_count}
    except Exception as exc:
        print(f"[Memory] retrieval index rebuild failed: {exc}")
        return {"semantic": 0, "episodic": 0}


def _search_retrieval_index(query: str, limit: int = 16) -> list[dict]:
    try:
        from memory.retrieval_index import search
        return search(RETRIEVAL_DB_PATH, query, limit=limit)
    except Exception:
        return []


def _semantic_latest() -> dict[tuple[str, str], dict]:
    latest: dict[tuple[str, str], dict] = {}
    for item in _read_brain_records(SEMANTIC_PATH):
        cat = str(item.get("category", "") or "").strip()
        key = str(item.get("key", "") or "").strip()
        if not cat or not key:
            continue
        marker = (cat, key)
        if str(item.get("op", "upsert")) == "delete":
            latest.pop(marker, None)
            continue
        value = str(item.get("value", "") or "").strip()
        if value:
            latest[marker] = item
    return latest


def _iter_update_leaves(obj, prefix: str = ""):
    if isinstance(obj, dict) and "value" in obj:
        yield prefix, obj.get("value")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            yield from _iter_update_leaves(v, child)
        return
    if prefix:
        yield prefix, obj

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            print(f"[Memory] Load error: {e}")
            return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    dropped = []
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        dropped.append(f"{cat}/{key}")
        print(f"[Memory] Trimmed {cat}/{key}")
    if dropped and _trim_notifier:
        try:
            _trim_notifier(
                f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})"
            )
        except Exception:
            pass
    return memory

def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def _mirror_updates_async(memory_update: dict) -> None:
    """Best-effort background mirror of just-changed facts to the optional remote
    memory service (core/knowledge_client.py). Off unless "knowledge_host" is
    configured; runs in a background thread so a slow/unreachable server can
    never delay save_memory()."""
    try:
        from core.knowledge_client import is_enabled, is_semantic_enabled, memory_upsert, semantic_memory_upsert
    except Exception:
        return
    if not is_enabled():
        return
    semantic_on = is_semantic_enabled()

    def _do():
        for cat, items in memory_update.items():
            if not isinstance(items, dict):
                continue
            for key, entry in items.items():
                if isinstance(entry, dict) and "value" not in entry:
                    continue  # nested sub-category — not used by any current caller
                value = entry.get("value") if isinstance(entry, dict) else entry
                if not value or (isinstance(value, str) and not value.strip()):
                    continue
                item_id = f"{cat}:{key}"
                memory_upsert(
                    item_id, str(value), actor="user",
                    created_at=datetime.now().strftime("%Y-%m-%d"),
                )
                if semantic_on:
                    semantic_memory_upsert(item_id, str(value), {"category": cat, "key": key})

    threading.Thread(target=_do, daemon=True).start()


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        for category, payload in memory_update.items():
            if category not in _SEMANTIC_CATEGORIES:
                continue
            for key, value in _iter_update_leaves(payload):
                if value is not None and str(value).strip():
                    _archive_semantic_fact(category, key, value, source="update_memory")
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
        _mirror_updates_async(memory_update)
    return memory

def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


# ── Legacy identity guard ────────────────────────────────────────────────────
# The assistant used to be called JARVIS. Facts and session summaries written
# back then can still sit in long_term.json ("assistant name: Jarvis", "Jarvis
# helped with …"). Nothing is deleted from disk — the memory panel still lists
# every entry so the user decides — but such entries are never fed back to the
# model, so stored history cannot re-assert the old identity over MIA.
_LEGACY_IDENTITY_RE = re.compile(r"\bj\.?\s?a\.?\s?r\.?\s?v\.?\s?i\.?\s?s\b\.?", re.IGNORECASE)


def mentions_legacy_identity(*texts) -> bool:
    return any(_LEGACY_IDENTITY_RE.search(str(t or "")) for t in texts)


def scrub_legacy_identity(text: str) -> str:
    """Replace the legacy assistant name with MIA in free text (display/prompt only)."""
    return _LEGACY_IDENTITY_RE.sub("MIA", text or "")


# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    This used to dump everything. It now sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS. Recency is the cheapest useful
         relevance signal available without embeddings.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Net effect on latency: this block is SMALLER than the old full dump, so
    every session connects with fewer tokens. Occasionally the model spends one
    extra round trip on recall_memory - covered by the acknowledgment it
    already speaks before any slow step."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val or mentions_legacy_identity(val):
            continue
        if field == "language":
            # Labelled as an observation, not a setting. A bare "Language:
            # English" line written months ago reads like a standing order and
            # was one of the reasons a Turkish question came back in English.
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val and not mentions_legacy_identity(key, val):
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val or mentions_legacy_identity(key, val):
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    # Add durable semantic-only keys to the prompt index so MIA knows
    # older facts exist and can call recall_memory for them.
    local_pairs = set()
    for local_cat, local_items in memory.items():
        if isinstance(local_items, dict):
            for local_key in local_items:
                local_pairs.add((local_cat, local_key))
    for (sem_cat, sem_key), _item in _semantic_latest().items():
        if (sem_cat, sem_key) in local_pairs:
            continue
        name = _pretty(sem_key)
        if name and name not in indexed:
            indexed.append(name)

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance. No embeddings, no network, no model call - this
    runs in well under a millisecond, which is the entire point: recall must
    cost one model round trip, never two."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def _search_memory_semantic_fallback(query: str, limit: int) -> str:
    """Tried only when the instant keyword search above finds nothing at all —
    this is the associative-memory path (embeddings + Qdrant, see
    core/knowledge_client.py). It costs a local embedding call, never an LLM
    call, and is a no-op when the vector service isn't configured."""
    try:
        from core.knowledge_client import is_semantic_enabled, semantic_memory_search
    except Exception:
        return ""
    if not is_semantic_enabled():
        return ""
    hits = semantic_memory_search(query, limit=limit)
    if not hits:
        return ""
    lines = [
        f"{h.get('category', '?')}/{_pretty(h.get('key', h.get('source_id', '')))}: {h.get('text', '')}"
        for h in hits
    ]
    return f"Nothing matched '{query}' by keyword, but this seems related:\n" + "\n".join(lines)


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    memory = load_memory()
    words  = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue                     # skip 'sessions', which is a list
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val or mentions_legacy_identity(key, val):
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    # Fast local FTS5 recall across durable semantic and episodic memory.
    for item in _search_retrieval_index(query, limit=max(limit * 2, 12)):
        cat = str(item.get("category", "") or item.get("kind", "") or "memory")
        key = str(item.get("key", "") or item.get("record_id", "") or "record")
        val = str(item.get("value", "") or "").strip()
        if not val:
            continue
        s = _score(words, cat, key, val) if words else 1
        if s > 0:
            rows.append((s + 2, cat, key, val))

    # Search durable semantic Brain memory too.
    for (cat, key), item in _semantic_latest().items():
        val = str(item.get("value", "") or "").strip()
        if not val:
            continue
        s = _score(words, cat, key, val) if words else 1
        if s > 0:
            rows.append((s, cat, key, val))

    # Search durable episodic Brain memory too.
    # This keeps old sessions recallable even after pop_last_session()
    # removes them from the fast long_term.json session queue.
    for item in _read_brain_records(EPISODIC_PATH):
        summary = str(item.get("summary", "") or "").strip()
        if not summary:
            continue

        date = str(item.get("date", "") or "unknown")
        key = f"session_{date}"
        cat = "episodic"

        s = _score(words, cat, key, summary) if words else 1
        if s > 0:
            rows.append((s, cat, key, summary))

    # Avoid exact duplicate results.
    deduped = []
    seen = set()

    for row in rows:
        marker = (row[1], row[2], row[3])
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(row)

    rows = deduped

    if not rows:
        if query:
            semantic = _search_memory_semantic_fallback(query, limit)
            if semantic:
                return semantic
            return f"Nothing stored about '{query}'."
        return "I have not stored anything about this person yet."

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what MIA knows, and when it learned it.
    Sorted newest first so the panel opens on what changed most recently."""
    memory = load_memory()
    rows = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            rows.append({
                "category": cat,
                "key":      key,
                "value":    val,
                "updated":  (entry.get("updated", "") if isinstance(entry, dict) else ""),
            })
    existing = {(r["category"], r["key"]) for r in rows}
    for (cat, key), item in _semantic_latest().items():
        if (cat, key) in existing:
            continue
        val = str(item.get("value", "") or "").strip()
        if not val:
            continue
        rows.append({
            "category": cat,
            "key": key,
            "value": val,
            "updated": str(item.get("updated", "") or ""),
        })
    rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
    return rows

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def _mirror_delete_async(item_id: str) -> None:
    try:
        from core.knowledge_client import is_enabled, is_semantic_enabled, memory_delete, semantic_memory_delete
    except Exception:
        return
    if not is_enabled():
        return
    semantic_on = is_semantic_enabled()

    def _do():
        memory_delete([item_id])
        if semantic_on:
            semantic_memory_delete(item_id)

    threading.Thread(target=_do, daemon=True).start()


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        _mirror_delete_async(f"{category}:{key}")
        _archive_semantic_delete(category, key)
        return f"Forgotten: {category}/{key}"
    if (category, key) in _semantic_latest():
        _archive_semantic_delete(category, key)
        _mirror_delete_async(f"{category}:{key}")
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget


# ── Session memory ─────────────────────────────────────────────────────────────

_SESSION_MAX = 3   # safety cap — in practice 0-1 entries after pop



def record_conversation_turn(user_text: str, assistant_text: str, language: str = "") -> None:
    """Persist one completed user/assistant turn as episodic memory and a learning candidate."""
    user_text = str(user_text or "").strip()
    assistant_text = str(assistant_text or "").strip()
    if not user_text and not assistant_text:
        return

    combined = f"User: {user_text}\nMIA: {assistant_text}".strip()
    if not _learning_text_safe(combined):
        return

    summary = combined[:1200]
    now = datetime.now()
    rid = "turn:" + hashlib.sha256((user_text + "\n" + assistant_text + now.isoformat()).encode("utf-8")).hexdigest()[:20]
    _append_brain_record(
        EPISODIC_PATH,
        {
            "record_id": rid,
            "type": "conversation_turn",
            "date": now.strftime("%Y-%m-%d"),
            "language": str(language or "").strip(),
            "user": user_text[:600],
            "assistant": assistant_text[:600],
            "summary": summary,
        },
    )
    _index_record(
        rid,
        kind="episodic",
        category="episodic",
        key=now.strftime("%Y-%m-%d"),
        value=summary,
        source="conversation_turn",
        updated=now.strftime("%Y-%m-%d"),
    )
    _queue_learning_candidate(
        summary,
        source="conversation_turn",
        language=language,
        kind="conversation_turn",
    )
    process_learning_inbox()


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a 1-2 sentence session summary to long_term.json['sessions']."""
    summary = (summary or "").strip()
    if not summary:
        return
    memory   = load_memory()
    sessions = memory.get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    entry: dict = {
        "date":    datetime.now().strftime("%Y-%m-%d"),
        "summary": summary[:280],
    }
    if language:
        entry["language"] = language
    sessions.append(entry)

    # Durable episodic memory:
    # long_term.json remains the fast session core while the Brain keeps
    # the complete history, even after pop_last_session() consumes it.
    _append_brain_record(
        EPISODIC_PATH,
        {
            "type": "session",
            "date": entry.get("date", ""),
            "language": entry.get("language", ""),
            "summary": entry.get("summary", ""),
        },
    )

    # Every finished session also enters the learning inbox. It is only a
    # candidate here; promotion to durable semantic knowledge requires
    # validation, matching the Brain learning policy.
    _queue_learning_candidate(
        entry.get("summary", ""),
        source="session_summary",
        language=entry.get("language", ""),
        kind="session_summary",
    )
    process_learning_inbox()

    memory["sessions"] = sessions[-_SESSION_MAX:]
    with _lock:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    print(f"[Memory] 📝 Session saved ({entry['date']}): {summary[:60]}…")


def recent_sessions_for_ui() -> list[dict]:
    """Read-only view of stored session summaries, newest first.

    Unlike pop_last_session(), this does not consume entries — it backs the
    memory panel, which must be able to show what's there without erasing the
    greeting flow's own copy."""
    memory   = load_memory()
    sessions = memory.get("sessions", [])
    if not isinstance(sessions, list):
        return []
    return [
        {**s, "summary": scrub_legacy_identity(s.get("summary", ""))}
        if isinstance(s, dict) else s
        for s in reversed(sessions)
    ]


def pop_last_session() -> dict | None:
    """
    Return AND remove the most recent session entry.
    Calling this consumes the entry so it is never repeated in future briefings.
    """
    with _lock:
        if not MEMORY_PATH.exists():
            return None
        try:
            memory   = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            sessions = memory.get("sessions", [])
            if not isinstance(sessions, list) or not sessions:
                return None
            entry = sessions.pop()          # remove the last entry
            memory["sessions"] = sessions
            MEMORY_PATH.write_text(
                json.dumps(memory, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if isinstance(entry, dict) and "summary" in entry:
                entry = {**entry, "summary": scrub_legacy_identity(entry["summary"])}
            return entry
        except Exception as e:
            print(f"[Memory] pop_last_session error: {e}")
            return None