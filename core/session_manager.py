"""
Session Management — Track and learn from chat sessions.

Each session maintains:
- Session ID and metadata
- Conversation history
- Topics discussed
- Outcomes and learnings
- Timestamp and duration
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any


BASE_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = BASE_DIR / "sessions"


def _ensure_sessions_dir() -> Path:
    """Create sessions directory if it doesn't exist."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


class SessionManager:
    """Manage chat sessions and extract learnings."""

    def __init__(self):
        self.sessions_dir = _ensure_sessions_dir()
        self.current_session: Optional[Dict[str, Any]] = None
        self.session_history: List[Dict[str, str]] = []

    def start_session(self, session_id: str) -> None:
        """Start a new session."""
        self.current_session = {
            "id": session_id,
            "start_time": datetime.now().isoformat(),
            "messages": [],
            "topics": [],
            "searches_performed": [],
        }
        self.session_history = []

    def add_message(
        self, speaker: str, text: str, search_results: Optional[List[str]] = None
    ) -> None:
        """Add a message to the current session."""
        if not self.current_session:
            return

        message = {
            "speaker": speaker,
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }

        if search_results:
            message["search_results"] = search_results

        self.current_session["messages"].append(message)
        self.session_history.append({"speaker": speaker, "text": text})

    def add_search(self, query: str, results: List[Dict[str, str]]) -> None:
        """Record a web search performed during session."""
        if not self.current_session:
            return

        self.current_session["searches_performed"].append(
            {"query": query, "results_count": len(results), "timestamp": time.time()}
        )

    def extract_topics(self) -> List[str]:
        """Extract main topics from session messages."""
        if not self.current_session or not self.current_session["messages"]:
            return []

        topics = []
        all_text = " ".join([m["text"] for m in self.current_session["messages"]])

        # Simple keyword extraction
        keywords = [
            "python",
            "javascript",
            "web",
            "database",
            "api",
            "machine learning",
            "ai",
            "bug",
            "feature",
            "design",
            "architecture",
            "performance",
            "security",
            "testing",
        ]

        for keyword in keywords:
            if keyword.lower() in all_text.lower():
                topics.append(keyword)

        return list(set(topics))

    def end_session(self) -> Dict[str, Any]:
        """End the current session and return summary."""
        if not self.current_session:
            return {}

        session = self.current_session
        session["end_time"] = datetime.now().isoformat()
        session["topics"] = self.extract_topics()
        session["message_count"] = len(session["messages"])

        # Save to file
        session_file = (
            self.sessions_dir / f"{session['id']}.json"
        )
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[SessionManager] Failed to save session: {e}")

        self.current_session = None
        self.session_history = []

        return session

    def get_session_summary(self) -> str:
        """Get a summary of the current session for learning."""
        if not self.current_session:
            return ""

        topics = self.extract_topics()
        msg_count = len(self.current_session["messages"])
        search_count = len(self.current_session["searches_performed"])

        summary = f"Session summary: {msg_count} messages"
        if topics:
            summary += f", topics: {', '.join(topics)}"
        if search_count:
            summary += f", {search_count} web searches performed"

        return summary

    def load_recent_sessions(self, count: int = 5) -> List[Dict[str, Any]]:
        """Load recent session summaries for context."""
        sessions = []
        try:
            session_files = sorted(
                self.sessions_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:count]

            for session_file in session_files:
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        session_data = json.load(f)
                        sessions.append(
                            {
                                "id": session_data.get("id"),
                                "topics": session_data.get("topics", []),
                                "message_count": session_data.get("message_count", 0),
                            }
                        )
                except Exception:
                    continue
        except Exception:
            pass

        return sessions


# Global session manager instance
_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    return _session_manager
