#!/usr/bin/env python3
"""
Autonomer Workflow-Aktivierer: Identifiziert und aktiviert Qdrant-geeignete Workflows.
Basiert auf n8n_analysis_report.json Ergebnissen.
"""

import json
import requests
import sys
from pathlib import Path
from typing import Dict, List


class WorkflowActivator:
    """Aktiviert automatisch Qdrant-geeignete Workflows in n8n."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "X-N8N-API-KEY": api_key,
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def load_analysis(self, report_file: str = "n8n_analysis_report.json") -> Dict:
        """Lade Analyse-Report."""
        try:
            with open(report_file) as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"❌ {report_file} nicht gefunden")
            return {}

    def activate_workflow(self, workflow_id: str, name: str) -> bool:
        """Aktiviere einen Workflow."""
        try:
            response = self.session.patch(
                f"{self.base_url}/api/v1/workflows/{workflow_id}",
                json={"active": True},
                timeout=30
            )
            if response.status_code in [200, 201]:
                print(f"✅ Workflow aktiviert: {name} (ID: {workflow_id})")
                return True
            else:
                print(f"❌ Workflow-Aktivierung fehlgeschlagen: {name}")
                print(f"   Status: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Fehler beim Aktivieren von {name}: {e}")
            return False

    def run(self):
        """Führe autonome Workflow-Aktivierung aus."""
        print("\n" + "="*60)
        print("🤖 AUTONOME WORKFLOW-AKTIVIERUNG")
        print("="*60 + "\n")

        # Lade Analyse
        analysis = self.load_analysis()
        if not analysis:
            print("❌ Keine Analyse-Daten verfügbar")
            return

        qdrant_workflows = analysis.get("qdrant_workflows", [])
        if not qdrant_workflows:
            print("ℹ️  Keine Qdrant-geeigneten Workflows identifiziert")
            return

        print(f"📋 Gefundene Qdrant-Workflows: {len(qdrant_workflows)}\n")

        activated = 0
        for wf in qdrant_workflows:
            wf_id = wf.get("id")
            wf_name = wf.get("name")
            is_active = wf.get("active", False)

            if is_active:
                print(f"ℹ️  Bereits aktiv: {wf_name} (ID: {wf_id})")
            else:
                if self.activate_workflow(wf_id, wf_name):
                    activated += 1

        print(f"\n" + "="*60)
        print(f"✨ {activated} Workflows aktiviert")
        print("="*60 + "\n")

        return {
            "total_qdrant_workflows": len(qdrant_workflows),
            "activated": activated,
            "already_active": len(qdrant_workflows) - activated
        }


def main():
    import os
    import argparse

    parser = argparse.ArgumentParser(
        description="Autonomer Workflow-Aktivierer für Qdrant-Integration"
    )
    parser.add_argument(
        "--n8n-url",
        default="http://localhost:3000",
        help="n8n URL (default: http://localhost:3000)"
    )
    parser.add_argument(
        "--api-key",
        help="n8n API Key (oder N8N_API_KEY Env-Variable)"
    )

    args = parser.parse_args()
    api_key = args.api_key or os.getenv("N8N_API_KEY")

    if not api_key:
        print("❌ Kein API-Key gefunden")
        sys.exit(1)

    activator = WorkflowActivator(args.n8n_url, api_key)
    result = activator.run()

    # Speichere Ergebnis
    with open("workflow_activation_result.json", "w") as f:
        json.dump(result or {}, f, indent=2)


if __name__ == "__main__":
    main()
