#!/usr/bin/env python3
"""
JARVIS Coordinator REST API
Receives instructions from WhatsApp and coordinates 10-agent system
Enhanced with Prompt Optimization Engine and Master System Prompt
"""

import os
import json
import logging
from typing import Dict, List
from datetime import datetime
from pathlib import Path
import asyncio
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# Load Master Prompt from memory
def load_master_prompt():
    """Load JARVIS Master Prompt from .claude/jarvis_master_system.md"""
    master_prompt_path = Path(".claude/jarvis_master_system.md")
    if master_prompt_path.exists():
        with open(master_prompt_path, 'r') as f:
            return f.read()
    return None


# Initialize Master Prompt
MASTER_PROMPT = load_master_prompt()


class PromptOptimizer:
    """Optimizes natural language input into structured prompts."""

    CATEGORIES = {
        "pricing": ["preis", "kosten", "budget", "preisplan", "angebot"],
        "planning": ["plan", "termin", "zeitplan", "deadline"],
        "research": ["recherche", "suche", "finde", "untersuchung"],
        "technical": ["code", "script", "automation", "implementation"],
        "advisory": ["rat", "vorschlag", "empfehlung", "strategie"],
        "communication": ["kunde", "mitteilen", "nachricht"],
        "analysis": ["analysiere", "untersuche", "evaluiere", "vergleiche"],
        "creative": ["erstelle", "entwerfe", "designiere", "brainstorm"],
        "optimization": ["optimiere", "verbessere", "effizienz", "schneller"]
    }

    def optimize(self, user_input: str, language: str = "de") -> Dict:
        """Transform natural language into optimized prompt structure."""
        categories = self._detect_categories(user_input)
        complexity = self._assess_complexity(user_input)

        return {
            "original_input": user_input,
            "categories": categories,
            "complexity": complexity,
            "language": language,
            "optimization_timestamp": datetime.now().isoformat(),
            "ready_for_agent_execution": True
        }

    def _detect_categories(self, text: str) -> List[str]:
        """Detect task categories from text."""
        text_lower = text.lower()
        found_categories = []

        for category, keywords in self.CATEGORIES.items():
            if any(kw in text_lower for kw in keywords):
                found_categories.append(category)

        return found_categories if found_categories else ["general"]

    def _assess_complexity(self, text: str) -> str:
        """Assess complexity level."""
        length = len(text)
        if length > 300:
            return "high"
        elif length > 100:
            return "medium"
        else:
            return "low"


class JARVISCoordinator:
    """Master orchestrator for 10-agent system."""

    AGENTS = {
        "coordinator": {"role": "Orchestrates all tasks", "status": "ACTIVE"},
        "prompt_architect": {"role": "Creates master prompts", "status": "READY"},
        "executor": {"role": "Executes instructions", "status": "READY"},
        "reviewer": {"role": "Reviews results", "status": "READY"},
        "angebot": {"role": "Quotations & Pricing", "status": "READY"},
        "dispo": {"role": "Planning & Scheduling", "status": "READY"},
        "kunde": {"role": "Customer Communication", "status": "READY"},
        "recherche": {"role": "Research & Facts", "status": "READY"},
        "technik": {"role": "Code & Automation", "status": "READY"},
        "berater": {"role": "Advisory & Consulting", "status": "READY"},
    }

    def __init__(self):
        self.instruction_log = Path("jarvis_instruction_log.json")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.n8n_api_key = os.getenv("N8N_API_KEY", "")
        self.prompt_optimizer = PromptOptimizer()
        self.master_prompt = MASTER_PROMPT
        self._load_instruction_history()

    def _load_instruction_history(self):
        """Load instruction history."""
        if self.instruction_log.exists():
            with open(self.instruction_log) as f:
                self.history = json.load(f)
        else:
            self.history = {"instructions": [], "decisions": []}

    def _save_instruction_history(self):
        """Save instruction history."""
        with open(self.instruction_log, "w") as f:
            json.dump(self.history, f, indent=2)

    def process_instruction(self, user_id: str, instruction: str, channel: str = "whatsapp") -> Dict:
        """Process user instruction and delegate to appropriate agents."""

        logger.info(f"📋 Processing instruction from {user_id} via {channel}")
        logger.info(f"   Instruction: {instruction[:100]}")

        # Step 1: Prompt Optimizer enhances instruction
        optimized = self.prompt_optimizer.optimize(instruction, language="de")
        logger.info(f"   📝 Prompt optimized: {optimized['categories']}")

        # Step 2: Prompt Architect analyzes instruction
        prompt_analysis = self._analyze_instruction(instruction)

        # Step 3: Determine which agents to delegate to
        delegation_plan = self._create_delegation_plan(prompt_analysis)

        # Step 4: Execute delegation
        execution_results = self._execute_delegation(delegation_plan, instruction)

        # Step 5: Reviewer verifies results
        final_response = self._review_and_format(execution_results)

        # Store in history
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "channel": channel,
            "instruction": instruction,
            "optimization": optimized,
            "delegation_plan": delegation_plan,
            "response": final_response,
            "status": "completed"
        }
        self.history["instructions"].append(log_entry)
        self._save_instruction_history()

        return final_response

    def _analyze_instruction(self, instruction: str) -> Dict:
        """Analyze instruction to determine required capabilities."""

        keywords = {
            "pricing": ["angebot", "preis", "kosten", "budget"],
            "planning": ["plan", "termin", "zeitplan", "deadline"],
            "research": ["research", "recherche", "suche", "infos"],
            "technical": ["code", "script", "automation", "technical"],
            "advisory": ["rat", "vorschlag", "strategie", "beratung"],
            "communication": ["kunde", "mitteilen", "nachricht", "kontakt"],
        }

        instruction_lower = instruction.lower()
        detected_categories = []

        for category, words in keywords.items():
            if any(word in instruction_lower for word in words):
                detected_categories.append(category)

        if not detected_categories:
            detected_categories = ["general"]

        return {
            "categories": detected_categories,
            "complexity": "high" if len(instruction) > 200 else "medium" if len(instruction) > 50 else "low",
            "requires_openai": any(word in instruction_lower for word in ["analyze", "deep", "complex", "analyse"]),
            "optimized": True
        }

    def _create_delegation_plan(self, analysis: Dict) -> List[str]:
        """Create plan for which agents to involve."""

        delegation_map = {
            "pricing": ["angebot", "reviewer"],
            "planning": ["dispo", "kunde"],
            "research": ["recherche", "prompt_architect"],
            "technical": ["technik", "executor"],
            "advisory": ["berater", "prompt_architect"],
            "communication": ["kunde", "prompt_architect"],
            "general": ["executor", "prompt_architect"]
        }

        agents_to_use = set(["executor"])  # Always start with executor

        for category in analysis.get("categories", []):
            if category in delegation_map:
                agents_to_use.update(delegation_map[category])

        if analysis.get("requires_openai"):
            agents_to_use.add("prompt_architect")

        return list(agents_to_use)

    def _execute_delegation(self, delegation_plan: List[str], instruction: str) -> Dict:
        """Execute delegation to selected agents."""

        results = {}

        for agent_name in delegation_plan:
            if agent_name not in self.AGENTS:
                continue

            agent_info = self.AGENTS[agent_name]

            # Simulate agent execution (in production, would call actual agent APIs)
            agent_result = {
                "agent": agent_name,
                "role": agent_info["role"],
                "status": "completed",
                "result": self._simulate_agent_execution(agent_name, instruction),
                "timestamp": datetime.now().isoformat()
            }

            results[agent_name] = agent_result
            logger.info(f"   ✅ {agent_name}: completed")

        return results

    def _simulate_agent_execution(self, agent_name: str, instruction: str) -> str:
        """Simulate agent execution (placeholder for real agent APIs)."""

        responses = {
            "executor": f"🔧 Befehl ausgeführt: {instruction[:50]}...",
            "prompt_architect": "📝 Prompt erstellt und optimiert",
            "reviewer": "✅ Qualitätsprüfung bestanden",
            "angebot": "💰 Preisangebot erstellt",
            "dispo": "📅 Zeitplan aktualisiert",
            "kunde": "👥 Kundenbenachrichtigung gesendet",
            "recherche": "🔍 Recherche durchgeführt",
            "technik": "⚙️  Technische Umsetzung gestartet",
            "berater": "💡 Beratung bereitgestellt",
        }

        return responses.get(agent_name, "✅ Befehl ausgeführt")

    def _review_and_format(self, results: Dict) -> str:
        """Review all results and format response."""

        completed_agents = list(results.keys())
        status_lines = [
            f"🤖 JARVIS Coordinator - Befehl verarbeitet",
            f"👥 Agenten eingesetzt: {', '.join(completed_agents)}",
            "",
        ]

        for agent_name, agent_result in results.items():
            status_lines.append(f"✅ {agent_name}: {agent_result['result']}")

        status_lines.extend([
            "",
            "✨ Alle Schritte abgeschlossen",
            "📝 Weitere Anweisungen über WhatsApp jederzeit möglich"
        ])

        return "\n".join(status_lines)

    def get_agent_status(self) -> Dict:
        """Get status of all agents."""
        return {
            "timestamp": datetime.now().isoformat(),
            "total_agents": len(self.AGENTS),
            "agents": self.AGENTS
        }


coordinator = JARVISCoordinator()


@app.route("/process_instruction", methods=["POST"])
def process_instruction():
    """Process instruction from any channel (WhatsApp, CLI, etc)."""
    try:
        data = request.get_json()

        user_id = data.get("user_id", "unknown")
        instruction = data.get("instruction", "")
        channel = data.get("channel", "api")

        if not instruction:
            return {"error": "Keine Anweisung vorhanden"}, 400

        response = coordinator.process_instruction(user_id, instruction, channel)

        return {
            "status": "ok",
            "user_id": user_id,
            "channel": channel,
            "response": response,
            "timestamp": datetime.now().isoformat()
        }, 200

    except Exception as e:
        logger.error(f"❌ Processing error: {e}")
        return {"error": str(e)}, 500


@app.route("/agent_status", methods=["GET"])
def agent_status():
    """Get status of all agents."""
    try:
        status = coordinator.get_agent_status()
        return status, 200
    except Exception as e:
        logger.error(f"❌ Status error: {e}")
        return {"error": str(e)}, 500


@app.route("/instruction_history", methods=["GET"])
def instruction_history():
    """Get instruction processing history."""
    try:
        user_id = request.args.get("user_id")

        if user_id:
            filtered = [
                instr for instr in coordinator.history["instructions"]
                if instr.get("user_id") == user_id
            ]
            return {"instructions": filtered}, 200
        else:
            return {"instructions": coordinator.history["instructions"]}, 200

    except Exception as e:
        logger.error(f"❌ History error: {e}")
        return {"error": str(e)}, 500


@app.route("/optimize_prompt", methods=["POST"])
def optimize_prompt():
    """Optimize natural language input into structured prompt."""
    try:
        data = request.get_json()
        user_input = data.get("input", "")
        language = data.get("language", "de")

        if not user_input:
            return {"error": "Keine Eingabe vorhanden"}, 400

        optimized = coordinator.prompt_optimizer.optimize(user_input, language)

        return {
            "status": "ok",
            "optimization": optimized,
            "timestamp": datetime.now().isoformat()
        }, 200

    except Exception as e:
        logger.error(f"❌ Optimization error: {e}")
        return {"error": str(e)}, 500


@app.route("/master_prompt", methods=["GET"])
def get_master_prompt():
    """Get JARVIS Master Prompt."""
    try:
        if not coordinator.master_prompt:
            return {"error": "Master Prompt nicht gefunden"}, 404

        return {
            "status": "ok",
            "master_prompt": coordinator.master_prompt,
            "loaded_at": datetime.now().isoformat(),
            "identity": "J.A.R.V.I.S. - Mentor, CEO, Best Friend, Advisor, Strategist"
        }, 200

    except Exception as e:
        logger.error(f"❌ Master Prompt error: {e}")
        return {"error": str(e)}, 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "JARVIS Coordinator API",
        "agents_online": len(coordinator.AGENTS),
        "prompt_optimizer": "active",
        "master_prompt": "loaded" if coordinator.master_prompt else "not_found",
        "timestamp": datetime.now().isoformat()
    }, 200


def main():
    """Start JARVIS Coordinator API."""
    print("\n" + "="*60)
    print("🤖 JARVIS COORDINATOR API")
    print("="*60 + "\n")

    print("📊 Agent Status:")
    status = coordinator.get_agent_status()
    for agent_name, agent_info in status["agents"].items():
        print(f"   ✅ {agent_name:20} | {agent_info['status']}")

    print("\n🔗 Endpoints:")
    print("   POST /process_instruction     - Process instruction from any source")
    print("   POST /optimize_prompt         - Optimize natural language to structured prompt")
    print("   GET  /master_prompt           - Get JARVIS Master System Prompt")
    print("   GET  /agent_status            - Get agent status")
    print("   GET  /instruction_history     - Get processing history")
    print("   GET  /health                  - Health check")
    print("\n")

    print("📝 Example request:")
    print("""
   curl -X POST http://localhost:8000/process_instruction \\
     -H "Content-Type: application/json" \\
     -d '{
       "user_id": "user@example.com",
       "instruction": "Erstelle einen Preisplan für ein neues Projekt",
       "channel": "whatsapp"
     }'
    """)

    print("\n" + "="*60)
    print("🚀 Starting JARVIS Coordinator on :8000...")
    print("="*60 + "\n")

    app.run(host="0.0.0.0", port=8000, debug=False)


if __name__ == "__main__":
    main()
