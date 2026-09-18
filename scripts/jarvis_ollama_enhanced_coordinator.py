#!/usr/bin/env python3
"""
JARVIS Enhanced Coordinator with Ollama Integration
Integrates local LLM for prompt enhancement, context understanding, and intelligent delegation
Zero API costs, 100% local, intelligent responses
"""

import os
import json
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OllamaPromptEnhancer:
    """Uses Ollama to enhance and understand user instructions."""

    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = "mistral"):
        self.ollama_url = ollama_url
        self.model = model
        self.is_available = self._check_availability()

    def _check_availability(self) -> bool:
        """Check if Ollama is available."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False

    def enhance_instruction(self, instruction: str) -> Dict:
        """Use Ollama to enhance instruction understanding."""
        if not self.is_available:
            return {
                "original": instruction,
                "enhanced": instruction,
                "intent": "unknown",
                "priority": "medium",
                "source": "fallback"
            }

        prompt = f"""
Analysiere diese Anweisung und gib JSON zurück:
{{"intent": "...", "priority": "high/medium/low", "key_actions": [...], "requires_external_api": true/false}}

Anweisung: {instruction}
"""

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.3
                },
                timeout=15
            )

            if response.status_code == 200:
                result = response.json().get('response', '').strip()
                try:
                    data = json.loads(result)
                    return {
                        "original": instruction,
                        "enhanced": instruction,
                        "intent": data.get("intent", "general"),
                        "priority": data.get("priority", "medium"),
                        "key_actions": data.get("key_actions", []),
                        "requires_external_api": data.get("requires_external_api", False),
                        "source": "ollama"
                    }
                except json.JSONDecodeError:
                    return {
                        "original": instruction,
                        "enhanced": result,
                        "intent": "understood",
                        "priority": "medium",
                        "source": "ollama_text"
                    }
        except Exception as e:
            logger.warning(f"Ollama enhancement failed: {e}")

        return {
            "original": instruction,
            "enhanced": instruction,
            "intent": "unknown",
            "priority": "medium",
            "source": "fallback"
        }

    def generate_response(self, context: str, instruction: str, max_tokens: int = 300) -> Optional[str]:
        """Generate contextual response using Ollama."""
        if not self.is_available:
            return None

        prompt = f"""
Kontext: {context}

Nutzeranfrage: {instruction}

Antworte hilfreich und präzise auf Deutsch:
"""

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
        except Exception as e:
            logger.warning(f"Response generation failed: {e}")

        return None


class JARVISEnhancedCoordinator:
    """Enhanced JARVIS Coordinator with Ollama integration."""

    def __init__(self):
        self.ollama_enhancer = OllamaPromptEnhancer()
        self.instruction_log = Path("jarvis_enhanced_instruction_log.json")
        self.load_master_prompt()
        self._load_instruction_history()

        logger.info("✅ JARVIS Enhanced Coordinator initialized with Ollama")
        logger.info(f"   Ollama Status: {'Available' if self.ollama_enhancer.is_available else 'Unavailable'}")

    def load_master_prompt(self):
        """Load JARVIS Master Prompt."""
        master_prompt_path = Path(".claude/jarvis_master_system.md")
        if master_prompt_path.exists():
            with open(master_prompt_path, 'r', encoding='utf-8') as f:
                self.master_prompt = f.read()
        else:
            self.master_prompt = "JARVIS - Advanced AI Assistant"

    def _load_instruction_history(self):
        """Load instruction history."""
        if self.instruction_log.exists():
            with open(self.instruction_log) as f:
                self.history = json.load(f)
        else:
            self.history = {"instructions": []}

    def _save_instruction_history(self):
        """Save instruction history."""
        with open(self.instruction_log, "w") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def process_instruction(self, user_id: str, instruction: str, channel: str = "whatsapp") -> Dict:
        """Process instruction with Ollama enhancement."""

        logger.info(f"📋 Processing: {instruction[:60]}...")

        # Step 1: Enhance instruction with Ollama
        enhancement = self.ollama_enhancer.enhance_instruction(instruction)
        logger.info(f"   Intent: {enhancement['intent']}, Priority: {enhancement['priority']}")

        # Step 2: Generate contextual response
        context = f"User: {user_id}, Channel: {channel}, Master: JARVIS"
        response = self.ollama_enhancer.generate_response(context, instruction)

        if not response:
            response = f"Anweisung verarbeitet: {instruction[:50]}..."

        # Step 3: Log
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "channel": channel,
            "instruction": instruction,
            "enhancement": enhancement,
            "response": response,
            "status": "completed"
        }
        self.history["instructions"].append(log_entry)
        self._save_instruction_history()

        return {
            "status": "ok",
            "user_id": user_id,
            "channel": channel,
            "original_instruction": instruction,
            "intent": enhancement['intent'],
            "priority": enhancement['priority'],
            "response": response,
            "ollama_used": enhancement['source'] == 'ollama',
            "timestamp": datetime.now().isoformat()
        }

    def get_status(self) -> Dict:
        """Get coordinator status."""
        return {
            "service": "JARVIS Enhanced Coordinator",
            "status": "active",
            "ollama": "available" if self.ollama_enhancer.is_available else "unavailable",
            "master_prompt": "loaded",
            "instructions_processed": len(self.history["instructions"]),
            "timestamp": datetime.now().isoformat()
        }


# Testing
if __name__ == "__main__":
    coordinator = JARVISEnhancedCoordinator()

    print("\n" + "="*60)
    print("🤖 JARVIS Enhanced Coordinator")
    print("="*60 + "\n")

    # Test instruction
    result = coordinator.process_instruction(
        user_id="test_user",
        instruction="Was kannst du alles mit Ollama machen? Antworte kurz.",
        channel="test"
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\n" + "="*60)
    print("Status:", coordinator.get_status())
    print("="*60 + "\n")
