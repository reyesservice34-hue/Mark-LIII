"""
Autonomous n8n Infrastructure Analyzer Plugin

Analyzes n8n workflows, identifies Qdrant-related integrations,
and generates configuration recommendations without external API calls.
"""

import os
import requests
import json
from pathlib import Path
from typing import Optional, Dict, List

PLUGIN = {
    "name": "n8n_analyzer",
    "description": (
        "Analyzes local n8n infrastructure: lists workflows, identifies inactive ones, "
        "detects Qdrant integration opportunities, and generates autonomous setup recommendations. "
        "Say 'analyze n8n' or 'check n8n workflows' to trigger."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "What to do: 'analyze' (full analysis), 'list' (workflows only), 'qdrant' (Qdrant opportunities), 'status' (infrastructure status)"
            },
            "n8n_url": {
                "type": "STRING",
                "description": "n8n server URL (default: http://localhost:3000)"
            },
        },
        "required": ["action"],
    },
}


def _load_api_key() -> str:
    """Read N8N_API_KEY from the environment or a local .env file."""
    key = os.getenv("N8N_API_KEY")
    if key:
        return key

    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        try:
            for line in candidate.read_text().splitlines():
                name, sep, value = line.partition("=")
                if sep and name.strip() == "N8N_API_KEY":
                    return value.strip().strip("'\"")
        except OSError:
            continue
    return ""


class N8nAnalyzer:
    TIMEOUT = 10

    def __init__(self, base_url: str = "http://localhost:3000", api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or _load_api_key()
        self.last_error = ""
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["X-N8N-API-KEY"] = self.api_key

    def _get(self, path: str):
        return self.session.get(f"{self.base_url}{path}", timeout=self.TIMEOUT)

    def test_connection(self) -> bool:
        """Test connection to n8n. /healthz needs no auth, /rest/settings is the fallback."""
        for path in ("/healthz", "/rest/settings"):
            try:
                if self._get(path).status_code == 200:
                    return True
            except requests.RequestException as e:
                self.last_error = str(e)
        return False

    def get_workflows(self) -> List[Dict]:
        """Get all workflows"""
        try:
            response = self._get("/api/v1/workflows")
            if response.status_code == 200:
                return response.json().get("data", [])
            self.last_error = f"workflows: HTTP {response.status_code}"
        except requests.RequestException as e:
            self.last_error = str(e)
        return []

    def get_nodes(self) -> List[Dict]:
        """Get available node types"""
        try:
            response = self._get("/types/nodes.json")
            if response.status_code == 200:
                return response.json()
        except (requests.RequestException, ValueError):
            pass
        return []

    def get_credentials(self) -> List[Dict]:
        """Get all credentials"""
        try:
            response = self._get("/api/v1/credentials")
            if response.status_code == 200:
                return response.json().get("data", [])
        except requests.RequestException:
            pass
        return []

    def analyze_all(self) -> Dict:
        """Complete infrastructure analysis"""
        result = {
            "connected": self.test_connection(),
            "workflows": [],
            "nodes_total": 0,
            "credentials_total": 0,
            "qdrant_workflows": [],
            "inactive_count": 0,
            "recommendations": []
        }

        if not result["connected"]:
            result["error"] = f"Cannot connect to {self.base_url}"
            if self.last_error:
                result["error"] += f" ({self.last_error})"
            return result

        # Get data
        workflows = self.get_workflows()
        nodes = self.get_nodes()
        credentials = self.get_credentials()

        result["nodes_total"] = len(nodes)
        result["credentials_total"] = len(credentials)

        # Analyze workflows
        for wf in workflows:
            wf_info = {
                "id": wf.get("id"),
                "name": wf.get("name"),
                "active": wf.get("active", False),
                "nodes": len(wf.get("nodes", [])),
            }
            result["workflows"].append(wf_info)

            if not wf.get("active"):
                result["inactive_count"] += 1

            # Check for Qdrant keywords
            wf_text = json.dumps(wf).lower()
            if any(kw in wf_text for kw in ["qdrant", "vector", "embedding", "semantic"]):
                result["qdrant_workflows"].append(wf_info)

        # Generate recommendations
        result["recommendations"] = self._generate_recommendations(result)

        return result

    def _generate_recommendations(self, analysis: Dict) -> List[str]:
        """Generate autonomous recommendations"""
        recs = []

        if analysis["inactive_count"] > 0:
            recs.append(f"🔴 {analysis['inactive_count']} inactive workflows found - evaluate for activation")

        if analysis["qdrant_workflows"]:
            recs.append(f"✅ {len(analysis['qdrant_workflows'])} workflows suitable for Qdrant integration identified")
            recs.append("→ Recommended action: Configure Qdrant credential and activate these workflows")

        if analysis["nodes_total"] > 100:
            recs.append("✅ Rich node library available - wide integration possibilities")

        if analysis["credentials_total"] < 5:
            recs.append("⚠️  Few credentials configured - consider adding more integrations")

        if not any("qdrant" in w.get("name", "").lower() for w in analysis.get("workflows", [])):
            recs.append("🚀 No Qdrant-specific workflows found - opportunity to create semantic search workflows")

        return recs


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Execute n8n analysis"""
    action = parameters.get("action", "analyze")
    n8n_url = parameters.get("n8n_url", "http://localhost:3000")

    try:
        analyzer = N8nAnalyzer(n8n_url)

        if action == "analyze":
            result = analyzer.analyze_all()

            if result.get("error"):
                return f"⚠️  {result['error']}"

            # Format output
            output = f"""
📊 n8n INFRASTRUCTURE ANALYSIS
{'='*50}

🔗 Connection: ✅ Connected to {n8n_url}

📋 Workflows:
   Total: {len(result['workflows'])}
   Active: {len(result['workflows']) - result['inactive_count']}
   Inactive: {result['inactive_count']}

📦 Available Nodes: {result['nodes_total']}
🔑 Configured Credentials: {result['credentials_total']}

🎯 Qdrant Integration Opportunities:
   Suitable workflows: {len(result['qdrant_workflows'])}
"""
            if result["qdrant_workflows"]:
                output += "\n   Workflows:\n"
                for wf in result["qdrant_workflows"]:
                    output += f"   • {wf['name']} (ID: {wf['id']}, Nodes: {wf['nodes']})\n"

            output += f"\n💡 Recommendations:\n"
            for rec in result["recommendations"]:
                output += f"   {rec}\n"

            # Save report
            report_path = Path("n8n_analysis_report.json")
            with open(report_path, "w") as f:
                json.dump(result, f, indent=2)

            output += f"\n✅ Full report saved to: {report_path}"

            if player:
                try:
                    player.write_log(f"JARVIS: n8n Analysis Complete - {len(result['workflows'])} workflows analyzed")
                except Exception:
                    pass

            return output

        elif action == "status":
            if analyzer.test_connection():
                workflows = len(analyzer.get_workflows())
                nodes = len(analyzer.get_nodes())
                return f"✅ n8n is running: {workflows} workflows, {nodes} node types available"
            else:
                return f"❌ Cannot reach n8n at {n8n_url}"

        else:
            return f"Unknown action: {action}. Use 'analyze', 'list', 'qdrant', or 'status'"

    except Exception as e:
        error_msg = f"n8n_analyzer failed: {str(e)}"
        if player:
            try:
                player.write_log(f"JARVIS: {error_msg}")
            except Exception:
                pass
        return error_msg


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Autonomous n8n infrastructure analyzer")
    parser.add_argument("--action", default="analyze", choices=["analyze", "status"])
    parser.add_argument("--n8n-url", default="http://localhost:3000")
    args = parser.parse_args()

    print(run({"action": args.action, "n8n_url": args.n8n_url}))
