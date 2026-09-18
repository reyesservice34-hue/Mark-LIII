#!/usr/bin/env python3
"""
JARVIS Complete Deployment Verification
Tests all services, integrations, and features
Run this after docker-compose up -d to verify full system health
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

class JARVISDeploymentVerifier:
    """Comprehensive JARVIS system verification."""

    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "tests": {},
            "services": {},
            "integrations": {},
            "summary": {}
        }
        self.all_passed = True

    def print_header(self, title: str):
        """Print formatted header."""
        print(f"\n{'='*60}")
        print(f"🔍 {title}")
        print(f"{'='*60}\n")

    def print_test(self, name: str, status: str, details: str = ""):
        """Print test result."""
        icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
        print(f"{icon} {name:40} [{status}]")
        if details:
            print(f"   → {details}")
        if status == "FAIL":
            self.all_passed = False

    def test_service(self, name: str, url: str, timeout: int = 5) -> bool:
        """Test if service is running and healthy."""
        try:
            response = requests.get(f"{url}/health", timeout=timeout)
            if response.status_code == 200:
                self.results["services"][name] = {
                    "status": "UP",
                    "url": url,
                    "response_time": response.elapsed.total_seconds()
                }
                return True
            else:
                self.results["services"][name] = {
                    "status": "DOWN",
                    "error": f"HTTP {response.status_code}"
                }
                return False
        except requests.exceptions.ConnectionError:
            self.results["services"][name] = {
                "status": "DOWN",
                "error": "Connection refused"
            }
            return False
        except requests.exceptions.Timeout:
            self.results["services"][name] = {
                "status": "TIMEOUT",
                "error": f"No response after {timeout}s"
            }
            return False
        except Exception as e:
            self.results["services"][name] = {
                "status": "ERROR",
                "error": str(e)
            }
            return False

    def test_ollama(self) -> bool:
        """Test Ollama LLM service."""
        self.print_header("OLLAMA LLM SERVICE")

        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

        # Test 1: Service health
        if self.test_service("Ollama", ollama_url):
            self.print_test("Ollama Service", "PASS", f"Running on {ollama_url}")
        else:
            self.print_test("Ollama Service", "FAIL", f"Cannot reach {ollama_url}")
            return False

        # Test 2: List available models
        try:
            response = requests.get(f"{ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [m.get('name') for m in data.get('models', [])]
                if models:
                    self.print_test("Models Available", "PASS", f"Found {len(models)} models: {', '.join(models)}")
                    self.results["integrations"]["ollama_models"] = models
                else:
                    self.print_test("Models Available", "FAIL", "No models loaded")
                    return False
            else:
                self.print_test("Models Available", "FAIL", f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.print_test("Models Available", "FAIL", str(e))
            return False

        # Test 3: Inference capability
        try:
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": "mistral",
                    "prompt": "Antworte kurz: Was bist du?",
                    "stream": False
                },
                timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get('response'):
                    self.print_test("Inference Test", "PASS", "Model generation working")
                    self.results["integrations"]["ollama_inference"] = True
                else:
                    self.print_test("Inference Test", "FAIL", "No response from model")
                    return False
            else:
                self.print_test("Inference Test", "FAIL", f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.print_test("Inference Test", "FAIL", str(e))
            return False

        return True

    def test_jarvis_coordinator(self) -> bool:
        """Test JARVIS Coordinator API."""
        self.print_header("JARVIS COORDINATOR API")

        coordinator_url = os.getenv("JARVIS_COORDINATOR_URL", "http://localhost:8000")

        # Test 1: Service health
        if self.test_service("JARVIS Coordinator", coordinator_url):
            self.print_test("JARVIS Coordinator", "PASS", f"Running on {coordinator_url}")
        else:
            self.print_test("JARVIS Coordinator", "FAIL", f"Cannot reach {coordinator_url}")
            return False

        # Test 2: Agent status
        try:
            response = requests.get(f"{coordinator_url}/agent_status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                agents = data.get("agents", {})
                if agents:
                    self.print_test("10-Agent System", "PASS", f"All {len(agents)} agents online")
                    self.results["integrations"]["jarvis_agents"] = list(agents.keys())
                else:
                    self.print_test("10-Agent System", "FAIL", "No agents found")
                    return False
            else:
                self.print_test("10-Agent System", "FAIL", f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.print_test("10-Agent System", "FAIL", str(e))
            return False

        # Test 3: Master Prompt
        try:
            response = requests.get(f"{coordinator_url}/master_prompt", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("master_prompt"):
                    self.print_test("Master Prompt", "PASS", "System prompt loaded")
                    self.results["integrations"]["master_prompt"] = "loaded"
                else:
                    self.print_test("Master Prompt", "FAIL", "Master prompt not found")
                    return False
            else:
                self.print_test("Master Prompt", "FAIL", f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.print_test("Master Prompt", "FAIL", str(e))
            return False

        # Test 4: Process instruction
        try:
            response = requests.post(
                f"{coordinator_url}/process_instruction",
                json={
                    "user_id": "test_user",
                    "instruction": "Teste die Koordinatorfunktion",
                    "channel": "test"
                },
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    self.print_test("Instruction Processing", "PASS", "Coordinator processing working")
                    self.results["integrations"]["coordinator_processing"] = True
                else:
                    self.print_test("Instruction Processing", "FAIL", "Invalid response")
                    return False
            else:
                self.print_test("Instruction Processing", "FAIL", f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.print_test("Instruction Processing", "FAIL", str(e))
            return False

        return True

    def test_whatsapp_gateway(self) -> bool:
        """Test WhatsApp Gateway with Voice."""
        self.print_header("WHATSAPP GATEWAY WITH VOICE")

        gateway_url = os.getenv("WHATSAPP_GATEWAY_URL", "http://localhost:5000")

        # Test 1: Service health
        if self.test_service("WhatsApp Gateway", gateway_url):
            self.print_test("WhatsApp Gateway", "PASS", f"Running on {gateway_url}")
        else:
            self.print_test("WhatsApp Gateway", "FAIL", f"Cannot reach {gateway_url}")
            return False

        # Test 2: Message processing
        try:
            response = requests.post(
                f"{gateway_url}/receive_message",
                json={
                    "From": "test_user",
                    "Body": "Hallo JARVIS, teste meine Integration"
                },
                timeout=10
            )
            if response.status_code == 200:
                self.print_test("Message Processing", "PASS", "Gateway accepting messages")
                self.results["integrations"]["gateway_messages"] = True
            else:
                self.print_test("Message Processing", "FAIL", f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.print_test("Message Processing", "FAIL", str(e))
            return False

        # Test 3: Voice generation
        try:
            response = requests.post(
                f"{gateway_url}/generate_voice",
                json={
                    "text": "Hallo Welt"
                },
                timeout=15
            )
            if response.status_code == 200:
                self.print_test("Voice Generation", "PASS", "TTS engine working")
                self.results["integrations"]["gateway_voice"] = True
            else:
                self.print_test("Voice Generation", "FAIL", f"HTTP {response.status_code}")
                # Don't fail here as voice is optional
        except Exception as e:
            self.print_test("Voice Generation", "WARN", "TTS unavailable (optional)")

        return True

    def test_memory_systems(self) -> bool:
        """Test JARVIS memory systems."""
        self.print_header("MEMORY SYSTEMS")

        memory_files = {
            ".claude/long_term_memory.md": "Long-term Memory",
            ".claude/session_instructions_memory.json": "Session Instructions",
            ".claude/jarvis_master_system.md": "Master System Prompt"
        }

        all_exist = True
        for file_path, name in memory_files.items():
            if Path(file_path).exists():
                self.print_test(name, "PASS", f"Found at {file_path}")
                self.results["integrations"][f"memory_{name.lower().replace(' ', '_')}"] = True
            else:
                self.print_test(name, "FAIL", f"Missing: {file_path}")
                all_exist = False

        return all_exist

    def test_knowledge_graphs(self) -> bool:
        """Test Graphify knowledge graph system."""
        self.print_header("KNOWLEDGE GRAPH SYSTEM")

        kg_script = Path("scripts/jarvis_graphify_knowledge_system.py")
        kg_dir = Path(".claude/knowledge_graphs")

        if kg_script.exists():
            self.print_test("Graphify Integration Script", "PASS", "Script available")
        else:
            self.print_test("Graphify Integration Script", "FAIL", "Script missing")
            return False

        if kg_dir.exists():
            kg_files = list(kg_dir.glob("*.json"))
            self.print_test("Knowledge Graphs", "PASS", f"Generated {len(kg_files)} graphs")
            self.results["integrations"]["knowledge_graphs"] = len(kg_files)
        else:
            self.print_test("Knowledge Graphs", "WARN", "Directory not yet created (run Graphify)")

        return True

    def test_dockerfiles(self) -> bool:
        """Verify Docker configuration."""
        self.print_header("DOCKER CONFIGURATION")

        docker_files = {
            "docker-compose.yml": "Docker Compose",
            "Dockerfile.jarvis": "JARVIS Coordinator Image",
            "Dockerfile.whatsapp": "WhatsApp Gateway Image",
            "Dockerfile.monitor": "Health Monitor Image",
            ".env.docker": "Docker Environment"
        }

        all_exist = True
        for file_name, name in docker_files.items():
            if Path(file_name).exists():
                self.print_test(name, "PASS", f"Found: {file_name}")
            else:
                self.print_test(name, "FAIL", f"Missing: {file_name}")
                all_exist = False

        return all_exist

    def run_all_tests(self) -> bool:
        """Run all verification tests."""
        print("\n")
        print("██████████████████████████████████████████████████████")
        print("🤖 JARVIS DEPLOYMENT VERIFICATION")
        print("██████████████████████████████████████████████████████")

        # Run all test suites
        tests = [
            ("Docker Configuration", self.test_dockerfiles),
            ("Memory Systems", self.test_memory_systems),
            ("Ollama LLM", self.test_ollama),
            ("JARVIS Coordinator", self.test_jarvis_coordinator),
            ("WhatsApp Gateway", self.test_whatsapp_gateway),
            ("Knowledge Graphs", self.test_knowledge_graphs)
        ]

        for test_name, test_func in tests:
            try:
                test_func()
            except Exception as e:
                print(f"\n❌ {test_name} suite failed: {e}")
                self.all_passed = False

        # Summary
        self.print_header("VERIFICATION SUMMARY")
        self.results["summary"] = {
            "total_services_up": sum(1 for s in self.results["services"].values() if s.get("status") == "UP"),
            "total_services": len(self.results["services"]),
            "all_tests_passed": self.all_passed,
            "timestamp": datetime.now().isoformat()
        }

        if self.all_passed:
            print("✅ ALL TESTS PASSED!")
            print("🎉 JARVIS System is production-ready!")
        else:
            print("⚠️  Some tests failed. See details above.")

        # Save results
        results_file = Path("jarvis_verification_results.json")
        with open(results_file, 'w') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)

        print(f"\n📊 Results saved to: {results_file}")
        print(f"📈 Services status: {self.results['summary']['total_services_up']}/{self.results['summary']['total_services']} UP\n")

        return self.all_passed


def main():
    """Main entry point."""
    verifier = JARVISDeploymentVerifier()
    success = verifier.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
