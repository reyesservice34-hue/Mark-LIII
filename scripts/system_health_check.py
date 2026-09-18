#!/usr/bin/env python3
"""
Complete System Health Check & Audit
Verifies all components, dependencies, and integrations before JARVIS launch
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple


class SystemHealthCheck:
    """Comprehensive system audit before production."""

    def __init__(self, project_root: str = "/home/user/Mark-LIII"):
        self.project_root = Path(project_root)
        self.report = {
            "timestamp": datetime.now().isoformat(),
            "project_root": str(self.project_root),
            "checks": [],
            "summary": {
                "total_checks": 0,
                "passed": 0,
                "failed": 0,
                "warnings": 0,
                "critical_issues": []
            }
        }

    def add_check(self, category: str, name: str, status: str, details: str = "") -> None:
        """Record a check result."""
        self.report["checks"].append({
            "category": category,
            "name": name,
            "status": status,  # "PASS", "FAIL", "WARN"
            "details": details
        })

        self.report["summary"]["total_checks"] += 1
        if status == "PASS":
            self.report["summary"]["passed"] += 1
        elif status == "FAIL":
            self.report["summary"]["failed"] += 1
            self.report["summary"]["critical_issues"].append(f"{category}/{name}")
        elif status == "WARN":
            self.report["summary"]["warnings"] += 1

    def run_all_checks(self) -> Dict:
        """Execute all health checks."""
        print("\n" + "="*70)
        print("🏥 SYSTEM HEALTH CHECK & AUDIT")
        print("="*70 + "\n")

        # 1. File Structure
        self._check_file_structure()
        print()

        # 2. Python Dependencies
        self._check_dependencies()
        print()

        # 3. Environment Configuration
        self._check_environment()
        print()

        # 4. Script Quality
        self._check_scripts()
        print()

        # 5. External Services
        self._check_external_services()
        print()

        # 6. Data Integrity
        self._check_data_integrity()
        print()

        # 7. Git Status
        self._check_git_status()
        print()

        # 8. Memory Systems
        self._check_memory_systems()
        print()

        # 9. Integration Tests
        self._check_integrations()
        print()

        return self.report

    def _check_file_structure(self) -> None:
        """Verify critical files exist."""
        print("📁 File Structure Check")
        print("-" * 70)

        critical_files = [
            "scripts/autonomous_complete.sh",
            "scripts/autonomous_openai_executor.py",
            "scripts/autonomous_memory_manager.py",
            "scripts/init_qdrant_collections.py",
            "scripts/deploy_n8n_workflows.py",
            "scripts/generate_qdrant_workflow.py",
            ".env",
            ".claude/long_term_memory.md",
            ".claude/session_learning.json",
            ".claude/session_instructions_memory.json",
            "GATES.md",
            "n8n_analysis_report.json",
            "qdrant_workflows_config.json"
        ]

        for file_path in critical_files:
            full_path = self.project_root / file_path
            status = "PASS" if full_path.exists() else "FAIL"
            self.add_check("File Structure", file_path, status)
            print(f"  {'✅' if status == 'PASS' else '❌'} {file_path}")

    def _check_dependencies(self) -> None:
        """Verify Python dependencies are installed."""
        print("\n📦 Python Dependencies Check")
        print("-" * 70)

        dependencies = [
            ("requests", "HTTP client"),
            ("openai", "OpenAI API"),
            ("pathlib", "Path handling"),
            ("json", "JSON parsing")
        ]

        for package, description in dependencies:
            try:
                __import__(package)
                status = "PASS"
                self.add_check("Dependencies", f"{package} ({description})", status)
                print(f"  ✅ {package}: {description}")
            except ImportError:
                status = "FAIL"
                self.add_check("Dependencies", f"{package} ({description})", status, "Not installed")
                print(f"  ❌ {package}: {description} - NOT INSTALLED")

    def _check_environment(self) -> None:
        """Verify environment configuration."""
        print("\n🔧 Environment Configuration Check")
        print("-" * 70)

        required_env_vars = [
            ("OPENAI_API_KEY", "OpenAI API authentication"),
            ("N8N_API_KEY", "n8n API authentication")
        ]

        for var_name, description in required_env_vars:
            value = os.getenv(var_name)
            if value:
                status = "PASS"
                print(f"  ✅ {var_name}: Set (length: {len(value)})")
            else:
                status = "FAIL"
                print(f"  ❌ {var_name}: NOT SET")

            self.add_check("Environment", var_name, status, description)

    def _check_scripts(self) -> None:
        """Verify script quality and executability."""
        print("\n⚙️  Script Quality Check")
        print("-" * 70)

        scripts = [
            "scripts/autonomous_complete.sh",
            "scripts/autonomous_openai_executor.py",
            "scripts/init_qdrant_collections.py",
            "scripts/deploy_n8n_workflows.py"
        ]

        for script_path in scripts:
            full_path = self.project_root / script_path

            if not full_path.exists():
                self.add_check("Scripts", script_path, "FAIL", "File not found")
                print(f"  ❌ {script_path}: NOT FOUND")
                continue

            # Check if executable
            is_exec = os.access(full_path, os.X_OK)

            # Check for syntax errors (Python only)
            syntax_ok = True
            if script_path.endswith(".py"):
                try:
                    with open(full_path) as f:
                        compile(f.read(), script_path, 'exec')
                except SyntaxError as e:
                    syntax_ok = False
                    print(f"  ❌ {script_path}: SYNTAX ERROR - {e}")

            status = "PASS" if is_exec and syntax_ok else "FAIL"
            self.add_check("Scripts", script_path, status)
            print(f"  {'✅' if status == 'PASS' else '❌'} {script_path}")

    def _check_external_services(self) -> None:
        """Verify connectivity to external services."""
        print("\n🌐 External Services Check")
        print("-" * 70)

        import requests

        # Qdrant
        try:
            resp = requests.get("http://172.17.0.1:6333/health", timeout=5)
            status = "PASS" if resp.status_code == 200 else "WARN"
            self.add_check("Services", "Qdrant (172.17.0.1:6333)", status)
            print(f"  {'✅' if status == 'PASS' else '⚠️'} Qdrant: {resp.status_code}")
        except Exception as e:
            self.add_check("Services", "Qdrant (172.17.0.1:6333)", "FAIL", str(e))
            print(f"  ❌ Qdrant: {e}")

        # n8n
        try:
            headers = {"X-N8N-API-KEY": os.getenv("N8N_API_KEY", "")}
            resp = requests.get("http://localhost:3000/api/v1/workflows", headers=headers, timeout=5)
            status = "PASS" if resp.status_code == 200 else "WARN"
            self.add_check("Services", "n8n (localhost:3000)", status)
            print(f"  {'✅' if status == 'PASS' else '⚠️'} n8n: {resp.status_code}")
        except Exception as e:
            self.add_check("Services", "n8n (localhost:3000)", "FAIL", str(e))
            print(f"  ❌ n8n: {e}")

        # OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self.add_check("Services", "OpenAI API Key", "PASS")
            print(f"  ✅ OpenAI API Key: Configured")
        else:
            self.add_check("Services", "OpenAI API Key", "FAIL")
            print(f"  ❌ OpenAI API Key: NOT CONFIGURED")

    def _check_data_integrity(self) -> None:
        """Verify data files are valid and complete."""
        print("\n📊 Data Integrity Check")
        print("-" * 70)

        data_files = {
            "n8n_analysis_report.json": "n8n Analysis",
            "qdrant_workflows_config.json": "Qdrant Configuration",
            ".claude/session_learning.json": "Session Learning",
            "GATES.md": "Completion Gates"
        }

        for file_path, description in data_files.items():
            full_path = self.project_root / file_path

            if not full_path.exists():
                self.add_check("Data", description, "FAIL", "File not found")
                print(f"  ❌ {description}: NOT FOUND")
                continue

            try:
                if file_path.endswith(".json"):
                    with open(full_path) as f:
                        json.load(f)

                # File exists and is valid
                file_size = full_path.stat().st_size
                self.add_check("Data", description, "PASS", f"Size: {file_size} bytes")
                print(f"  ✅ {description}: Valid ({file_size} bytes)")

            except json.JSONDecodeError as e:
                self.add_check("Data", description, "FAIL", f"Invalid JSON: {e}")
                print(f"  ❌ {description}: INVALID JSON")
            except Exception as e:
                self.add_check("Data", description, "FAIL", str(e))
                print(f"  ❌ {description}: {e}")

    def _check_git_status(self) -> None:
        """Verify git repository is clean and up-to-date."""
        print("\n📚 Git Status Check")
        print("-" * 70)

        try:
            # Check git status
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5
            )

            uncommitted = result.stdout.strip().split("\n") if result.stdout.strip() else []
            uncommitted = [line for line in uncommitted if line]

            if not uncommitted or (len(uncommitted) == 1 and not uncommitted[0]):
                self.add_check("Git", "Uncommitted Changes", "PASS")
                print(f"  ✅ No uncommitted changes")
            else:
                self.add_check("Git", "Uncommitted Changes", "WARN", f"{len(uncommitted)} files")
                print(f"  ⚠️  {len(uncommitted)} uncommitted files")

            # Check latest commit
            result = subprocess.run(
                ["git", "log", "-1", "--oneline"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                commit = result.stdout.strip()
                self.add_check("Git", "Latest Commit", "PASS", commit)
                print(f"  ✅ Latest commit: {commit}")

        except Exception as e:
            self.add_check("Git", "Status Check", "WARN", str(e))
            print(f"  ⚠️  Git check error: {e}")

    def _check_memory_systems(self) -> None:
        """Verify memory and learning systems are functional."""
        print("\n🧠 Memory Systems Check")
        print("-" * 70)

        memory_files = [
            (".claude/long_term_memory.md", "Long-term Memory"),
            (".claude/session_learning.json", "Session Learning"),
            (".claude/session_instructions_memory.json", "Instructions Memory")
        ]

        for file_path, description in memory_files:
            full_path = self.project_root / file_path

            if full_path.exists():
                file_size = full_path.stat().st_size
                self.add_check("Memory", description, "PASS", f"Size: {file_size} bytes")
                print(f"  ✅ {description}: Active ({file_size} bytes)")
            else:
                self.add_check("Memory", description, "FAIL", "File not found")
                print(f"  ❌ {description}: NOT FOUND")

    def _check_integrations(self) -> None:
        """Test critical integrations."""
        print("\n🔗 Integration Tests")
        print("-" * 70)

        # Test 1: Memory Manager loads
        try:
            sys.path.insert(0, str(self.project_root))
            from scripts.autonomous_memory_manager import AutonomousMemoryManager

            manager = AutonomousMemoryManager(str(self.project_root))
            patterns = manager.learnings.get("patterns_learned", {})

            if patterns:
                self.add_check("Integration", "Memory Manager", "PASS", "Learnings loaded")
                print(f"  ✅ Memory Manager: Functional (loaded {len(patterns)} pattern categories)")
            else:
                self.add_check("Integration", "Memory Manager", "WARN", "No patterns loaded")
                print(f"  ⚠️  Memory Manager: No patterns loaded yet")

        except Exception as e:
            self.add_check("Integration", "Memory Manager", "FAIL", str(e))
            print(f"  ❌ Memory Manager: {e}")

        # Test 2: Analysis report valid
        try:
            analysis_file = self.project_root / "n8n_analysis_report.json"
            if analysis_file.exists():
                with open(analysis_file) as f:
                    analysis = json.load(f)

                workflows = len(analysis.get("workflows", []))
                qdrant_wfs = len(analysis.get("qdrant_workflows", []))

                self.add_check("Integration", "n8n Analysis", "PASS", f"{workflows} workflows")
                print(f"  ✅ n8n Analysis: {workflows} workflows, {qdrant_wfs} Qdrant-suitable")
            else:
                self.add_check("Integration", "n8n Analysis", "WARN", "Report not found")
                print(f"  ⚠️  n8n Analysis: Report not yet generated")

        except Exception as e:
            self.add_check("Integration", "n8n Analysis", "FAIL", str(e))
            print(f"  ❌ n8n Analysis: {e}")

    def print_summary(self) -> None:
        """Print audit summary."""
        summary = self.report["summary"]

        print("\n" + "="*70)
        print("📋 AUDIT SUMMARY")
        print("="*70)
        print(f"""
Total Checks: {summary['total_checks']}
✅ Passed:    {summary['passed']}
❌ Failed:    {summary['failed']}
⚠️  Warnings:  {summary['warnings']}

Health Score: {int(summary['passed'] / max(summary['total_checks'], 1) * 100)}%
""")

        if summary['critical_issues']:
            print("🚨 CRITICAL ISSUES:")
            for issue in summary['critical_issues']:
                print(f"   - {issue}")
            print()

        readiness = "🟢 READY" if summary['failed'] == 0 else "🟡 READY WITH WARNINGS" if summary['failed'] < 3 else "🔴 NOT READY"
        print(f"Status: {readiness}\n")

    def save_report(self) -> None:
        """Save audit report to file."""
        report_file = self.project_root / "system_audit_report.json"
        with open(report_file, "w") as f:
            json.dump(self.report, f, indent=2)

        print(f"📄 Full report saved: {report_file}\n")


def main():
    """Run complete system audit."""
    audit = SystemHealthCheck()
    audit.run_all_checks()
    audit.print_summary()
    audit.save_report()

    # Exit with appropriate code
    if audit.report["summary"]["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
