#!/usr/bin/env python3
"""
Auto-Upload Results Script

Monitors for n8n_status.json, commits it to GitHub, and notifies Claude.

Usage:
    python3 scripts/auto_upload_results.py
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime


class ResultUploader:
    def __init__(self):
        self.repo_dir = Path.cwd()
        self.status_file = self.repo_dir / "n8n_status.json"
        self.max_wait_time = 600  # 10 minutes
        self.check_interval = 5  # Check every 5 seconds

    def wait_for_status_file(self) -> bool:
        """Wait for n8n_status.json to be generated."""
        print("⏳ Waiting for n8n_status.json...")

        start_time = time.time()
        while time.time() - start_time < self.max_wait_time:
            if self.status_file.exists():
                print(f"✅ Found n8n_status.json")
                return True

            time.sleep(self.check_interval)
            elapsed = int(time.time() - start_time)
            print(f"   [{elapsed}s] Still waiting...")

        print(f"❌ Timeout: n8n_status.json not found after {self.max_wait_time}s")
        return False

    def read_status(self) -> dict:
        """Read the status file."""
        try:
            with open(self.status_file) as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error reading status file: {e}")
            return {}

    def commit_and_push(self) -> bool:
        """Commit the status file to GitHub."""
        try:
            # Add file
            result = subprocess.run(
                ["git", "add", "n8n_status.json"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                print(f"❌ Git add failed: {result.stderr}")
                return False

            # Commit
            timestamp = datetime.now().isoformat()
            commit_message = f"""Add n8n infrastructure status report

Generated: {timestamp}

Report contains:
- All available node types and connectors
- Workflow inventory (active/inactive)
- Qdrant credential configuration
- Identified Qdrant-related workflows
- Configuration recommendations

Auto-uploaded by auto_upload_results.py
"""

            result = subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                print(f"❌ Git commit failed: {result.stderr}")
                return False

            print(f"✅ Committed n8n_status.json")

            # Push
            result = subprocess.run(
                ["git", "push", "-u", "origin", "claude/session-01a0ae45-continuation-lmlahz"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                print(f"❌ Git push failed: {result.stderr}")
                return False

            print(f"✅ Pushed to GitHub")
            return True

        except subprocess.TimeoutExpired:
            print(f"❌ Git operation timeout")
            return False
        except Exception as e:
            print(f"❌ Error during commit/push: {e}")
            return False

    def generate_summary(self, status: dict) -> str:
        """Generate a summary of the status."""
        if not status:
            return "No status data available"

        summary = f"""
n8n INFRASTRUCTURE STATUS REPORT
{'='*60}

🔗 Connection: {status.get('n8n_url', 'N/A')}

📦 Connectors/Nodes: {status.get('nodes_count', 'N/A')}

📋 Workflows:
   - Total: {status.get('workflows_total', 'N/A')}
   - Active: {status.get('workflows_active', 'N/A')}
   - Inactive: {status.get('workflows_inactive', 'N/A')}

Inactive Workflows:
"""

        for wf in status.get('inactive_workflows', []):
            summary += f"   - {wf.get('name')} (ID: {wf.get('id')})\n"

        summary += f"""
🔑 Credentials: {status.get('credentials_count', 'N/A')}

⚙️  Qdrant Credential:
   - Status: {'Created' if status.get('qdrant_credential') else 'Not found'}
   - ID: {status.get('qdrant_credential', {}).get('id', 'N/A')}

🔍 Qdrant-Related Workflows: {len(status.get('qdrant_workflows', []))}

{'='*60}
Report saved to: n8n_status.json
Uploaded to: GitHub (claude/session-01a0ae45-continuation-lmlahz)
"""
        return summary

    def run(self):
        """Main execution flow."""
        print("🚀 n8n Results Auto-Upload Service")
        print("="*60)

        # Wait for status file
        if not self.wait_for_status_file():
            sys.exit(1)

        # Read status
        status = self.read_status()
        if not status:
            print("❌ Could not read status data")
            sys.exit(1)

        # Print summary
        summary = self.generate_summary(status)
        print(summary)

        # Commit and push
        if not self.commit_and_push():
            print("❌ Could not upload results")
            sys.exit(1)

        print("\n" + "="*60)
        print("✨ RESULTS UPLOADED TO GITHUB")
        print("="*60)
        print("\n📍 Claude is checking GitHub for your results...")
        print("   Give him a moment to fetch and analyze...\n")


if __name__ == "__main__":
    try:
        uploader = ResultUploader()
        uploader.run()
    except KeyboardInterrupt:
        print("\n⏹️  Upload service stopped")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
