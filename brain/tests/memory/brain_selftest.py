from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory.memory_manager as mm


def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        mm.MEMORY_PATH = base / 'long_term.json'
        mm.EPISODIC_PATH = base / 'brain' / 'memory' / 'episodic' / 'sessions.jsonl'
        mm.SEMANTIC_PATH = base / 'brain' / 'memory' / 'semantic' / 'archive.jsonl'
        mm.LEARNING_INBOX_PATH = base / 'brain' / 'ingestion' / 'inbox' / 'candidates.jsonl'
        mm.LEARNING_VALIDATED_PATH = base / 'brain' / 'ingestion' / 'validated' / 'processed.jsonl'
        mm.LEARNING_REJECTED_PATH = base / 'brain' / 'ingestion' / 'rejected' / 'rejected.jsonl'
        mm.RETRIEVAL_DB_PATH = base / 'brain' / 'indexes' / 'memory_fts.sqlite3'

        episodic_marker = 'MIA_SELFTEST_EPISODIC'
        semantic_marker = 'MIA_SELFTEST_SEMANTIC'

        mm.save_session_summary(episodic_marker, 'de-DE')
        before_pop = episodic_marker in mm.search_memory(episodic_marker)
        popped = mm.pop_last_session()
        after_pop = episodic_marker in mm.search_memory(episodic_marker)

        mm.record_conversation_turn(
            'Merke dir bitte: ' + semantic_marker,
            'Verstanden.',
            'de-DE',
        )
        semantic_recall = semantic_marker in mm.search_memory(semantic_marker)
        validation_log = mm.LEARNING_VALIDATED_PATH.read_text(encoding='utf-8')
        promoted = 'promoted_semantic' in validation_log

        before = mm.LEARNING_INBOX_PATH.read_text(encoding='utf-8')
        mm._queue_learning_candidate('password=supersecret', source='selftest')
        after = mm.LEARNING_INBOX_PATH.read_text(encoding='utf-8')
        secret_blocked = before == after

        index_stats = mm.rebuild_retrieval_index()
        indexed_recall = semantic_marker in mm.search_memory(semantic_marker)

        result = {
            'episodic_before_pop': before_pop,
            'episodic_after_pop': after_pop,
            'pop_returned': popped is not None,
            'semantic_recall': semantic_recall,
            'autolearn_promoted': promoted,
            'secret_blocked': secret_blocked,
            'fts_semantic_count': index_stats.get('semantic', 0),
            'fts_episodic_count': index_stats.get('episodic', 0),
            'indexed_recall': indexed_recall,
        }
        result['ok'] = all([
            before_pop, after_pop, popped is not None, semantic_recall,
            promoted, secret_blocked, indexed_recall,
            result['fts_semantic_count'] >= 1,
            result['fts_episodic_count'] >= 1,
        ])
        return result


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
