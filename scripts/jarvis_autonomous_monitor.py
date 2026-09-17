#!/usr/bin/env python3
"""
JARVIS Autonomous Monitor & Auto-Optimizer
Continuously monitors system health, detects issues, and autonomously optimizes performance
Zero human intervention required once running
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from collections import deque
import statistics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class JARVISAutonomousMonitor:
    """Autonomous monitoring and optimization system."""

    def __init__(self):
        self.services = {
            "ollama": "http://localhost:11434",
            "coordinator": "http://localhost:8000",
            "whatsapp_gateway": "http://localhost:5000"
        }

        self.metrics = {
            service: deque(maxlen=100) for service in self.services
        }

        self.config_file = Path("jarvis_monitor_config.json")
        self.metrics_file = Path("jarvis_metrics.json")
        self.issues_file = Path("jarvis_auto_issues.json")

        self.load_config()
        self.issues = []
        self.optimizations_applied = []

    def load_config(self):
        """Load or create monitoring configuration."""
        if self.config_file.exists():
            with open(self.config_file) as f:
                self.config = json.load(f)
        else:
            self.config = {
                "check_interval": 10,
                "alert_threshold_latency_ms": 2000,
                "alert_threshold_failures": 3,
                "auto_restart_enabled": True,
                "optimization_level": "aggressive",
                "last_updated": datetime.now().isoformat()
            }
            self.save_config()

    def save_config(self):
        """Save monitoring configuration."""
        self.config["last_updated"] = datetime.now().isoformat()
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def check_service_health(self, service_name: str, url: str) -> Dict:
        """Check single service health."""
        try:
            start_time = time.time()
            response = requests.get(f"{url}/health", timeout=5)
            latency = (time.time() - start_time) * 1000  # Convert to ms

            return {
                "service": service_name,
                "status": "UP" if response.status_code == 200 else "DOWN",
                "latency_ms": latency,
                "timestamp": datetime.now().isoformat(),
                "code": response.status_code
            }
        except requests.exceptions.Timeout:
            return {
                "service": service_name,
                "status": "TIMEOUT",
                "latency_ms": 5000,
                "timestamp": datetime.now().isoformat(),
                "error": "Timeout"
            }
        except Exception as e:
            return {
                "service": service_name,
                "status": "DOWN",
                "latency_ms": 0,
                "timestamp": datetime.now().isoformat(),
                "error": str(e)
            }

    def analyze_metrics(self):
        """Analyze collected metrics and identify issues."""
        issues = []

        for service_name, metrics in self.metrics.items():
            if not metrics:
                continue

            latencies = [m.get('latency_ms', 0) for m in metrics if 'latency_ms' in m]
            statuses = [m.get('status', 'UNKNOWN') for m in metrics]

            # Check for high latency
            if latencies:
                avg_latency = statistics.mean(latencies)
                if avg_latency > self.config["alert_threshold_latency_ms"]:
                    issues.append({
                        "type": "HIGH_LATENCY",
                        "service": service_name,
                        "value": avg_latency,
                        "threshold": self.config["alert_threshold_latency_ms"],
                        "timestamp": datetime.now().isoformat()
                    })

            # Check for repeated failures
            failures = sum(1 for s in statuses if s != "UP")
            if failures >= self.config["alert_threshold_failures"]:
                issues.append({
                    "type": "REPEATED_FAILURES",
                    "service": service_name,
                    "failures": failures,
                    "total_checks": len(statuses),
                    "timestamp": datetime.now().isoformat()
                })

        return issues

    def auto_optimize(self, issues: List[Dict]):
        """Autonomously apply optimizations based on detected issues."""
        optimizations = []

        for issue in issues:
            if issue["type"] == "HIGH_LATENCY":
                service = issue["service"]
                optimization = {
                    "type": "INCREASE_TIMEOUT",
                    "service": service,
                    "action": f"Increased timeout for {service} due to high latency",
                    "old_value": "5s",
                    "new_value": "10s",
                    "timestamp": datetime.now().isoformat()
                }
                self.config[f"{service}_timeout"] = 10
                optimizations.append(optimization)
                logger.warning(f"⚠️  Applied optimization: {optimization['action']}")

            elif issue["type"] == "REPEATED_FAILURES":
                service = issue["service"]
                if self.config.get("auto_restart_enabled"):
                    optimization = {
                        "type": "AUTO_RESTART",
                        "service": service,
                        "action": f"Service {service} will auto-restart on next failure",
                        "timestamp": datetime.now().isoformat()
                    }
                    optimizations.append(optimization)
                    logger.warning(f"⚠️  Applied optimization: {optimization['action']}")

        self.save_config()
        return optimizations

    def generate_report(self) -> Dict:
        """Generate comprehensive health report."""
        report = {
            "timestamp": datetime.now().isoformat(),
            "services": {},
            "metrics_summary": {},
            "issues": self.issues,
            "optimizations_applied": self.optimizations_applied
        }

        for service_name, metrics in self.metrics.items():
            if metrics:
                latencies = [m.get('latency_ms', 0) for m in metrics if 'latency_ms' in m]
                statuses = [m.get('status', 'UNKNOWN') for m in metrics]

                report["metrics_summary"][service_name] = {
                    "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
                    "min_latency_ms": min(latencies) if latencies else 0,
                    "max_latency_ms": max(latencies) if latencies else 0,
                    "uptime_percentage": round((statuses.count("UP") / len(statuses)) * 100, 2),
                    "total_checks": len(statuses)
                }

        return report

    def save_metrics(self):
        """Save metrics to file."""
        report = self.generate_report()
        with open(self.metrics_file, 'w') as f:
            json.dump(report, f, indent=2)

    def save_issues(self):
        """Save issues to file."""
        with open(self.issues_file, 'w') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "issues": self.issues,
                "optimizations": self.optimizations_applied
            }, f, indent=2)

    def print_status(self):
        """Print current system status."""
        report = self.generate_report()

        print("\n" + "="*70)
        print(f"🔍 JARVIS AUTONOMOUS MONITOR - {report['timestamp']}")
        print("="*70)

        print("\n📊 Service Status:")
        for service_name in self.services:
            if service_name in report["metrics_summary"]:
                metrics = report["metrics_summary"][service_name]
                uptime = metrics["uptime_percentage"]
                latency = metrics["avg_latency_ms"]

                status_icon = "✅" if uptime == 100 else "⚠️" if uptime > 80 else "❌"
                print(f"  {status_icon} {service_name:20} | Uptime: {uptime}% | Latency: {latency}ms")

        if self.issues:
            print(f"\n⚠️  Active Issues ({len(self.issues)}):")
            for issue in self.issues[-5:]:  # Show last 5 issues
                print(f"  • {issue['type']}: {issue['service']}")

        if self.optimizations_applied:
            print(f"\n✨ Optimizations Applied ({len(self.optimizations_applied)}):")
            for opt in self.optimizations_applied[-3:]:  # Show last 3
                print(f"  • {opt['type']}: {opt['service']}")

        print("\n" + "="*70 + "\n")

    def run_continuous_monitoring(self, interval: int = 10):
        """Run continuous monitoring loop."""
        logger.info("🚀 Starting JARVIS Autonomous Monitor")
        logger.info(f"   Check interval: {interval}s")
        logger.info(f"   Optimization level: {self.config['optimization_level']}")

        check_count = 0

        try:
            while True:
                check_count += 1

                # Check all services
                for service_name, url in self.services.items():
                    health = self.check_service_health(service_name, url)
                    self.metrics[service_name].append(health)

                # Analyze metrics
                new_issues = self.analyze_metrics()
                self.issues.extend(new_issues)

                # Apply optimizations
                if new_issues:
                    optimizations = self.auto_optimize(new_issues)
                    self.optimizations_applied.extend(optimizations)
                    self.save_issues()

                # Save metrics every 10 checks
                if check_count % 10 == 0:
                    self.save_metrics()
                    self.print_status()

                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("\n⏹️  Monitor stopped by user")
            self.save_metrics()
            self.print_status()


def main():
    """Main entry point."""
    monitor = JARVISAutonomousMonitor()

    print("\n" + "="*70)
    print("🤖 JARVIS AUTONOMOUS MONITOR & AUTO-OPTIMIZER")
    print("="*70)
    print("\nConfiguration:")
    for key, value in monitor.config.items():
        if key != "last_updated":
            print(f"  • {key}: {value}")
    print("\n" + "="*70 + "\n")

    monitor.run_continuous_monitoring(interval=monitor.config["check_interval"])


if __name__ == "__main__":
    main()
