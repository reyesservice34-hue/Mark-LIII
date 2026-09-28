#!/usr/bin/env python3
"""
Autonomous OpenAI Executor: Receives instructions, delegates to OpenAI, verifies results.
Part of the delegation chain: User → Claude → OpenAI → Verification → Done.
"""

import os
import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:
    print("❌ OpenAI library not installed. Install with: pip install openai")
    sys.exit(1)


class AutonomousOpenAIExecutor:
    """Delegates instructions to OpenAI and verifies execution."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("No OPENAI_API_KEY found")

        self.client = OpenAI(api_key=self.api_key)
        self.model = "gpt-4-turbo"
        self.execution_log = []

    def execute_instruction(self, instruction: str, context: Dict = None) -> Dict:
        """
        Execute a user instruction via OpenAI.

        Args:
            instruction: User's instruction (e.g., "Analyze n8n workflows and generate recommendations")
            context: Context data (files, previous results, etc.)

        Returns:
            Dict with execution results and verification status
        """
        result = {
            "instruction": instruction,
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
            "openai_response": None,
            "execution_output": None,
            "verification": None,
            "error": None
        }

        try:
            # Step 1: Send to OpenAI
            print(f"\n📤 Delegating to OpenAI: {instruction[:60]}...")

            system_prompt = """You are an autonomous executor.
- Analyze the instruction carefully
- Generate a complete, runnable solution
- Return Python code, bash scripts, or JSON as needed
- Be precise and assume the user will execute your output exactly
- Think step-by-step and include error handling
- Do not ask for confirmation - provide complete solutions
- Format code blocks with clear markers
- Include comments explaining non-obvious logic"""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": f"""Instruction: {instruction}

Context: {json.dumps(context or {}, indent=2)}

Provide a complete, executable solution. Include:
1. Code or commands to execute
2. Expected output/behavior
3. How to verify success"""
                    }
                ],
                system=system_prompt
            )

            openai_response = response.content[0].text
            result["openai_response"] = openai_response
            result["status"] = "openai_response_received"

            print(f"✅ OpenAI response received")

            # Step 2: Extract and execute code if present
            execution_result = self._extract_and_execute(openai_response)
            result["execution_output"] = execution_result

            # Step 3: Verify results
            verification = self._verify_execution(instruction, execution_result, openai_response)
            result["verification"] = verification
            result["status"] = "completed" if verification.get("passed") else "verification_failed"

            # Log execution
            self.execution_log.append(result)

            return result

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            self.execution_log.append(result)
            print(f"❌ Execution failed: {e}")
            return result

    def _extract_and_execute(self, response_text: str) -> Optional[str]:
        """Extract and execute code from OpenAI response."""
        # Look for code blocks
        if "```python" in response_text:
            code_block = response_text.split("```python")[1].split("```")[0].strip()
            try:
                print("🔄 Executing Python code from OpenAI response...")
                result = subprocess.run(
                    ["python3", "-c", code_block],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                output = result.stdout if result.returncode == 0 else result.stderr
                print(f"✅ Code executed: {output[:100] if output else 'No output'}")
                return output
            except Exception as e:
                print(f"⚠️  Code execution failed: {e}")
                return f"ERROR: {e}"

        elif "```bash" in response_text:
            bash_block = response_text.split("```bash")[1].split("```")[0].strip()
            try:
                print("🔄 Executing bash commands from OpenAI response...")
                result = subprocess.run(
                    bash_block,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                output = result.stdout if result.returncode == 0 else result.stderr
                print(f"✅ Bash executed: {output[:100] if output else 'No output'}")
                return output
            except Exception as e:
                print(f"⚠️  Bash execution failed: {e}")
                return f"ERROR: {e}"

        else:
            print("ℹ️  No executable code found, storing response as output")
            return response_text[:500]

    def _verify_execution(self, instruction: str, execution_output: str, openai_response: str) -> Dict:
        """Verify that execution matches the instruction intent."""
        verification = {
            "instruction": instruction,
            "checks": [],
            "passed": True,
            "notes": []
        }

        # Basic checks
        if execution_output and "ERROR" not in execution_output:
            verification["checks"].append("✅ No execution errors")
        else:
            verification["checks"].append("❌ Execution had errors")
            verification["passed"] = False

        if execution_output:
            verification["checks"].append("✅ Produced output")
        else:
            verification["checks"].append("⚠️  No output generated")

        # Intent matching
        if "analyze" in instruction.lower() and ("workflow" in execution_output.lower() or "analysis" in openai_response.lower()):
            verification["checks"].append("✅ Response contains analysis")
        elif "create" in instruction.lower() and ("created" in execution_output.lower() or "create" in openai_response.lower()):
            verification["checks"].append("✅ Creation-related output detected")

        return verification

    def batch_execute(self, instructions: List[str]) -> List[Dict]:
        """Execute multiple instructions in sequence."""
        results = []
        for instr in instructions:
            print(f"\n🎯 Executing: {instr[:60]}...")
            result = self.execute_instruction(instr)
            results.append(result)

            if result["status"] == "error":
                print(f"⚠️  Failed to execute: {instr}. Continuing with next...")

        return results

    def save_execution_log(self, filename: str = "autonomous_execution_log.json"):
        """Save execution log to file."""
        with open(filename, "w") as f:
            json.dump(self.execution_log, f, indent=2, default=str)
        print(f"📝 Execution log saved: {filename}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Autonomous OpenAI Executor")
    parser.add_argument("instruction", nargs="+", help="Instruction to execute")
    parser.add_argument("--api-key", help="OpenAI API Key (or OPENAI_API_KEY env)")
    parser.add_argument("--model", default="gpt-4-turbo", help="OpenAI model to use")
    parser.add_argument("--batch", action="store_true", help="Batch mode (multiple instructions)")

    args = parser.parse_args()

    instruction = " ".join(args.instruction)

    try:
        executor = AutonomousOpenAIExecutor(api_key=args.api_key)
        executor.model = args.model

        print("🤖 AUTONOMOUS OPENAI EXECUTOR")
        print("════════════════════════════════════════")

        result = executor.execute_instruction(instruction)

        print("\n" + "════════════════════════════════════════")
        print(f"Status: {result['status']}")
        if result['verification']:
            print(f"Verification: {'✅ PASSED' if result['verification']['passed'] else '❌ FAILED'}")
            for check in result['verification']['checks']:
                print(f"  {check}")

        executor.save_execution_log()

        return 0 if result['status'] in ['completed'] else 1

    except Exception as e:
        print(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
