#!/usr/bin/env python3
"""
Setup n8n Credential für lokale Qdrant-Vektor-Datenbank.

Verwendung:
    python3 setup_qdrant_credential.py --n8n-url http://localhost:3000 --api-key YOUR_API_KEY

Ohne --api-key: Script versucht automatisch, den API-Key aus .env oder config zu laden.
"""

import requests
import argparse
import sys
import json
from pathlib import Path


def load_env_var(name: str, default: str = None) -> str:
    """Try to load from .env file first, then from environment."""
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip('"\'')

    import os
    return os.getenv(name, default)


def setup_qdrant_credential(n8n_url: str, api_key: str, qdrant_host: str) -> dict:
    """Create Qdrant credential in n8n via API."""

    headers = {
        "Content-Type": "application/json",
        "X-N8N-API-KEY": api_key
    }

    # Qdrant Credential Payload
    credential_data = {
        "name": "Qdrant Local",
        "type": "qdrantApi",
        "data": {
            "host": qdrant_host,
            "port": 6333,
            "apiKey": "",  # Empty if no API key set in Qdrant
        }
    }

    url = f"{n8n_url}/api/v1/credentials"

    print(f"🔗 Connecting to n8n: {n8n_url}")
    print(f"📝 Creating credential: {credential_data['name']}")

    try:
        response = requests.post(url, json=credential_data, headers=headers, timeout=10)

        if response.status_code in [200, 201]:
            result = response.json()
            print(f"✅ Credential created successfully!")
            print(f"   ID: {result.get('id')}")
            print(f"   Name: {result.get('name')}")
            return result
        elif response.status_code == 401:
            print(f"❌ API Key invalid. Check your X-N8N-API-KEY.")
            sys.exit(1)
        elif response.status_code == 403:
            print(f"❌ Permission denied. Your API key may not have credential creation access.")
            sys.exit(1)
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"   Response: {response.text}")
            sys.exit(1)

    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to n8n at {n8n_url}")
        print(f"   Make sure n8n is running and accessible.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Setup n8n Credential für lokale Qdrant-Vektor-Datenbank"
    )
    parser.add_argument(
        "--n8n-url",
        default="http://localhost:3000",
        help="n8n server URL (default: http://localhost:3000)"
    )
    parser.add_argument(
        "--api-key",
        help="n8n API Key (or set N8N_API_KEY env var)"
    )
    parser.add_argument(
        "--qdrant-host",
        default="http://172.17.0.1",
        help="Qdrant host as reachable FROM the n8n container. Default is the "
             "docker0 bridge; 'http://localhost' only works if n8n runs on the host."
    )

    args = parser.parse_args()

    # Load API key from argument, env, or .env file
    api_key = args.api_key or load_env_var("N8N_API_KEY")

    if not api_key:
        print("❌ No API Key provided.")
        print("   Use: --api-key YOUR_KEY")
        print("   Or:  export N8N_API_KEY=YOUR_KEY")
        print("   Or:  N8N_API_KEY=... in .env file")
        sys.exit(1)

    print(f"🚀 Setting up Qdrant credential in n8n...\n")
    print(f"   Qdrant host (as seen by n8n): {args.qdrant_host}:6333")
    result = setup_qdrant_credential(args.n8n_url, api_key, args.qdrant_host)

    print(f"\n✨ Done! You can now use this credential in your n8n workflows.")


if __name__ == "__main__":
    main()
