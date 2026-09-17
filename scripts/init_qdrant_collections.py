#!/usr/bin/env python3
"""
Proactive Qdrant Collection Initialization
Autonomously creates and configures collections for semantic search
"""

import os
import sys
import json
import requests
from pathlib import Path
from typing import Dict


class QdrantCollectionInit:
    """Autonomously initialize Qdrant collections."""

    def __init__(self, host: str = "http://172.17.0.1", port: int = 6333):
        self.host = host
        self.port = port
        self.base_url = f"{host}:{port}"
        self.session = requests.Session()

    def health_check(self) -> bool:
        """Check if Qdrant is running."""
        try:
            resp = self.session.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(f"❌ Qdrant health check failed: {e}")
            return False

    def collection_exists(self, collection_name: str) -> bool:
        """Check if collection already exists."""
        try:
            resp = self.session.get(
                f"{self.base_url}/collections/{collection_name}",
                timeout=5
            )
            return resp.status_code == 200
        except Exception:
            return False

    def create_collection(
        self,
        name: str,
        vector_size: int = 1536,
        distance: str = "Cosine"
    ) -> bool:
        """Create a new collection with embeddings configuration."""
        if self.collection_exists(name):
            print(f"ℹ️  Collection '{name}' already exists")
            return True

        try:
            payload = {
                "vectors": {
                    "size": vector_size,
                    "distance": distance,
                    "on_disk": True
                },
                "optimizers_config": {
                    "default_segment_number": 2,
                    "snapshot_every_sec": 600
                },
                "wal_config": {
                    "wal_capacity_mb": 32,
                    "wal_segments_ahead": 0
                }
            }

            resp = self.session.put(
                f"{self.base_url}/collections/{name}",
                json=payload,
                timeout=10
            )

            if resp.status_code in [200, 201]:
                print(f"✅ Collection '{name}' created successfully")
                return True
            else:
                print(f"❌ Failed to create collection '{name}': {resp.status_code}")
                print(f"   Response: {resp.text}")
                return False

        except Exception as e:
            print(f"❌ Error creating collection '{name}': {e}")
            return False

    def setup_payload_index(self, collection_name: str) -> bool:
        """Setup payload indexes for efficient filtering."""
        try:
            indexes = [
                {"field_name": "source", "field_schema": "keyword"},
                {"field_name": "timestamp", "field_schema": "integer"},
                {"field_name": "category", "field_schema": "keyword"}
            ]

            for index in indexes:
                resp = self.session.put(
                    f"{self.base_url}/collections/{collection_name}/index",
                    json=index,
                    timeout=5
                )
                if resp.status_code != 200:
                    print(f"⚠️  Could not create index {index['field_name']}")

            print(f"✅ Payload indexes configured for '{collection_name}'")
            return True

        except Exception as e:
            print(f"⚠️  Error setting up indexes: {e}")
            return False

    def verify_collection(self, collection_name: str) -> Dict:
        """Verify collection structure and readiness."""
        try:
            resp = self.session.get(
                f"{self.base_url}/collections/{collection_name}",
                timeout=5
            )

            if resp.status_code == 200:
                data = resp.json()
                config = data.get("result", {})
                return {
                    "name": collection_name,
                    "status": "ready",
                    "vector_size": config.get("config", {}).get("params", {}).get("vectors", {}).get("size"),
                    "points_count": config.get("points_count", 0),
                    "segments_count": config.get("segments_count", 0)
                }
            else:
                return {"name": collection_name, "status": "not_found"}

        except Exception as e:
            return {"name": collection_name, "status": "error", "error": str(e)}

    def initialize_all(self) -> Dict:
        """Complete initialization of all collections."""
        results = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "qdrant_host": self.base_url,
            "health": False,
            "collections_initialized": [],
            "collections_verified": []
        }

        print("\n" + "="*60)
        print("🚀 QDRANT COLLECTION INITIALIZATION")
        print("="*60 + "\n")

        # Health check
        print(f"🔍 Checking Qdrant at {self.base_url}...")
        if not self.health_check():
            print("❌ Qdrant is not reachable!")
            results["health"] = False
            return results

        print(f"✅ Qdrant is healthy\n")
        results["health"] = True

        # Collections to create
        collections = [
            {
                "name": "documents",
                "description": "Main document embeddings collection for semantic search"
            },
            {
                "name": "document_classes",
                "description": "Document category embeddings for classification"
            },
            {
                "name": "search_cache",
                "description": "Cached search results for performance optimization"
            }
        ]

        # Create collections
        print("📦 Creating collections...")
        for col in collections:
            if self.create_collection(col["name"]):
                self.setup_payload_index(col["name"])
                results["collections_initialized"].append(col["name"])
            print()

        # Verify collections
        print("✓ Verifying collections...")
        for col_name in results["collections_initialized"]:
            verification = self.verify_collection(col_name)
            results["collections_verified"].append(verification)
            print(f"  ✅ {col_name}: {verification.get('status')}")
            if verification.get("points_count"):
                print(f"     Points: {verification['points_count']}")

        print("\n" + "="*60)
        print(f"✨ INITIALIZATION COMPLETE")
        print("="*60 + "\n")

        return results


def main():
    """Run collection initialization autonomously."""
    initializer = QdrantCollectionInit(
        host=os.getenv("QDRANT_HOST", "http://172.17.0.1"),
        port=int(os.getenv("QDRANT_PORT", "6333"))
    )

    results = initializer.initialize_all()

    # Save results
    output_file = Path("qdrant_collections_init_results.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"📄 Results saved: {output_file}")

    # Summary
    if results["health"] and len(results["collections_initialized"]) > 0:
        print("✅ Collections ready for use!")
        return 0
    else:
        print("❌ Initialization incomplete - check Qdrant connection")
        return 1


if __name__ == "__main__":
    sys.exit(main())
