#!/usr/bin/env python3
"""
Qdrant Cloud Setup - Autonomous Initialization
Creates collections and configures JARVIS for cloud-hosted Qdrant
"""

import os
import json
from pathlib import Path
from datetime import datetime

print("\n" + "="*60)
print("🌐 QDRANT CLOUD SETUP")
print("="*60 + "\n")

print("📋 Step 1: Qdrant Cloud Account Setup")
print("─" * 60)
print("""
Go to: https://qdrant.tech/
1. Click "Sign Up" (or Login if you have account)
2. Create account (free tier)
3. Create a new cluster (name: "mark-liii")
4. Copy the following:
   - Cluster URL (https://xxx.qdrant.io)
   - API Key
""")

# Get user input
cluster_url = input("\n🔗 Enter Cluster URL (https://xxx.qdrant.io): ").strip()
api_key = input("🔑 Enter API Key: ").strip()

if not cluster_url or not api_key:
    print("❌ Cluster URL and API Key required!")
    exit(1)

# Save to .env
env_file = Path("scripts/.env")
env_content = env_file.read_text() if env_file.exists() else ""

# Update or add Qdrant settings
lines = env_content.split('\n')
qdrant_url_found = False
qdrant_key_found = False

new_lines = []
for line in lines:
    if line.startswith('QDRANT_URL='):
        new_lines.append(f'QDRANT_URL={cluster_url}')
        qdrant_url_found = True
    elif line.startswith('QDRANT_API_KEY='):
        new_lines.append(f'QDRANT_API_KEY={api_key}')
        qdrant_key_found = True
    else:
        new_lines.append(line)

if not qdrant_url_found:
    new_lines.append(f'QDRANT_URL={cluster_url}')
if not qdrant_key_found:
    new_lines.append(f'QDRANT_API_KEY={api_key}')

env_file.write_text('\n'.join(new_lines))
print(f"\n✅ Saved to scripts/.env")

# Test connection
print("\n📋 Step 2: Testing Connection...")
print("─" * 60)

try:
    import requests

    headers = {
        "api-key": api_key,
        "Content-Type": "application/json"
    }

    response = requests.get(f"{cluster_url}/health", headers=headers, timeout=5)

    if response.status_code == 200:
        print("✅ Qdrant Cloud Connection: SUCCESS")
    else:
        print(f"⚠️  Status Code: {response.status_code}")
        print(f"   Response: {response.text}")

except Exception as e:
    print(f"❌ Connection Error: {e}")
    print("   Check URL and API Key")
    exit(1)

# Initialize collections
print("\n📋 Step 3: Creating Collections...")
print("─" * 60)

collections = [
    {
        "name": "documents",
        "vector_size": 1536,
        "distance": "Cosine"
    },
    {
        "name": "document_classes",
        "vector_size": 1536,
        "distance": "Cosine"
    }
]

for collection in collections:
    try:
        payload = {
            "vectors": {
                "size": collection["vector_size"],
                "distance": collection["distance"]
            }
        }

        response = requests.put(
            f"{cluster_url}/collections/{collection['name']}",
            headers=headers,
            json=payload,
            timeout=10
        )

        if response.status_code in [200, 201]:
            print(f"✅ Collection '{collection['name']}': Created")
        elif response.status_code == 409:
            print(f"ℹ️  Collection '{collection['name']}': Already exists")
        else:
            print(f"⚠️  Collection '{collection['name']}': {response.status_code}")

    except Exception as e:
        print(f"❌ Error creating '{collection['name']}': {e}")

# Save configuration
print("\n📋 Step 4: Saving Configuration...")
print("─" * 60)

config = {
    "qdrant_cloud": {
        "initialized": True,
        "cluster_url": cluster_url,
        "collections": ["documents", "document_classes"],
        "vector_size": 1536,
        "timestamp": datetime.now().isoformat()
    }
}

config_file = Path("qdrant_cloud_config.json")
with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print(f"✅ Configuration saved to {config_file}")

print("\n" + "="*60)
print("✨ QDRANT CLOUD SETUP COMPLETE!")
print("="*60)
print("""
Next Steps:
1. Update jarvis_coordinator_api.py to use cloud Qdrant
2. Test integration with JARVIS
3. Deploy n8n workflows to use cloud collections

Status: READY FOR PRODUCTION
""")
