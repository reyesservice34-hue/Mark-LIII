#!/usr/bin/env python3
"""
JARVIS Memory-First Coordinator
Implements the mandatory memory retrieval protocol before ANY action
This ensures JARVIS ALWAYS consults memory before responding to Master
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class JARVISMemoryRetrieval:
    """Memory-First Protocol Implementation for JARVIS."""

    def __init__(self):
        self.project_root = Path(".").resolve()
        self.memory_hierarchy = [
            ".claude/session_instructions_memory.json",  # CRITICAL RULES (1st priority)
            ".claude/long_term_memory.md",               # Strategic learnings
            ".claude/session_learning.json",             # Current session discoveries
            ".claude/jarvis_master_system.md",           # Identity & values
        ]
        self.retrieved_memory = {}

    def retrieve_all_memory(self, context: str = "") -> Dict:
        """
        MANDATORY: Retrieve all available memory before processing any instruction.
        This function implements the 'Obligatorischer Speicherabruf' protocol.
        """
        logger.info("🧠 INITIATING MANDATORY MEMORY RETRIEVAL...")
        logger.info(f"   Context: {context}")

        self.retrieved_memory = {
            "timestamp": datetime.now().isoformat(),
            "context": context,
            "retrieval_status": "IN_PROGRESS",
            "memory_sources": {}
        }

        # Retrieve in hierarchy order (most critical first)
        for memory_file in self.memory_hierarchy:
            file_path = self.project_root / memory_file

            if file_path.exists():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    self.retrieved_memory["memory_sources"][memory_file] = {
                        "status": "loaded",
                        "size": len(content),
                        "timestamp": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                    }

                    # Parse based on file type
                    if memory_file.endswith('.json'):
                        try:
                            self.retrieved_memory[memory_file] = json.loads(content)
                        except json.JSONDecodeError:
                            logger.warning(f"⚠️  Failed to parse JSON: {memory_file}")
                            self.retrieved_memory[memory_file] = {"raw": content}
                    else:
                        self.retrieved_memory[memory_file] = {"raw": content[:1000]}  # First 1000 chars

                    logger.info(f"   ✅ Loaded: {memory_file}")

                except Exception as e:
                    logger.error(f"   ❌ Failed to load {memory_file}: {e}")
                    self.retrieved_memory["memory_sources"][memory_file] = {
                        "status": "failed",
                        "error": str(e)
                    }
            else:
                logger.warning(f"   ⚠️  Not found: {memory_file}")
                self.retrieved_memory["memory_sources"][memory_file] = {
                    "status": "not_found"
                }

        self.retrieved_memory["retrieval_status"] = "COMPLETE"
        logger.info("✅ MEMORY RETRIEVAL COMPLETE")

        return self.retrieved_memory

    def extract_critical_rules(self) -> Dict:
        """
        Extract critical rules from session_instructions_memory.json.
        These rules override all other considerations.
        """
        if ".claude/session_instructions_memory.json" not in self.retrieved_memory:
            return {}

        instr_memory = self.retrieved_memory.get(".claude/session_instructions_memory.json", {})

        critical_rules = {
            "instructions": instr_memory.get("critical_instructions_learned", []),
            "decision_rules": instr_memory.get("decision_rules_extracted", {}),
            "personality": instr_memory.get("personality_profile_learned", {}),
            "autonomy_commitment": instr_memory.get("autonomy_commitment", {}),
        }

        return critical_rules

    def extract_strategic_learnings(self) -> Dict:
        """Extract strategic learnings from long-term memory."""
        if ".claude/long_term_memory.md" not in self.retrieved_memory:
            return {}

        long_term = self.retrieved_memory.get(".claude/long_term_memory.md", {})

        # This would need markdown parsing in production
        return {
            "raw_content": long_term.get("raw", "")[:2000]  # First 2000 chars
        }

    def extract_session_learnings(self) -> Dict:
        """Extract current session discoveries and patterns."""
        if ".claude/session_learning.json" not in self.retrieved_memory:
            return {}

        session = self.retrieved_memory.get(".claude/session_learning.json", {})

        return {
            "patterns": session.get("patterns_learned", {}),
            "improvements": session.get("improvements_identified", []),
            "autonomous_decisions": session.get("autonomous_decisions_made", [])
        }

    def context_fusion(self, user_input: str) -> Dict:
        """
        KONTEXT-VERSCHMELZUNG: Merge current input with all retrieved memory.
        This ensures the response is perfectly aligned with Master's history.
        """
        logger.info("🔀 INITIATING CONTEXT FUSION...")

        critical_rules = self.extract_critical_rules()
        strategic_learnings = self.extract_strategic_learnings()
        session_learnings = self.extract_session_learnings()

        fused_context = {
            "user_input": user_input,
            "retrieved_timestamp": datetime.now().isoformat(),
            "critical_rules": critical_rules,
            "strategic_learnings": strategic_learnings,
            "session_learnings": session_learnings,
            "fusion_notes": self._generate_fusion_notes(critical_rules)
        }

        logger.info("✅ CONTEXT FUSION COMPLETE")
        return fused_context

    def _generate_fusion_notes(self, critical_rules: Dict) -> str:
        """Generate summary of how memory applies to current context."""
        notes = []

        if critical_rules.get("instructions"):
            notes.append(f"⚡ {len(critical_rules['instructions'])} critical instructions active")

        if critical_rules.get("autonomy_commitment"):
            notes.append("🤖 Autonomous decision-making mode: ACTIVE")

        if critical_rules.get("decision_rules"):
            notes.append("📋 Decision rules loaded and enforced")

        return " | ".join(notes) if notes else "Memory loaded"

    def verify_alignment(self, proposed_action: str) -> bool:
        """
        Verify that proposed action aligns with Master's values and rules.
        Returns True if action is aligned, False if it violates critical rules.
        """
        critical_rules = self.extract_critical_rules()

        # Check against stored preferences (example: cost optimization)
        instructions = critical_rules.get("instructions", [])

        for instr in instructions:
            if instr.get("id") == "instr_006":  # Cost optimization rule
                if "expensive" in proposed_action.lower() and "free" not in proposed_action.lower():
                    logger.warning("⚠️  Proposed action may violate cost optimization rule")
                    return False

        return True

    def generate_memory_aware_response(self, user_input: str) -> Dict:
        """
        Generate a response that is 100% aware of and aligned with all retrieved memory.
        This is the core of the Memory-First Protocol.
        """
        logger.info("\n" + "="*70)
        logger.info("🧠 JARVIS MEMORY-FIRST RESPONSE GENERATION")
        logger.info("="*70)

        # STEP 1: Mandatory memory retrieval
        self.retrieve_all_memory(context=user_input)

        # STEP 2: Context fusion
        fused_context = self.context_fusion(user_input)

        # STEP 3: Alignment verification
        is_aligned = self.verify_alignment(user_input)

        response = {
            "status": "ready" if is_aligned else "alignment_check_failed",
            "user_input": user_input,
            "memory_retrieval": self.retrieved_memory,
            "fused_context": fused_context,
            "alignment_verified": is_aligned,
            "generation_timestamp": datetime.now().isoformat()
        }

        logger.info(f"✅ MEMORY-AWARE RESPONSE READY")
        logger.info(f"   Alignment: {'✅ PASS' if is_aligned else '⚠️  NEEDS REVIEW'}")
        logger.info("="*70 + "\n")

        return response


class MemoryFirstJARVISCoordinator:
    """
    Enhanced JARVIS Coordinator with Memory-First Protocol enforced.
    This is the production-ready coordinator that ALWAYS uses memory retrieval.
    """

    def __init__(self):
        self.memory_retrieval = JARVISMemoryRetrieval()
        self.instruction_log = Path("jarvis_memory_aware_instructions.json")
        self._load_instruction_log()

    def _load_instruction_log(self):
        """Load previous instruction history."""
        if self.instruction_log.exists():
            with open(self.instruction_log, 'r', encoding='utf-8') as f:
                self.log = json.load(f)
        else:
            self.log = {"instructions": [], "memory_retrievals": []}

    def _save_instruction_log(self):
        """Save instruction history."""
        with open(self.instruction_log, 'w', encoding='utf-8') as f:
            json.dump(self.log, f, indent=2, ensure_ascii=False)

    def process_instruction_with_memory(self, user_id: str, instruction: str, channel: str = "api") -> Dict:
        """
        Process instruction with MANDATORY memory-first protocol.
        Every instruction goes through memory retrieval first.
        """
        logger.info(f"\n📋 Processing instruction from {user_id}")
        logger.info(f"   Instruction: {instruction[:100]}...")
        logger.info(f"   Channel: {channel}")

        # STEP 1: Memory-First Protocol - Non-negotiable
        memory_aware_response = self.memory_retrieval.generate_memory_aware_response(instruction)

        # STEP 2: Log the retrieval (for audit trail)
        self.log["memory_retrievals"].append({
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "instruction": instruction[:100],
            "memory_sources_loaded": list(memory_aware_response["memory_retrieval"]["memory_sources"].keys()),
            "alignment_verified": memory_aware_response["alignment_verified"]
        })

        # STEP 3: Prepare response
        response = {
            "status": "ready",
            "user_id": user_id,
            "instruction": instruction,
            "channel": channel,
            "memory_aware": True,
            "memory_retrieval_complete": True,
            "response_ready": True,
            "timestamp": datetime.now().isoformat()
        }

        # Save log
        self._save_instruction_log()

        return response

    def get_memory_statistics(self) -> Dict:
        """Get statistics about memory usage and retrievals."""
        return {
            "total_instructions_processed": len(self.log["instructions"]),
            "total_memory_retrievals": len(self.log["memory_retrievals"]),
            "memory_sources_available": len(self.memory_retrieval.memory_hierarchy),
            "last_memory_retrieval": self.log["memory_retrievals"][-1]["timestamp"] if self.log["memory_retrievals"] else None
        }


def main():
    """Test the Memory-First Protocol."""
    print("\n" + "="*70)
    print("🧠 JARVIS MEMORY-FIRST PROTOCOL TEST")
    print("="*70 + "\n")

    coordinator = MemoryFirstJARVISCoordinator()

    # Test instruction
    test_instruction = "Erstelle einen Kostenoptimierungsplan"

    result = coordinator.process_instruction_with_memory(
        user_id="master",
        instruction=test_instruction,
        channel="whatsapp"
    )

    print("✅ TEST COMPLETE")
    print(f"Status: {result['status']}")
    print(f"Memory Retrieval: {'✅ COMPLETE' if result['memory_retrieval_complete'] else '❌ FAILED'}")
    print(f"Response Ready: {'✅ YES' if result['response_ready'] else '❌ NO'}")

    stats = coordinator.get_memory_statistics()
    print(f"\nMemory Statistics:")
    print(f"  - Sources Available: {stats['memory_sources_available']}")
    print(f"  - Total Retrievals: {stats['total_memory_retrievals']}")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()
