"""Tests für actions/knowledge_lookup.py."""
import pytest

from actions import knowledge_lookup as kl


def test_returns_not_configured_message_when_disabled(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: False)
    assert kl.knowledge_lookup({}) == "The knowledge base is not configured, sir."


def test_lists_documents_when_no_name_given(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: True)
    monkeypatch.setattr(kl, "knowledge_list", lambda: ["faq.json", "prices.json"])
    result = kl.knowledge_lookup({})
    assert "faq.json" in result and "prices.json" in result
    assert result.startswith("Available knowledge documents:")


def test_name_that_is_only_whitespace_is_treated_as_no_name(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: True)
    monkeypatch.setattr(kl, "knowledge_list", lambda: ["a.json"])
    result = kl.knowledge_lookup({"name": "   "})
    assert "a.json" in result


def test_empty_document_list_reports_empty_or_unreachable(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: True)
    monkeypatch.setattr(kl, "knowledge_list", lambda: [])
    assert kl.knowledge_lookup({}) == "The knowledge base is empty or unreachable, sir."


def test_returns_document_content_as_string_when_found(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: True)
    monkeypatch.setattr(kl, "knowledge_get", lambda name: {"titel": "FAQ", "text": "Antwort"})
    result = kl.knowledge_lookup({"name": "faq.json"})
    assert result == str({"titel": "FAQ", "text": "Antwort"})


def test_returns_not_found_message_when_document_missing(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: True)
    monkeypatch.setattr(kl, "knowledge_get", lambda name: None)
    result = kl.knowledge_lookup({"name": "does-not-exist.json"})
    assert "Could not find or read 'does-not-exist.json'" in result


def test_none_parameters_defaults_to_empty_dict(monkeypatch):
    monkeypatch.setattr(kl, "is_enabled", lambda: True)
    monkeypatch.setattr(kl, "knowledge_list", lambda: [])
    assert kl.knowledge_lookup(None) == "The knowledge base is empty or unreachable, sir."


def test_tool_declaration_shape():
    assert kl.TOOL["name"] == "knowledge_lookup"
    assert kl.TOOL["handler"] is kl.knowledge_lookup
    assert kl.TOOL["parameters"]["type"] == "OBJECT"
