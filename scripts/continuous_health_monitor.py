#!/usr/bin/env python3
"""
Continuous Health Monitor for Mark-LIII JARVIS System
Monitors all services and provides real-time status dashboard
"""

import os
import json
import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import threading

# Color codes
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'
    CLEAR = '\033[2J'
    HOME = '\033[H'

class HealthMonitor:
    """Continuous health monitoring for JARVIS system."""

    def __init__(self, update_interval: int = 10):
        self.update_interval = update_interval
        self.project_root = Path(__file__).parent.parent
        self.health_log = self.project_root / "system_health.json"
        self.monitoring = False

        self.services = {
            "JARVIS Coordinator": {
                "url": "http://127.0.0.1:8000/health",
                "port": 8000,
                "status": "UNKNOWN",
                "last_check": None,
                "response_time": 0
            },
            "WhatsApp Gateway": {
                "url": "http://127.0.0.1:5000/health",
                "port": 5000,
                "status": "UNKNOWN",
                "last_check": None,
                "response_time": 0
            },
            "n8n": {
                "url": "http://localhost:3000/healthz",
                "port": 3000,
                "status": "UNKNOWN",
                "last_check": None,
                "response_time": 0
            },
            "Qdrant Cloud": {
                "env_vars": ["QDRANT_CLOUD_URL", "QDRANT_API_KEY"],
                "status": "UNKNOWN",
                "last_check": None,
                "response_time": 0
            }
        }

        self.metrics = {
            "uptime_checks": 0,
            "failures": 0,
            "last_failure": None,
            "start_time": datetime.now().isoformat()
        }

    def check_service(self, service_name: str) -> bool:
        """Check if a service is healthy."""
        try:
            import requests

            service = self.services[service_name]

            if "url" not in service:
                return self._check_qdrant_credentials()

            start_time = time.time()
            response = requests.get(service["url"], timeout=5)
            response_time = time.time() - start_time

            service["response_time"] = response_time
            service["last_check"] = datetime.now().isoformat()

            if response.status_code == 200:
                service["status"] = "UP"
                return True
            else:
                service["status"] = "ERROR"
                return False

        except Exception as e:
            self.services[service_name]["status"] = "DOWN"
            self.services[service_name]["last_check"] = datetime.now().isoformat()
            return False

    def _check_qdrant_credentials(self) -> bool:
        """Check if Qdrant Cloud credentials are configured."""
        try:
            cluster_url = os.getenv("QDRANT_CLOUD_URL")
            api_key = os.getenv("QDRANT_API_KEY")

            if cluster_url and api_key:
                self.services["Qdrant Cloud"]["status"] = "CONFIGURED"
                return True
            else:
                self.services["Qdrant Cloud"]["status"] = "NOT_CONFIGURED"
                return False
        except:
            self.services["Qdrant Cloud"]["status"] = "ERROR"
            return False

    def get_port_process(self, port: int) -> Optional[str]:
        """Get process name using a specific port."""
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                for line in result.stdout.split('\n'):
                    if f":{port}" in line and "LISTENING" in line:
                        return "RUNNING"
            else:
                result = subprocess.run(
                    ["lsof", "-i", f":{port}"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    return "RUNNING"
            return None
        except:
            return None

    def display_dashboard(self):
        """Display health dashboard."""
        os.system("cls" if sys.platform == "win32" else "clear")

        print(f"{Colors.BOLD}{Colors.CYAN}")
        print("="*70)
        print("🏥 MARK-LIII SYSTEM HEALTH DASHBOARD")
        print("="*70)
        print(f"{Colors.END}")

        # Services status
        print(f"\n{Colors.BOLD}📡 Service Status:{Colors.END}\n")

        for service_name, service in self.services.items():
            status = service.get("status", "UNKNOWN")

            if status == "UP" or status == "CONFIGURED":
                status_icon = f"{Colors.GREEN}✅{Colors.END}"
                status_text = f"{Colors.GREEN}{status}{Colors.END}"
            elif status == "DOWN" or status == "NOT_CONFIGURED":
                status_icon = f"{Colors.RED}❌{Colors.END}"
                status_text = f"{Colors.RED}{status}{Colors.END}"
            else:
                status_icon = f"{Colors.YELLOW}⚠️{Colors.END}"
                status_text = f"{Colors.YELLOW}{status}{Colors.END}"

            response_time = service.get("response_time", 0)
            last_check = service.get("last_check", "Never")

            print(f"{status_icon} {service_name:.<30} {status_text}")

            if response_time > 0 and status in ["UP", "CONFIGURED"]:
                print(f"   {Colors.CYAN}Response: {response_time*1000:.1f}ms{Colors.END}")

            if last_check and last_check != "Never":
                print(f"   {Colors.CYAN}Last check: {last_check}{Colors.END}")

            print()

        # Overall metrics
        print(f"\n{Colors.BOLD}📊 Metrics:{Colors.END}\n")

        uptime_checks = self.metrics["uptime_checks"]
        failures = self.metrics["failures"]
        success_rate = ((uptime_checks - failures) / uptime_checks * 100) if uptime_checks > 0 else 0

        print(f"  Uptime Checks: {uptime_checks}")
        print(f"  Failures: {failures}")
        print(f"  Success Rate: {success_rate:.1f}%")
        print(f"  Started: {self.metrics['start_time']}")

        # File size check
        print(f"\n{Colors.BOLD}💾 Data Files:{Colors.END}\n")

        log_files = [
            ("JARVIS Logs", self.project_root / "jarvis_instruction_log.json"),
            ("WhatsApp History", self.project_root / "whatsapp_message_history.json"),
            ("Deployment Log", self.project_root / "phase_5_deployment.json")
        ]

        for name, path in log_files:
            if path.exists():
                size = path.stat().st_size / 1024  # KB
                print(f"  ✅ {name}: {size:.1f} KB")
            else:
                print(f"  ⚠️  {name}: Not found")

        # Instructions
        print(f"\n{Colors.BOLD}⌨️  Controls:{Colors.END}")
        print(f"  {Colors.CYAN}Press Ctrl+C to exit{Colors.END}")
        print(f"  {Colors.CYAN}Updates every {self.update_interval} seconds{Colors.END}")

        print(f"\n{Colors.CYAN}{'='*70}{Colors.END}\n")

    def run_continuous_monitoring(self):
        """Run continuous health monitoring."""
        self.monitoring = True

        print(f"{Colors.GREEN}Starting continuous health monitoring...{Colors.END}\n")
        time.sleep(2)

        try:
            while self.monitoring:
                # Check all services
                check_results = {}
                for service_name in self.services.keys():
                    result = self.check_service(service_name)
                    check_results[service_name] = result

                # Update metrics
                self.metrics["uptime_checks"] += 1

                # Count failures
                failures_this_round = sum(1 for v in check_results.values() if not v)
                if failures_this_round > 0:
                    self.metrics["failures"] += failures_this_round
                    self.metrics["last_failure"] = datetime.now().isoformat()

                # Display dashboard
                self.display_dashboard()

                # Save health log
                self.save_health_log()

                # Wait for next update
                time.sleep(self.update_interval)

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Health monitoring stopped by user{Colors.END}")
            self.monitoring = False

    def save_health_log(self):
        """Save health data to JSON log."""
        try:
            log_data = {
                "timestamp": datetime.now().isoformat(),
                "services": self.services,
                "metrics": self.metrics
            }

            with open(self.health_log, 'w') as f:
                json.dump(log_data, f, indent=2)
        except:
            pass  # Silent fail - don't interrupt monitoring

    def start_monitoring(self):
        """Start health monitoring in background."""
        monitor_thread = threading.Thread(target=self.run_continuous_monitoring, daemon=True)
        monitor_thread.start()
        return monitor_thread


def main():
    """Main entry point."""
    update_interval = 10

    if len(sys.argv) > 1:
        try:
            update_interval = int(sys.argv[1])
        except:
            pass

    monitor = HealthMonitor(update_interval=update_interval)
    monitor.run_continuous_monitoring()


if __name__ == "__main__":
    main()
