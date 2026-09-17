#!/usr/bin/env python3
"""
n8n Connector Handler & Autonomous Workflow Configuration

Connects to n8n, loads all available connectors/nodes, lists workflows,
identifies inactive ones, and configures Qdrant credential.

Usage:
    python3 scripts/n8n_connector_handler.py --n8n-url http://localhost:3000 --api-key YOUR_KEY
"""

import requests
import json
import sys
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass


@dataclass
class N8nConnector:
    name: str
    type: str
    description: str
    enabled: bool = True


@dataclass
class N8nWorkflow:
    id: str
    name: str
    active: bool
    nodes: List[Dict]
    connections: Dict


class N8nConnectorHandler:
    """Handle n8n connectors, credentials, and workflows."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "X-N8N-API-KEY": api_key,
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def test_connection(self) -> bool:
        """Test connection to n8n server."""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/health", timeout=10)
            if response.status_code == 200:
                print(f"✅ Connected to n8n: {self.base_url}")
                return True
            else:
                print(f"❌ n8n health check failed: {response.status_code}")
                return False
        except requests.exceptions.ConnectionError:
            print(f"❌ Cannot connect to n8n at {self.base_url}")
            return False
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return False

    def get_available_nodes(self) -> List[Dict]:
        """Get all available node types from n8n."""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/node-types", timeout=30)
            if response.status_code == 200:
                nodes = response.json()
                print(f"✅ Loaded {len(nodes)} available node types")
                return nodes
            else:
                print(f"❌ Failed to load node types: {response.status_code}")
                return []
        except Exception as e:
            print(f"❌ Error loading node types: {e}")
            return []

    def list_workflows(self) -> List[N8nWorkflow]:
        """List all workflows in n8n."""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/workflows", timeout=30)
            if response.status_code == 200:
                workflows_data = response.json()
                workflows = []

                for wf in workflows_data.get("data", []):
                    workflow = N8nWorkflow(
                        id=wf.get("id"),
                        name=wf.get("name"),
                        active=wf.get("active", False),
                        nodes=wf.get("nodes", []),
                        connections=wf.get("connections", {})
                    )
                    workflows.append(workflow)

                print(f"✅ Found {len(workflows)} workflows")
                return workflows
            else:
                print(f"❌ Failed to list workflows: {response.status_code}")
                return []
        except Exception as e:
            print(f"❌ Error listing workflows: {e}")
            return []

    def get_workflow_details(self, workflow_id: str) -> Optional[Dict]:
        """Get detailed information about a workflow."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/workflows/{workflow_id}",
                timeout=30
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Failed to get workflow {workflow_id}: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Error getting workflow details: {e}")
            return None

    def list_credentials(self) -> List[Dict]:
        """List all credentials in n8n."""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/credentials", timeout=30)
            if response.status_code == 200:
                credentials = response.json().get("data", [])
                print(f"✅ Found {len(credentials)} credentials")
                return credentials
            else:
                print(f"❌ Failed to list credentials: {response.status_code}")
                return []
        except Exception as e:
            print(f"❌ Error listing credentials: {e}")
            return []

    def create_qdrant_credential(self) -> Optional[Dict]:
        """Create Qdrant credential if it doesn't exist."""
        try:
            # Check if Qdrant credential already exists
            credentials = self.list_credentials()
            for cred in credentials:
                if cred.get("name") == "Qdrant Local":
                    print(f"ℹ️  Qdrant credential already exists (ID: {cred.get('id')})")
                    return cred

            # Create new Qdrant credential
            credential_data = {
                "name": "Qdrant Local",
                "type": "qdrantApi",
                "data": {
                    "host": "http://localhost",
                    "port": 6333,
                    "apiKey": "",
                }
            }

            response = self.session.post(
                f"{self.base_url}/api/v1/credentials",
                json=credential_data,
                timeout=30
            )

            if response.status_code in [200, 201]:
                result = response.json()
                print(f"✅ Qdrant credential created (ID: {result.get('id')})")
                return result
            else:
                print(f"❌ Failed to create credential: {response.status_code}")
                print(f"   Response: {response.text}")
                return None
        except Exception as e:
            print(f"❌ Error creating Qdrant credential: {e}")
            return None

    def identify_qdrant_workflows(self, workflows: List[N8nWorkflow]) -> List[Dict]:
        """Identify workflows that use or should use Qdrant."""
        qdrant_workflows = []

        for wf in workflows:
            # Check if workflow mentions vector, embeddings, or qdrant
            workflow_text = json.dumps(wf.__dict__).lower()

            if any(keyword in workflow_text for keyword in ["vector", "qdrant", "embedding", "semantic"]):
                details = self.get_workflow_details(wf.id)
                qdrant_workflows.append({
                    "id": wf.id,
                    "name": wf.name,
                    "active": wf.active,
                    "details": details
                })

        print(f"✅ Found {len(qdrant_workflows)} Qdrant-related workflows")
        return qdrant_workflows

    def summarize_status(self) -> Dict:
        """Generate comprehensive status summary."""
        print("\n" + "="*60)
        print("🔍 n8n CONNECTOR & WORKFLOW ANALYSIS")
        print("="*60 + "\n")

        # Test connection
        if not self.test_connection():
            return {"status": "error", "message": "Cannot connect to n8n"}

        # Get nodes
        nodes = self.get_available_nodes()
        print(f"\n📦 Available Connector Types:")
        print(f"   Total: {len(nodes)}")

        # Get workflows
        workflows = self.list_workflows()
        active_count = sum(1 for w in workflows if w.active)
        inactive_count = len(workflows) - active_count

        print(f"\n📋 Workflows:")
        print(f"   Total: {len(workflows)}")
        print(f"   Active: {active_count}")
        print(f"   Inactive: {inactive_count}")

        if workflows:
            print(f"\n   Inactive Workflows:")
            for wf in workflows:
                if not wf.active:
                    print(f"   - {wf.name} (ID: {wf.id})")

        # List credentials
        credentials = self.list_credentials()
        print(f"\n🔑 Credentials:")
        print(f"   Total: {len(credentials)}")
        for cred in credentials[:5]:  # Show first 5
            print(f"   - {cred.get('name')} ({cred.get('type')})")

        # Create Qdrant credential
        print(f"\n⚙️  Qdrant Configuration:")
        qdrant_cred = self.create_qdrant_credential()

        # Identify Qdrant workflows
        qdrant_workflows = self.identify_qdrant_workflows(workflows)

        print("\n" + "="*60)
        print(f"✨ STATUS: Ready for workflow configuration")
        print("="*60 + "\n")

        return {
            "status": "success",
            "n8n_url": self.base_url,
            "nodes_count": len(nodes),
            "workflows_total": len(workflows),
            "workflows_active": active_count,
            "workflows_inactive": inactive_count,
            "inactive_workflows": [{"id": w.id, "name": w.name} for w in workflows if not w.active],
            "credentials_count": len(credentials),
            "qdrant_credential": qdrant_cred,
            "qdrant_workflows": qdrant_workflows,
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="n8n Connector Handler & Autonomous Workflow Configuration"
    )
    parser.add_argument(
        "--n8n-url",
        default="http://localhost:3000",
        help="n8n server URL (default: http://localhost:3000)"
    )
    parser.add_argument(
        "--api-key",
        help="n8n API Key (or set N8N_API_KEY env var)"
    )

    args = parser.parse_args()

    # Load API key
    import os
    api_key = args.api_key or os.getenv("N8N_API_KEY")

    if not api_key:
        print("❌ No API Key provided.")
        print("   Use: --api-key YOUR_KEY")
        print("   Or:  export N8N_API_KEY=YOUR_KEY")
        sys.exit(1)

    # Initialize handler and run analysis
    handler = N8nConnectorHandler(args.n8n_url, api_key)
    result = handler.summarize_status()

    # Save result to file
    result_file = Path("n8n_status.json")
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"📄 Status saved to: {result_file}")


if __name__ == "__main__":
    main()
