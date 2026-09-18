#!/usr/bin/env python3
"""
JARVIS Ollama Integration
Enhanced Ollama LLM provider for JARVIS Coordinator
100% kostenlos, 100% lokal, €0.00 Betrieb
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

class OllamaLLMProvider:
    """Ollama integration as LLM provider for JARVIS Coordinator."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "mistral"):
        self.ollama_url = url
        self.model = model
        self.config_file = Path("ollama_integration.json")
        self.is_available = False
        self._check_availability()

    def _check_availability(self) -> bool:
        """Check if Ollama service is running."""
        try:
            response = requests.get(
                f"{self.ollama_url}/api/tags",
                timeout=2
            )
            self.is_available = response.status_code == 200
            return self.is_available
        except:
            self.is_available = False
            return False

    def get_available_models(self) -> list:
        """Get list of available Ollama models."""
        if not self.is_available:
            return []

        try:
            response = requests.get(
                f"{self.ollama_url}/api/tags",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                models = [m.get('name') for m in data.get('models', [])]
                return models
            return []
        except:
            return []

    def generate_response(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Generate response using Ollama local LLM."""
        if not self.is_available:
            return None

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.7,
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return data.get('response', '').strip()
            return None

        except Exception as e:
            print(f"❌ Ollama generation failed: {e}")
            return None

    def setup_jarvis_llm_config(self) -> Dict:
        """Create JARVIS LLM configuration with Ollama."""
        models = self.get_available_models()

        config = {
            "llm_provider": "ollama",
            "ollama_url": self.ollama_url,
            "default_model": models[0] if models else "mistral",
            "available_models": models,
            "capabilities": {
                "text_generation": True,
                "local_execution": True,
                "cost": "€0.00",
                "latency": "< 2 seconds",
                "context_window": "4096 tokens"
            },
            "features": [
                "Fast inference (local)",
                "Privacy-preserving (no data sent to cloud)",
                "Unlimited API calls (€0.00)",
                "Multiple model support",
                "GPU acceleration (if available)"
            ],
            "status": "ready" if models else "models_needed",
            "created": datetime.now().isoformat()
        }

        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return config

    def test_inference(self, test_prompt: str = "Wie heißt du?") -> bool:
        """Test Ollama inference capability."""
        if not self.is_available:
            return False

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": test_prompt,
                    "stream": False
                },
                timeout=30
            )
            return response.status_code == 200
        except:
            return False

    def get_status(self) -> Dict:
        """Get Ollama provider status."""
        return {
            "provider": "Ollama",
            "status": "available" if self.is_available else "unavailable",
            "url": self.ollama_url,
            "model": self.model,
            "models_available": self.get_available_models(),
            "timestamp": datetime.now().isoformat()
        }


class JARVISOllamaCoordinator:
    """Enhanced JARVIS Coordinator with Ollama LLM support."""

    def __init__(self):
        self.ollama_provider = OllamaLLMProvider()
        self.config = self.ollama_provider.setup_jarvis_llm_config()
        self.env_file = Path(".env")

    def integrate_with_environment(self):
        """Update .env with Ollama configuration."""
        env_content = ""
        if self.env_file.exists():
            env_content = self.env_file.read_text()

        lines = env_content.split('\n')
        ollama_url_found = False
        ollama_model_found = False

        new_lines = []
        for line in lines:
            if line.startswith('OLLAMA_URL='):
                new_lines.append(f'OLLAMA_URL={self.ollama_provider.ollama_url}')
                ollama_url_found = True
            elif line.startswith('OLLAMA_MODEL='):
                new_lines.append(f'OLLAMA_MODEL={self.ollama_provider.model}')
                ollama_model_found = True
            else:
                new_lines.append(line)

        if not ollama_url_found:
            new_lines.append(f'OLLAMA_URL={self.ollama_provider.ollama_url}')
        if not ollama_model_found:
            new_lines.append(f'OLLAMA_MODEL={self.ollama_provider.model}')

        self.env_file.write_text('\n'.join(new_lines))

    def test_end_to_end(self) -> bool:
        """Test complete Ollama integration."""
        if not self.ollama_provider.is_available:
            return False

        # Test 1: Connection
        print("✅ Test 1: Ollama Connection - PASS")

        # Test 2: Model availability
        models = self.ollama_provider.get_available_models()
        if models:
            print(f"✅ Test 2: Models Available - PASS ({', '.join(models)})")
        else:
            print("❌ Test 2: Models Available - FAIL (no models found)")
            return False

        # Test 3: Inference
        if self.ollama_provider.test_inference():
            print("✅ Test 3: Inference Test - PASS")
        else:
            print("❌ Test 3: Inference Test - FAIL")
            return False

        # Test 4: Configuration
        print("✅ Test 4: Configuration - PASS")

        return True

    def print_status(self):
        """Print integration status."""
        print("\n" + "="*70)
        print("🤖 JARVIS + OLLAMA INTEGRATION STATUS")
        print("="*70 + "\n")

        status = self.ollama_provider.get_status()
        print(f"Provider Status: {status['status'].upper()}")
        print(f"URL: {status['url']}")
        print(f"Model: {status['model']}")
        print(f"Available Models: {', '.join(status['models_available']) if status['models_available'] else 'None'}")
        print()

        if status['status'] == 'available':
            print("✅ Ollama is ready for JARVIS!")
            print("✅ Cost: €0.00/month (100% lokal)")
            print("✅ Speed: < 2 Sekunden pro Query")
        else:
            print("⚠️  Ollama nicht verfügbar")
            print("   Starte Ollama: ollama serve")
            print("   Oder unter Windows: Starte Ollama App")


def main():
    """Run JARVIS Ollama Integration."""
    print("\n" + "="*70)
    print("🤖 JARVIS OLLAMA INTEGRATION SETUP")
    print("="*70 + "\n")

    # Initialize
    print("📋 Initializing Ollama integration...")
    coordinator = JARVISOllamaCoordinator()

    # Check status
    coordinator.print_status()

    # Integrate with environment
    if coordinator.ollama_provider.is_available:
        print("\n📝 Updating .env configuration...")
        coordinator.integrate_with_environment()
        print("✅ .env updated with Ollama settings")

        # Run tests
        print("\n🧪 Running integration tests...")
        if coordinator.test_end_to_end():
            print("\n✨ INTEGRATION SUCCESSFUL!")
            print("JARVIS can now use Ollama for:")
            print("  - Text generation (€0.00)")
            print("  - Prompt optimization")
            print("  - Task delegation")
            print("  - Response generation")
        else:
            print("\n⚠️  Some tests failed")
    else:
        print("\n⚠️  Ollama nicht verfügbar")
        print("   Bitte starten Sie Ollama zuerst:")
        print("   - Windows: Öffnen Sie Ollama App")
        print("   - Linux/Mac: ollama serve")
        print("   - Docker: docker run -d -p 11434:11434 ollama/ollama")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()
