import json

from actions import agent_manager as am


def test_create_agent_writes_shared_roster(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "BASE_DIR", tmp_path)
    monkeypatch.setattr(am, "ROSTER_PATH", tmp_path / "config" / "command_center" / "agents.json")
    monkeypatch.setattr(am, "BACKUP_DIR", tmp_path / "data" / "agent-roster-backups")
    monkeypatch.setattr(am, "_available_mia_tools", lambda: {"web_search", "file_processor"})

    result = am.manage_agents({
        "operation": "create",
        "id": "market_research",
        "name": "Marktforschung",
        "role": "Prüft Märkte und Quellen.",
        "instructions": "Recherchiere, belege Quellen und erfinde keine Fakten.",
        "capabilities": ["research"],
        "tools": ["web_search", "file_processor", "does_not_exist"],
    })

    assert "[AGENT_CREATED]" in result
    data = json.loads(am.ROSTER_PATH.read_text(encoding="utf-8"))
    agent = data["agents"][0]
    assert agent["name"] == "market_research"
    assert agent["display_name"] == "Marktforschung"
    assert agent["mia_tools"] == ["web_search", "file_processor"]
    assert agent["tools"] == ["web.search", "web.fetch", "filesystem.read", "filesystem.list"]
    assert ["coordinator", "market_research"] in data["flows"]


def test_update_agent_keeps_identity_and_changes_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "BASE_DIR", tmp_path)
    monkeypatch.setattr(am, "ROSTER_PATH", tmp_path / "config" / "command_center" / "agents.json")
    monkeypatch.setattr(am, "BACKUP_DIR", tmp_path / "data" / "agent-roster-backups")
    monkeypatch.setattr(am, "_available_mia_tools", lambda: {"web_search", "file_processor"})

    am.manage_agents({
        "operation": "create", "id": "analyst", "name": "Analyst",
        "role": "Analysiert.", "instructions": "Arbeite nachvollziehbar.",
        "tools": ["file_processor"],
    })
    result = am.manage_agents({
        "operation": "update", "id": "analyst",
        "instructions": "Arbeite nachvollziehbar und belege Quellen.",
        "tools": ["web_search"],
    })

    assert "[AGENT_UPDATED]" in result
    data = json.loads(am.ROSTER_PATH.read_text(encoding="utf-8"))
    assert data["agents"][0]["instructions"].endswith("belege Quellen.")
    assert data["agents"][0]["mia_tools"] == ["web_search"]
