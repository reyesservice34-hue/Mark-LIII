#!/usr/bin/env python3
"""
Ollama Integration für JARVIS
Verbindet lokale Ollama KI mit JARVIS Coordinator
100% kostenlos, 100% lokal, 0€ pro Monat
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime

class OllamaIntegration:
    """Integriert Ollama in JARVIS Coordinator."""

    def __init__(self):
        self.ollama_url = "http://localhost:11434"
        self.jarvis_url = "http://127.0.0.1:8000"
        self.project_root = Path(__file__).parent.parent
        self.config_file = self.project_root / "ollama_config.json"
        self.env_file = Path(__file__).parent / ".env"

    def check_ollama_running(self) -> bool:
        """Überprüfe ob Ollama läuft."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False

    def get_ollama_models(self) -> list:
        """Liste verfügbare Ollama Modelle."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [m.get('name') for m in data.get('models', [])]
                return models
            return []
        except:
            return []

    def test_ollama_inference(self, model: str, prompt: str = "Hallo") -> bool:
        """Teste Ollama Inference."""
        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=30
            )
            return response.status_code == 200
        except:
            return False

    def create_ollama_connector(self) -> dict:
        """Erstelle JARVIS Ollama Connector."""
        connector = {
            "name": "ollama_integration",
            "type": "local_llm",
            "config": {
                "base_url": self.ollama_url,
                "models": self.get_ollama_models(),
                "default_model": "mistral",
                "parameters": {
                    "temperature": 0.7,
                    "top_k": 40,
                    "top_p": 0.9,
                    "repeat_penalty": 1.1
                }
            },
            "cost": "€0.00",
            "status": "active",
            "created": datetime.now().isoformat()
        }
        return connector

    def update_env_file(self):
        """Update .env mit Ollama Konfiguration."""
        if not self.env_file.exists():
            self.env_file.write_text("")

        env_content = self.env_file.read_text()
        lines = env_content.split('\n')

        new_lines = []
        ollama_url_found = False
        ollama_model_found = False

        for line in lines:
            if line.startswith('OLLAMA_URL='):
                new_lines.append(f'OLLAMA_URL={self.ollama_url}')
                ollama_url_found = True
            elif line.startswith('OLLAMA_MODEL='):
                new_lines.append('OLLAMA_MODEL=mistral')
                ollama_model_found = True
            else:
                new_lines.append(line)

        if not ollama_url_found:
            new_lines.append(f'OLLAMA_URL={self.ollama_url}')
        if not ollama_model_found:
            new_lines.append('OLLAMA_MODEL=mistral')

        self.env_file.write_text('\n'.join(new_lines))

    def save_config(self, connector: dict):
        """Speichere Konfiguration."""
        with open(self.config_file, 'w') as f:
            json.dump(connector, f, indent=2, ensure_ascii=False)

    def run_integration(self):
        """Führe Ollama Integration aus."""
        print("\n" + "="*70)
        print("🤖 OLLAMA INTEGRATION FÜR JARVIS")
        print("="*70 + "\n")

        # Check Ollama
        print("📋 Step 1: Überprüfe Ollama Service")
        print("-" * 70)

        if not self.check_ollama_running():
            print("❌ Ollama läuft nicht!")
            print("   Starte Ollama Service und versuche nochmal:")
            print("   1. Öffne Ollama App (sollte im System tray sein)")
            print("   2. Oder starte: ollama serve")
            return False

        print("✅ Ollama Service läuft auf http://localhost:11434\n")

        # Get Models
        print("📋 Step 2: Lade verfügbare Modelle")
        print("-" * 70)

        models = self.get_ollama_models()
        if not models:
            print("❌ Keine Modelle gefunden!")
            print("   Lade ein Modell mit: ollama pull mistral")
            return False

        print(f"✅ Gefundene Modelle: {', '.join(models)}\n")

        # Test Model
        print("📋 Step 3: Teste Inference")
        print("-" * 70)

        default_model = models[0] if models else "mistral"
        print(f"Teste Modell: {default_model}...")

        if self.test_ollama_inference(default_model):
            print(f"✅ Inference funktioniert!\n")
        else:
            print(f"⚠️  Inference-Test fehlgeschlagen (Modell lädt noch?)\n")

        # Create Connector
        print("📋 Step 4: Erstelle JARVIS Ollama Connector")
        print("-" * 70)

        connector = self.create_ollama_connector()
        self.save_config(connector)
        print(f"✅ Connector erstellt: {self.config_file}\n")

        # Update .env
        print("📋 Step 5: Update .env")
        print("-" * 70)

        self.update_env_file()
        print(f"✅ .env aktualisiert\n")

        # Summary
        print("="*70)
        print("✨ OLLAMA INTEGRATION KOMPLETT!")
        print("="*70 + "\n")

        print("📊 Konfiguration:")
        print(f"  Ollama URL: {self.ollama_url}")
        print(f"  Modelle: {', '.join(models)}")
        print(f"  Kosten: €0.00/Monat")
        print(f"  Status: READY ✅\n")

        print("💡 JARVIS kann jetzt Ollama verwenden statt OpenAI!")
        print("   - Kostenlos")
        print("   - 100% lokal")
        print("   - Keine API Kosten\n")

        print("Nächster Schritt:")
        print("  1. Starte JARVIS: python scripts\\jarvis_coordinator_api.py")
        print("  2. Sende Message: Hallo JARVIS!")
        print("  3. Ollama antwortet lokal (keine API Kosten!)\n")

        return True


if __name__ == "__main__":
    integration = OllamaIntegration()
    success = integration.run_integration()
    exit(0 if success else 1)
