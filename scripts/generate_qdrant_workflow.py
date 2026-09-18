#!/usr/bin/env python3
"""
Generate n8n workflow JSON for Qdrant + OpenAI Embeddings integration.
Autonomous workflow generation without external API calls.
"""

import json
from datetime import datetime


def generate_semantic_search_workflow():
    """Generate complete n8n workflow for semantic search with Qdrant."""

    workflow = {
        "name": "Semantic Search with Qdrant + OpenAI Embeddings",
        "active": False,
        "nodes": [
            {
                "parameters": {},
                "id": "uuid.v4()",
                "name": "When Called",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [250, 300],
                "webhookId": "workflow-start"
            },
            {
                "parameters": {
                    "promptType": "define",
                    "text": "=You are a document processor. Extract the query or document content from the input."
                },
                "id": "uuid.v4()",
                "name": "Process Input",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [450, 300]
            },
            {
                "parameters": {
                    "model": "text-embedding-3-small",
                    "text": "={{ $json.content }}",
                    "resource": "openai",
                    "credentials": "openai_api_key"
                },
                "id": "uuid.v4()",
                "name": "Generate Embeddings",
                "type": "n8n-nodes-openai.embeddings",
                "typeVersion": 1,
                "position": [650, 300]
            },
            {
                "parameters": {
                    "connectionType": "apiKey",
                    "credentials": "qdrant_credential",
                    "collectionName": "documents",
                    "vectorField": "vector",
                    "queryVector": "={{ $json.embedding }}",
                    "limit": 5,
                    "scoreThreshold": 0.7
                },
                "id": "uuid.v4()",
                "name": "Search Qdrant",
                "type": "n8n-nodes-base.qdrant",
                "typeVersion": 1,
                "position": [850, 300]
            },
            {
                "parameters": {
                    "mappingMode": "defineBelow",
                    "mapping": {
                        "results": "={{ $json.results }}",
                        "count": "={{ $json.results.length }}",
                        "totalScore": "={{ $json.results.reduce((s, r) => s + r.score, 0) }}"
                    }
                },
                "id": "uuid.v4()",
                "name": "Format Results",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3,
                "position": [1050, 300]
            },
            {
                "parameters": {
                    "statusCode": 200,
                    "responseBody": "={{ JSON.stringify({ results: $json.results, count: $json.count, score: $json.totalScore }) }}"
                },
                "id": "uuid.v4()",
                "name": "Return Response",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [1250, 300]
            },
            {
                "parameters": {
                    "deploymentUrl": "http://localhost:3000",
                    "credentials": "n8n_api_key",
                    "operation": "activate",
                    "workflowId": "{{ $json.workflowId }}"
                },
                "id": "uuid.v4()",
                "name": "Auto Activate",
                "type": "n8n-nodes-base.n8n",
                "typeVersion": 1,
                "position": [1250, 450]
            }
        ],
        "connections": {
            "When Called": {
                "main": [[{"node": "Process Input", "type": "main", "index": 0}]]
            },
            "Process Input": {
                "main": [[{"node": "Generate Embeddings", "type": "main", "index": 0}]]
            },
            "Generate Embeddings": {
                "main": [[{"node": "Search Qdrant", "type": "main", "index": 0}]]
            },
            "Search Qdrant": {
                "main": [[{"node": "Format Results", "type": "main", "index": 0}]]
            },
            "Format Results": {
                "main": [[{"node": "Return Response", "type": "main", "index": 0}]]
            }
        },
        "settings": {
            "timezone": "Europe/Berlin",
            "errorHandler": "retry"
        },
        "staticData": {
            "generatedAt": datetime.now().isoformat(),
            "version": "1.0",
            "description": "Semantic search pipeline with Qdrant vector DB and OpenAI embeddings"
        }
    }

    return workflow


def generate_document_classification_workflow():
    """Generate workflow for document classification with embeddings."""

    workflow = {
        "name": "Document Classification with Embeddings",
        "active": False,
        "nodes": [
            {
                "parameters": {},
                "id": "uuid.v4()",
                "name": "Input",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [250, 300]
            },
            {
                "parameters": {
                    "model": "text-embedding-3-small",
                    "text": "={{ $json.document }}"
                },
                "id": "uuid.v4()",
                "name": "Embed Document",
                "type": "n8n-nodes-openai.embeddings",
                "typeVersion": 1,
                "position": [450, 300]
            },
            {
                "parameters": {
                    "connectionType": "apiKey",
                    "credentials": "qdrant_credential",
                    "collectionName": "document_classes",
                    "queryVector": "={{ $json.embedding }}",
                    "limit": 1
                },
                "id": "uuid.v4()",
                "name": "Find Category",
                "type": "n8n-nodes-base.qdrant",
                "typeVersion": 1,
                "position": [650, 300]
            },
            {
                "parameters": {
                    "responseBody": "={{ { category: $json.results[0]?.category, confidence: $json.results[0]?.score } }}"
                },
                "id": "uuid.v4()",
                "name": "Output",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [850, 300]
            }
        ],
        "connections": {
            "Input": {
                "main": [[{"node": "Embed Document", "type": "main", "index": 0}]]
            },
            "Embed Document": {
                "main": [[{"node": "Find Category", "type": "main", "index": 0}]]
            },
            "Find Category": {
                "main": [[{"node": "Output", "type": "main", "index": 0}]]
            }
        }
    }

    return workflow


def main():
    print("🔧 Generating n8n Qdrant + OpenAI Workflows...")
    print("════════════════════════════════════════════════════════════════\n")

    workflows = {
        "semantic_search": generate_semantic_search_workflow(),
        "document_classification": generate_document_classification_workflow()
    }

    # Save workflows
    for name, workflow in workflows.items():
        filename = f"qdrant_{name}_workflow.json"
        with open(filename, "w") as f:
            json.dump(workflow, f, indent=2)
        print(f"✅ Generated: {filename}")
        print(f"   Name: {workflow['name']}")
        print(f"   Nodes: {len(workflow['nodes'])}")
        print()

    # Save combined configuration
    config = {
        "generated_at": datetime.now().isoformat(),
        "workflows": workflows,
        "qdrant_config": {
            "host": "172.17.0.1",
            "port": 6333,
            "collections": ["documents", "document_classes"]
        },
        "openai_config": {
            "model": "text-embedding-3-small",
            "embedding_dimension": 1536,
            "max_tokens_per_call": 8191
        },
        "n8n_setup": {
            "qdrant_credential_name": "Qdrant Local",
            "openai_credential_name": "OpenAI API",
            "webhook_auth": "required",
            "timeout_seconds": 30
        }
    }

    with open("qdrant_workflows_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("✅ Configuration saved: qdrant_workflows_config.json")
    print("\n════════════════════════════════════════════════════════════════")
    print("📋 SETUP INSTRUCTIONS")
    print("════════════════════════════════════════════════════════════════")
    print("""
1. In n8n UI:
   - Ensure Qdrant credential "Qdrant Local" exists (host: 172.17.0.1:6333)
   - Ensure OpenAI credential exists (API key configured)

2. Import workflows:
   - qdrant_semantic_search_workflow.json
   - qdrant_document_classification_workflow.json

3. Activate collections in Qdrant:
   - Create collection "documents" (vector_size: 1536)
   - Create collection "document_classes" (vector_size: 1536)

4. Test workflows:
   - Semantic Search: Send query with document content
   - Document Classification: Send document for auto-categorization

5. Monitor performance:
   - Check n8n execution logs
   - Verify Qdrant collection sizes
   - Monitor OpenAI API usage
""")


if __name__ == "__main__":
    main()
