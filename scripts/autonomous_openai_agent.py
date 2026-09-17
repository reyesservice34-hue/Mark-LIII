#!/usr/bin/env python3
"""
Autonomous OpenAI Agent for n8n Connector Analysis

Uses OpenAI's function calling to autonomously:
1. Connect to n8n
2. Load all connectors
3. Analyze workflows
4. Configure Qdrant
5. Generate status report

Requires: OPENAI_API_KEY, N8N_API_KEY environment variables
"""

import os
import sys
import json
import subprocess
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("❌ OpenAI library not found. Install with: pip install openai")
    sys.exit(1)


# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    print("❌ OPENAI_API_KEY environment variable not set")
    sys.exit(1)

client = OpenAI(api_key=openai_api_key)

# Define available tools/functions for OpenAI
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_n8n_connector_handler",
            "description": "Execute n8n connector handler script to analyze workflows and setup Qdrant",
            "parameters": {
                "type": "object",
                "properties": {
                    "n8n_url": {
                        "type": "string",
                        "description": "n8n server URL (default: http://localhost:3000)",
                        "default": "http://localhost:3000"
                    },
                    "api_key": {
                        "type": "string",
                        "description": "n8n API Key"
                    }
                },
                "required": ["api_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_status_report",
            "description": "Read and analyze the n8n_status.json report",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_workflow_config",
            "description": "Generate configuration recommendations for inactive Qdrant workflows",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of workflow IDs to configure"
                    }
                },
                "required": ["workflow_ids"]
            }
        }
    }
]


def run_n8n_connector_handler(n8n_url: str, api_key: str) -> dict:
    """Execute the n8n connector handler script."""
    print(f"🔄 Executing n8n connector handler...")
    try:
        result = subprocess.run(
            ["python3", "scripts/n8n_connector_handler.py",
             "--n8n-url", n8n_url,
             "--api-key", api_key],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0:
            print("✅ Script executed successfully")
            # Try to read the generated status file
            if Path("n8n_status.json").exists():
                with open("n8n_status.json") as f:
                    status = json.load(f)
                return {"success": True, "status": status, "output": result.stdout}
            else:
                return {"success": True, "output": result.stdout}
        else:
            print(f"❌ Script failed: {result.stderr}")
            return {"success": False, "error": result.stderr}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Script execution timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def read_status_report() -> dict:
    """Read the n8n_status.json report."""
    status_file = Path("n8n_status.json")
    if not status_file.exists():
        return {"success": False, "error": "n8n_status.json not found"}

    try:
        with open(status_file) as f:
            status = json.load(f)
        return {"success": True, "status": status}
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_workflow_config(workflow_ids: list) -> dict:
    """Generate configuration for workflows."""
    config = {
        "workflows": [],
        "recommendations": []
    }

    for wf_id in workflow_ids:
        config["workflows"].append({
            "id": wf_id,
            "recommended_nodes": [
                "Qdrant Vector Store",
                "OpenAI Embeddings",
                "Vector Search"
            ],
            "configuration_steps": [
                "1. Add Qdrant Vector Store node",
                "2. Connect to Qdrant Local credential",
                "3. Add OpenAI Embeddings node",
                "4. Configure collection name",
                "5. Test connection"
            ]
        })

    config["recommendations"].extend([
        "Enable workflow after configuration",
        "Test with sample data first",
        "Monitor execution logs",
        "Verify Qdrant collection creation"
    ])

    return {"success": True, "config": config}


def process_tool_call(tool_name: str, tool_input: dict) -> str:
    """Process tool calls from OpenAI."""
    print(f"\n🔧 Executing: {tool_name}")
    print(f"   Parameters: {json.dumps(tool_input, indent=2)}")

    if tool_name == "run_n8n_connector_handler":
        result = run_n8n_connector_handler(
            n8n_url=tool_input.get("n8n_url", "http://localhost:3000"),
            api_key=tool_input.get("api_key")
        )
    elif tool_name == "read_status_report":
        result = read_status_report()
    elif tool_name == "generate_workflow_config":
        result = generate_workflow_config(tool_input.get("workflow_ids", []))
    else:
        result = {"error": f"Unknown tool: {tool_name}"}

    return json.dumps(result)


def autonomous_agent_loop():
    """Run autonomous OpenAI agent loop."""

    n8n_api_key = os.getenv("N8N_API_KEY")
    if not n8n_api_key:
        print("❌ N8N_API_KEY environment variable not set")
        sys.exit(1)

    print("🤖 Starting Autonomous OpenAI Agent")
    print("="*60)

    # Initial system message
    system_message = """You are an autonomous AI agent for n8n infrastructure management.

Your task:
1. Execute the n8n connector handler to analyze all workflows
2. Identify inactive Qdrant-related workflows
3. Generate configuration recommendations
4. Create a comprehensive setup report

Use the available tools to complete this task autonomously.
When all information is gathered, provide a final summary."""

    # Initial user message
    user_message = f"""Execute the following task autonomously:

1. Run n8n connector handler with API key to analyze infrastructure
   - N8N_API_KEY: {n8n_api_key}
   - URL: http://localhost:3000

2. Read the generated status report

3. For any inactive Qdrant workflows, generate configuration recommendations

4. Provide a comprehensive summary of findings

Start by executing the n8n connector handler."""

    messages = [
        {"role": "user", "content": user_message}
    ]

    # Agentic loop
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        print(f"\n📍 Iteration {iteration}/{max_iterations}")

        # Call OpenAI with tools
        response = client.chat.completions.create(
            model="gpt-4",
            system=system_message,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"
        )

        # Check if we're done
        if response.stop_reason == "end_turn":
            print("\n✅ Agent completed task")
            print("\n" + "="*60)
            print("📋 FINAL REPORT")
            print("="*60)

            # Extract and print final message
            for msg in response.choices[0].message.content:
                if hasattr(msg, 'text'):
                    print(msg.text)

            break

        # Process tool calls
        if response.stop_reason == "tool_calls":
            assistant_message = response.choices[0].message
            messages.append({"role": "assistant", "content": assistant_message})

            # Process each tool call
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                tool_input = json.loads(tool_call.function.arguments)

                # Execute tool
                tool_result = process_tool_call(tool_name, tool_input)

                # Add tool result to messages
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call.id,
                            "content": tool_result
                        }
                    ]
                })

                print(f"✅ {tool_name} completed")
        else:
            # Unexpected response
            print(f"⚠️  Unexpected response: {response.stop_reason}")
            break

    print("\n" + "="*60)
    print("🎉 Autonomous Agent Finished")
    print("="*60)


if __name__ == "__main__":
    try:
        autonomous_agent_loop()
    except KeyboardInterrupt:
        print("\n\n⏹️  Agent interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
