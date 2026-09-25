from core.knowledge_client import is_enabled, knowledge_get, knowledge_list


def knowledge_lookup(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """Reads reference documents from the optional remote knowledge base
    (mia-knowledge-service — see knowledge_services/docker-compose.yml).
    Off unless "knowledge_host" is configured in config/api_keys.json."""
    if not is_enabled():
        return "The knowledge base is not configured, sir."

    p    = parameters or {}
    name = p.get("name", "").strip()

    if not name:
        documents = knowledge_list()
        if not documents:
            return "The knowledge base is empty or unreachable, sir."
        return "Available knowledge documents:\n" + "\n".join(f"- {d}" for d in documents)

    doc = knowledge_get(name)
    if doc is None:
        return f"Could not find or read '{name}' in the knowledge base, sir."
    return str(doc)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "knowledge_lookup",
    "description": (
        "Reads a reference document from the shared knowledge base (business info, "
        "manuals, procedures uploaded outside this conversation). "
        "Call with no name to list what's available, or with a name to read one."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {
                "type": "STRING",
                "description": "Document filename to read (e.g. 'faq.json'). Leave empty to list all documents.",
            },
        },
        "required": [],
    },
    "handler": knowledge_lookup,
}
