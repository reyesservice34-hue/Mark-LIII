import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\info\MIA")
sys.path.insert(0, str(PROJECT_ROOT))

import memory.memory_manager as mm

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    mm.MEMORY_PATH = base / "long_term.json"
    mm.EPISODIC_PATH = base / "brain" / "memory" / "episodic" / "sessions.jsonl"
    marker = "MIA_EPISODIC_TEST_27092026"
    mm.save_session_summary(f"Testsession mit Marker {marker}", "de-DE")
    before = mm.search_memory(marker)
    popped = mm.pop_last_session()
    after = mm.search_memory(marker)
    archive_exists = mm.EPISODIC_PATH.exists()
    archive_text = mm.EPISODIC_PATH.read_text(encoding="utf-8") if archive_exists else ""
    assert marker in before, "Marker vor pop nicht gefunden"
    assert popped is not None, "Session konnte nicht gepoppt werden"
    assert marker in after, "Marker nach pop nicht mehr auffindbar"
    assert marker in archive_text, "Marker fehlt im episodischen Archiv"
    print("MIA EPISODIC MEMORY TEST: PASS")
    print("Recall before pop:", marker in before)
    print("Recall after pop:", marker in after)
    print("Archive exists:", archive_exists)
