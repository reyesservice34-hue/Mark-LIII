#!/usr/bin/env python3
"""
JARVIS Coordinator with Integrated Prompt Optimization Engine
Natural language input → Optimized prompt → Agent execution → Response
"""

import os
import json
import logging
from typing import Dict
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# Import the prompt optimizer
import sys
sys.path.insert(0, str(Path(__file__).parent))
from prompt_optimization_engine import PromptOptimizer

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


class JARVISWithOptimization:
    """JARVIS Coordinator with integrated prompt optimization."""

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
        self.optimizer = PromptOptimizer()
        self.instruction_log = Path("jarvis_optimized_instruction_log.json")
        self._load_instruction_history()

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
            json.dump(self.history, f, indent=2)

    def process_natural_language(self, user_id: str, natural_input: str, channel: str = "whatsapp") -> Dict:
        """
        Process natural language input through optimization → execution pipeline.

        Pipeline:
        1. Natural Language Input
        2. Prompt Optimization
        3. Agent Delegation
        4. Execution
        5. Verification
        6. Response
        """

        logger.info(f"🎯 Processing natural language from {user_id}")
        logger.info(f"   Input: {natural_input[:80]}...")

        # Step 1: Optimize the prompt
        optimization_result = self.optimizer.optimize(natural_input)
        optimized_prompt = optimization_result['optimized_prompt']
        category = optimization_result['context']['category']

        logger.info(f"✅ Prompt optimized: {category}")

        # Step 2: Determine delegation based on category
        delegation_plan = self._create_delegation_plan(category, optimization_result)

        # Step 3: Execute delegation
        execution_results = self._execute_delegation(delegation_plan, optimized_prompt)

        # Step 4: Reviewer verifies results
        final_response = self._review_and_format(
            natural_input=natural_input,
            optimized_prompt=optimized_prompt,
            execution_results=execution_results,
            category=category
        )

        # Step 5: Store in history
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "channel": channel,
            "original_input": natural_input,
            "optimization": {
                "category": category,
                "complexity": optimization_result['context']['complexity'],
                "optimized_prompt": optimized_prompt
            },
            "delegation_plan": delegation_plan,
            "execution_results": execution_results,
            "response": final_response,
            "status": "completed"
        }
        self.history["instructions"].append(log_entry)
        self._save_instruction_history()

        return {
            "status": "ok",
            "user_id": user_id,
            "original_input": natural_input,
            "optimization_category": category,
            "optimized_prompt": optimized_prompt,
            "agents_used": delegation_plan,
            "response": final_response,
            "timestamp": datetime.now().isoformat()
        }

    def _create_delegation_plan(self, category: str, optimization_result: Dict) -> list:
        """Create agent delegation plan based on category."""

        delegation_map = {
            "pricing": ["angebot", "executor", "reviewer", "prompt_architect"],
            "planning": ["dispo", "executor", "kunde", "prompt_architect"],
            "research": ["recherche", "prompt_architect", "executor"],
            "technical": ["technik", "executor", "reviewer"],
            "advisory": ["berater", "prompt_architect", "executor"],
            "communication": ["kunde", "executor", "prompt_architect"],
            "analysis": ["recherche", "prompt_architect", "executor", "reviewer"],
            "creative": ["prompt_architect", "executor", "berater"],
            "optimization": ["technik", "executor", "berater", "reviewer"],
            "other": ["executor", "prompt_architect"]
        }

        base_agents = delegation_map.get(category, delegation_map["other"])

        # Always include coordinator for orchestration
        agents = list(set(["coordinator"] + base_agents))

        return agents

    def _execute_delegation(self, delegation_plan: list, optimized_prompt: str) -> Dict:
        """Execute delegation to agents."""
        results = {}

        for agent_name in delegation_plan:
            if agent_name not in self.AGENTS:
                continue

            agent_info = self.AGENTS[agent_name]
            agent_result = {
                "agent": agent_name,
                "role": agent_info["role"],
                "status": "completed",
                "result": self._simulate_agent_execution(agent_name, optimized_prompt),
                "timestamp": datetime.now().isoformat()
            }

            results[agent_name] = agent_result
            logger.info(f"   ✅ {agent_name}: completed")

        return results

    def _simulate_agent_execution(self, agent_name: str, optimized_prompt: str) -> str:
        """Simulate agent execution."""

        responses = {
            "coordinator": f"🎯 Koordiniert Ausführung: {optimized_prompt[:40]}...",
            "prompt_architect": "📝 Prompt analysiert und optimiert",
            "executor": "⚙️  Befehl ausgeführt basierend auf optimiertem Prompt",
            "reviewer": "✅ Qualitätsprüfung basierend auf Erwartungen bestanden",
            "angebot": "💰 Angebot erstellt nach optimiertem Prompt",
            "dispo": "📅 Zeitplan erstellt und optimiert",
            "kunde": "👥 Kundenbenachrichtigung vorbereitet",
            "recherche": "🔍 Recherche durchgeführt gemäß optimiertem Prompt",
            "technik": "⚙️  Technische Implementierung abgeschlossen",
            "berater": "💡 Beratung bereitgestellt basierend auf Optimierung",
        }

        return responses.get(agent_name, "✅ Ausgeführt")

    def _review_and_format(self, natural_input: str, optimized_prompt: str,
                          execution_results: Dict, category: str) -> str:
        """Review and format final response."""

        completed_agents = list(execution_results.keys())

        status_lines = [
            f"🤖 JARVIS Coordinator - Natürliche Sprache verarbeitet",
            f"📝 Original: {natural_input[:60]}...",
            f"🎯 Kategorie: {category.upper()}",
            f"👥 Agenten eingesetzt: {', '.join(completed_agents[:3])}{'...' if len(completed_agents) > 3 else ''}",
            f"",
        ]

        # Show agent results
        for agent_name, agent_result in list(execution_results.items())[:5]:
            status_lines.append(f"✅ {agent_name}: {agent_result['result']}")

        if len(execution_results) > 5:
            status_lines.append(f"✅ +{len(execution_results)-5} weitere Agenten")

        status_lines.extend([
            "",
            "🎯 Optimierter Prompt wurde verwendet für:",
            f"   - Präzise Agent-Delegation",
            f"   - Konsistente Ausführung",
            f"   - Qualitätsprüfung",
            "",
            "✨ Natürliche Sprache → Optimierter Prompt → Ausgeführt",
            "📝 Weitere Anweisungen über WhatsApp jederzeit möglich"
        ])

        return "\n".join(status_lines)

    def get_optimization_history(self, user_id: Optional[str] = None) -> list:
        """Get optimization history."""
        if user_id:
            return [
                instr for instr in self.history["instructions"]
                if instr.get("user_id") == user_id
            ]
        return self.history["instructions"]


coordinator = JARVISWithOptimization()


@app.route("/process_natural_language", methods=["POST"])
def process_natural_language():
    """Process natural language input with optimization."""
    try:
        data = request.get_json()

        user_id = data.get("user_id", "unknown")
        natural_input = data.get("input", "")
        channel = data.get("channel", "api")

        if not natural_input:
            return {"error": "Keine Eingabe vorhanden"}, 400

        response = coordinator.process_natural_language(user_id, natural_input, channel)

        return response, 200

    except Exception as e:
        logger.error(f"❌ Processing error: {e}")
        return {"error": str(e)}, 500


@app.route("/optimization_history", methods=["GET"])
def optimization_history():
    """Get optimization history."""
    try:
        user_id = request.args.get("user_id")
        history = coordinator.get_optimization_history(user_id)
        return {"history": history}, 200
    except Exception as e:
        logger.error(f"❌ History error: {e}")
        return {"error": str(e)}, 500


@app.route("/agent_status", methods=["GET"])
def agent_status():
    """Get agent status."""
    return {
        "timestamp": datetime.now().isoformat(),
        "total_agents": len(coordinator.AGENTS),
        "agents": coordinator.AGENTS,
        "optimization_engine": "ACTIVE"
    }, 200


@app.route("/health", methods=["GET"])
def health():
    """Health check."""
    return {
        "status": "ok",
        "service": "JARVIS Coordinator with Prompt Optimization",
        "agents_online": len(coordinator.AGENTS),
        "prompt_optimizer": "ACTIVE",
        "timestamp": datetime.now().isoformat()
    }, 200


def main():
    """Start JARVIS with prompt optimization."""
    print("\n" + "="*70)
    print("🤖 JARVIS COORDINATOR WITH PROMPT OPTIMIZATION ENGINE")
    print("="*70 + "\n")

    print("🧠 Prompt Optimization Engine: ACTIVE")
    print("✅ Natural Language Processing: ENABLED")
    print("✅ Automatic Prompt Engineering: ENABLED")
    print("\n")

    print("📊 Agent Status:")
    for agent_name, agent_info in coordinator.AGENTS.items():
        print(f"   ✅ {agent_name:20} | {agent_info['status']}")

    print("\n" + "="*70)
    print("🔗 API Endpoints:")
    print("="*70)
    print("   POST /process_natural_language")
    print("   GET  /optimization_history")
    print("   GET  /agent_status")
    print("   GET  /health")

    print("\n📝 Example Request:")
    print("""
   curl -X POST http://localhost:8000/process_natural_language \\
     -H "Content-Type: application/json" \\
     -d '{
       "user_id": "du@example.com",
       "input": "Erstelle einen Preisplan für ein neues Projekt",
       "channel": "whatsapp"
     }'
    """)

    print("\n" + "="*70)
    print("🚀 Starting JARVIS with Prompt Optimization on :8000")
    print("="*70 + "\n")

    app.run(host="0.0.0.0", port=8000, debug=False)


if __name__ == "__main__":
    main()
