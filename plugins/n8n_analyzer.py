"""
Autonomous n8n Infrastructure Analyzer Plugin

Analyzes n8n workflows, identifies Qdrant-related integrations,
and generates configuration recommendations without external API calls.
"""

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


class N8nAnalyzer:
    def __init__(self, base_url: str = "http://localhost:3000"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = 10

    def test_connection(self) -> bool:
        """Test connection to n8n"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/health")
            return response.status_code == 200
        except Exception:
            return False

    def get_workflows(self) -> List[Dict]:
        """Get all workflows"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/workflows")
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
        except Exception:
            pass
        return []

    def get_nodes(self) -> List[Dict]:
        """Get available node types"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/node-types")
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return []

    def get_credentials(self) -> List[Dict]:
        """Get all credentials"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/credentials")
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
        except Exception:
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
            analyzer.test_connection()
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
