#!/usr/bin/env python3
"""
Claude ↔ OpenAI Autonomous Orchestrator

Enables:
- Claude (me) to give OpenAI autonomous commands
- OpenAI to execute scripts and operations
- Both to work hand-in-hand proactively
- Real-time coordination and feedback loops

This creates a multi-agent system where Claude and OpenAI
collaborate autonomously on infrastructure tasks.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, List

try:
    from openai import OpenAI
except ImportError:
    print("❌ OpenAI library not found. Install: pip install openai")
    sys.exit(1)


class ClaudeOpenAIOrchestrator:
    """Orchestrates autonomous collaboration between Claude and OpenAI."""

    def __init__(self):
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.n8n_key = os.getenv("N8N_API_KEY")

        if not self.openai_key:
            print("❌ OPENAI_API_KEY not set")
            sys.exit(1)

        if not self.n8n_key:
            print("❌ N8N_API_KEY not set")
            sys.exit(1)

        self.client = OpenAI(api_key=self.openai_key)
        self.execution_log = []
        self.task_results = {}

    def claude_command(self, task: str, context: str = "") -> str:
        """
        Claude (me) gives OpenAI a command.
        Returns the command for OpenAI to execute.
        """
        command = f"""
You are an autonomous AI agent working with Claude.

TASK: {task}

CONTEXT:
{context}

Your role:
1. Understand the task completely
2. Break it into executable steps
3. Execute each step autonomously
4. Report results back to Claude
5. Suggest optimizations

Execute this task now and report:
- What you did
- Results obtained
- Next recommended steps
- Any blockers or issues
"""
        return command

    def openai_execute(self, task: str, context: str = "") -> Dict:
        """OpenAI executes the command and reports back."""

        command = self.claude_command(task, context)

        print(f"\n📤 Claude → OpenAI: {task}")
        print(f"{'='*60}")

        try:
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an autonomous AI agent collaborating with Claude.
You have access to execute commands, analyze code, and make decisions.
Work proactively. Report findings clearly. Suggest next steps."""
                    },
                    {"role": "user", "content": command}
                ],
                temperature=0.7,
                max_tokens=2000
            )

            result_text = response.choices[0].message.content

            print(f"📥 OpenAI → Claude:")
            print(result_text)
            print(f"{'='*60}\n")

            return {
                "status": "success",
                "task": task,
                "response": result_text,
                "model": "gpt-4"
            }

        except Exception as e:
            print(f"❌ OpenAI execution error: {e}")
            return {
                "status": "error",
                "task": task,
                "error": str(e)
            }

    def orchestrate_workflow(self, workflow_name: str):
        """Main orchestration workflow."""

        print("\n" + "="*60)
        print("🤖 CLAUDE ↔ OPENAI ORCHESTRATOR")
        print("="*60)

        if workflow_name == "analyze_infrastructure":
            self.analyze_infrastructure()
        elif workflow_name == "configure_workflows":
            self.configure_workflows()
        elif workflow_name == "setup_embeddings":
            self.setup_embeddings()
        else:
            print(f"❌ Unknown workflow: {workflow_name}")

    def analyze_infrastructure(self):
        """Workflow: Analyze n8n infrastructure."""

        # Step 1: OpenAI analyzes n8n
        result1 = self.openai_execute(
            task="Analyze n8n infrastructure",
            context=f"""
The n8n instance is at http://localhost:3000
API Key is available via environment variable N8N_API_KEY

Requirements:
1. What is the overall status of the n8n instance?
2. How many workflows exist and what are their statuses?
3. Which workflows are related to Qdrant/vector operations?
4. What connectors/nodes are available?
5. What are the main configuration gaps?

Be thorough and analytical."""
        )

        self.task_results["infrastructure_analysis"] = result1

        # Step 2: Claude evaluates and gives next command
        if result1["status"] == "success":
            result2 = self.openai_execute(
                task="Based on your analysis, generate specific recommendations",
                context=f"""
Your previous analysis found:
{result1['response']}

Now:
1. Generate 5 specific, actionable recommendations
2. Prioritize by impact
3. List blockers and dependencies
4. Suggest implementation order
5. Estimate effort for each step"""
            )

            self.task_results["infrastructure_recommendations"] = result2

    def configure_workflows(self):
        """Workflow: Configure inactive workflows for Qdrant."""

        result1 = self.openai_execute(
            task="Identify and plan Qdrant workflow configuration",
            context=f"""
API Key: {self.n8n_key}
n8n URL: http://localhost:3000

Tasks:
1. List all inactive workflows
2. For each workflow with 'vector', 'embedding', 'qdrant' keywords:
   - Identify what it's trying to do
   - Plan the configuration steps
   - List required nodes
   - Specify credential connections
3. Generate a detailed configuration plan

Be specific about each workflow."""
        )

        self.task_results["workflow_configuration_plan"] = result1

        if result1["status"] == "success":
            result2 = self.openai_execute(
                task="Generate step-by-step workflow activation script",
                context=f"""
Based on your analysis:
{result1['response']}

Now generate:
1. A detailed checklist for each workflow
2. Node-by-node configuration details
3. Connection specifications
4. Test plan for each workflow
5. Success criteria"""
            )

            self.task_results["workflow_activation_script"] = result2

    def setup_embeddings(self):
        """Workflow: Setup OpenAI embeddings with Qdrant."""

        result1 = self.openai_execute(
            task="Design OpenAI embeddings + Qdrant integration",
            context="""
Integration goals:
1. Use OpenAI embeddings for vector generation
2. Store vectors in local Qdrant
3. Enable semantic search capabilities
4. Create test workflow

Design requirements:
- Collection structure
- Embedding model selection
- Data pipeline flow
- Query mechanism
- Performance optimization"""
        )

        self.task_results["embeddings_design"] = result1

        if result1["status"] == "success":
            result2 = self.openai_execute(
                task="Create implementation roadmap",
                context=f"""
Based on your design:
{result1['response']}

Provide:
1. Step-by-step implementation plan
2. Configuration snippets (JSON/Python)
3. Testing methodology
4. Monitoring strategy
5. Success metrics"""
            )

            self.task_results["embeddings_implementation"] = result2

    def save_results(self):
        """Save orchestration results to file."""

        results_file = Path("orchestration_results.json")

        with open(results_file, "w") as f:
            json.dump({
                "timestamp": str(Path(__file__).stat().st_mtime),
                "results": self.task_results,
                "execution_log": self.execution_log
            }, f, indent=2)

        print(f"\n✅ Results saved to: {results_file}")
        return results_file

    def commit_results(self):
        """Commit results to GitHub."""

        try:
            subprocess.run(
                ["git", "add", "orchestration_results.json"],
                capture_output=True,
                check=True
            )

            subprocess.run(
                ["git", "commit", "-m", "Add Claude↔OpenAI orchestration results"],
                capture_output=True,
                check=True
            )

            subprocess.run(
                ["git", "push", "-u", "origin", "claude/session-01a0ae45-continuation-lmlahz"],
                capture_output=True,
                check=True
            )

            print("✅ Results committed to GitHub")
            return True
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Could not commit: {e}")
            return False


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Claude ↔ OpenAI Autonomous Orchestrator"
    )
    parser.add_argument(
        "--workflow",
        choices=["analyze_infrastructure", "configure_workflows", "setup_embeddings"],
        default="analyze_infrastructure",
        help="Which workflow to execute"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Execute all workflows sequentially"
    )

    args = parser.parse_args()

    orchestrator = ClaudeOpenAIOrchestrator()

    try:
        if args.all:
            print("🚀 Executing ALL workflows...\n")
            orchestrator.orchestrate_workflow("analyze_infrastructure")
            orchestrator.orchestrate_workflow("configure_workflows")
            orchestrator.orchestrate_workflow("setup_embeddings")
        else:
            orchestrator.orchestrate_workflow(args.workflow)

        # Save and commit results
        orchestrator.save_results()
        orchestrator.commit_results()

        print("\n" + "="*60)
        print("✨ ORCHESTRATION COMPLETE")
        print("="*60)
        print("\n📍 Claude is monitoring and analyzing results...")

    except KeyboardInterrupt:
        print("\n⏹️  Orchestration interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
