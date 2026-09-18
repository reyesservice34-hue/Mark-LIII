#!/usr/bin/env python3
"""
Proactive n8n Workflow Deployer
Autonomously imports and activates generated workflows
"""

import os
import sys
import json
import requests
from pathlib import Path
from typing import Dict, List, Optional


class N8nWorkflowDeployer:
    """Deploy workflows to n8n autonomously."""

    def __init__(self, n8n_url: str = "http://localhost:3000", api_key: str = None):
        self.n8n_url = n8n_url.rstrip("/")
        self.api_key = api_key or os.getenv("N8N_API_KEY")

        if not self.api_key:
            raise ValueError("N8N_API_KEY not found")

        self.headers = {
            "Content-Type": "application/json",
            "X-N8N-API-KEY": self.api_key
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def health_check(self) -> bool:
        """Check if n8n is running and authenticated."""
        try:
            resp = self.session.get(f"{self.n8n_url}/api/v1/workflows", timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(f"❌ n8n health check failed: {e}")
            return False

    def workflow_exists(self, workflow_name: str) -> Optional[str]:
        """Check if workflow already exists, return ID if so."""
        try:
            resp = self.session.get(f"{self.n8n_url}/api/v1/workflows", timeout=10)
            if resp.status_code == 200:
                workflows = resp.json().get("data", [])
                for wf in workflows:
                    if wf.get("name") == workflow_name:
                        return wf.get("id")
            return None
        except Exception:
            return None

    def create_workflow(self, workflow_json: Dict) -> Optional[str]:
        """Create a new workflow in n8n."""
        try:
            # Prepare payload
            payload = {
                "name": workflow_json.get("name", "Unnamed Workflow"),
                "nodes": workflow_json.get("nodes", []),
                "connections": workflow_json.get("connections", {}),
                "settings": workflow_json.get("settings", {}),
                "active": False  # Always start disabled
            }

            resp = self.session.post(
                f"{self.n8n_url}/api/v1/workflows",
                json=payload,
                timeout=30
            )

            if resp.status_code in [200, 201]:
                result = resp.json()
                workflow_id = result.get("id")
                print(f"✅ Workflow '{payload['name']}' created (ID: {workflow_id})")
                return workflow_id
            else:
                print(f"❌ Failed to create workflow: {resp.status_code}")
                print(f"   {resp.text[:200]}")
                return None

        except Exception as e:
            print(f"❌ Error creating workflow: {e}")
            return None

    def activate_workflow(self, workflow_id: str, activate: bool = True) -> bool:
        """Activate or deactivate a workflow."""
        try:
            payload = {"active": activate}
            resp = self.session.patch(
                f"{self.n8n_url}/api/v1/workflows/{workflow_id}",
                json=payload,
                timeout=10
            )

            if resp.status_code in [200, 201]:
                status = "activated" if activate else "deactivated"
                print(f"✅ Workflow {status}: {workflow_id}")
                return True
            else:
                print(f"❌ Failed to activate workflow {workflow_id}: {resp.status_code}")
                return False

        except Exception as e:
            print(f"❌ Error activating workflow: {e}")
            return False

    def deploy_workflow_file(self, workflow_file: Path, activate: bool = False) -> Optional[str]:
        """Deploy a workflow from JSON file."""
        if not workflow_file.exists():
            print(f"❌ Workflow file not found: {workflow_file}")
            return None

        try:
            with open(workflow_file) as f:
                workflow_json = json.load(f)

            workflow_name = workflow_json.get("name", workflow_file.stem)

            # Check if already exists
            existing_id = self.workflow_exists(workflow_name)
            if existing_id:
                print(f"ℹ️  Workflow '{workflow_name}' already exists (ID: {existing_id})")
                return existing_id

            # Create new workflow
            workflow_id = self.create_workflow(workflow_json)

            if workflow_id and activate:
                self.activate_workflow(workflow_id, activate=True)

            return workflow_id

        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in {workflow_file}: {e}")
            return None
        except Exception as e:
            print(f"❌ Error deploying workflow: {e}")
            return None

    def deploy_all_workflows(self) -> Dict:
        """Deploy all generated workflows."""
        results = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "n8n_url": self.n8n_url,
            "health": False,
            "workflows_deployed": [],
            "workflows_failed": []
        }

        print("\n" + "="*60)
        print("🚀 N8N WORKFLOW DEPLOYMENT")
        print("="*60 + "\n")

        # Health check
        print(f"🔍 Checking n8n at {self.n8n_url}...")
        if not self.health_check():
            print("❌ n8n is not reachable!")
            results["health"] = False
            return results

        print("✅ n8n is healthy\n")
        results["health"] = True

        # Find workflow files
        workflow_dir = Path(".")
        workflow_files = list(workflow_dir.glob("qdrant_*_workflow.json"))

        if not workflow_files:
            print("⚠️  No workflow files found")
            return results

        print(f"📋 Found {len(workflow_files)} workflows to deploy:\n")

        # Deploy each workflow
        for wf_file in workflow_files:
            print(f"📦 Deploying {wf_file.name}...")

            # For semantic workflows, activate them
            should_activate = "semantic" in wf_file.name.lower()

            workflow_id = self.deploy_workflow_file(wf_file, activate=should_activate)

            if workflow_id:
                results["workflows_deployed"].append({
                    "file": wf_file.name,
                    "id": workflow_id,
                    "active": should_activate
                })
            else:
                results["workflows_failed"].append(wf_file.name)

            print()

        # Summary
        print("="*60)
        print(f"✨ DEPLOYMENT COMPLETE")
        print(f"   Deployed: {len(results['workflows_deployed'])}")
        print(f"   Failed: {len(results['workflows_failed'])}")
        print("="*60 + "\n")

        return results


def main():
    """Run deployment autonomously."""
    try:
        deployer = N8nWorkflowDeployer(
            n8n_url=os.getenv("N8N_URL", "http://localhost:3000")
        )

        results = deployer.deploy_all_workflows()

        # Save results
        output_file = Path("n8n_deployment_results.json")
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)

        print(f"📄 Results saved: {output_file}")

        # Summary
        if results["health"] and len(results["workflows_deployed"]) > 0:
            print("✅ Workflows deployed and ready!")
            return 0
        else:
            print("❌ Deployment incomplete")
            return 1

    except Exception as e:
        print(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
