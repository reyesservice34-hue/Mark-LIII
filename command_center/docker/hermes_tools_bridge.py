"""Hermes-Werkzeuge als MCP-Server (streamable HTTP) im Command-Center-Container.

Ersetzt den eigenen Container `mia-hermes-tools`: derselbe Werkzeugumfang läuft
jetzt im selben Container wie das Command Center. Port 8765, Pfad /mcp, nur intern.
"""
import os

os.environ.setdefault("HERMES_QUIET", "1")
os.environ.setdefault("HERMES_REDACT_SECRETS", "true")

import agent.transports.hermes_tools_mcp_server as bridge  # noqa: E402

EXTRA = (
    "tool_search", "tool_describe", "tool_call", "execute_code", "skill_manage",
    "terminal", "read_file", "write_file", "patch", "search_files", "browser_exec",
)
bridge.EXPOSED_TOOLS = tuple(dict.fromkeys((*bridge.EXPOSED_TOOLS, *EXTRA)))

server = bridge._build_server()
server.run(
    transport="streamable-http",
    host="127.0.0.1",
    port=int(os.environ.get("HERMES_BRIDGE_PORT", "8765")),
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=False,
)
