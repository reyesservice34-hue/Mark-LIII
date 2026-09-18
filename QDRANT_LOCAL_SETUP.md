# 🚀 Qdrant Local Setup & Collections Initialization

**Status:** Diese Cloud-Umgebung kann Qdrant nicht erreichen. Verwende diesen Guide für lokale Installation.

---

## **Option 1: Docker (Recommended - 1 Minute)**

```bash
# Starte Qdrant Docker Container
docker run -d -p 6333:6333 qdrant/qdrant:latest

# Überprüfe, ob Qdrant läuft
curl http://localhost:6333/health

# Output sollte sein:
# {"status":"ok"}
```

---

## **Option 2: Binary Installation (2 Minutes)**

```bash
# macOS
brew install qdrant

# Linux
curl https://api.github.com/repos/qdrant/qdrant/releases/latest \
  | grep browser_download_url | grep linux | cut -d '"' -f 4 | wget -qi -

# Windows
# Download von: https://github.com/qdrant/qdrant/releases

# Starten
qdrant
# Läuft auf localhost:6333
```

---

## **Option 3: Kubernetes (für Produktion)**

```bash
helm repo add qdrant https://qdrant.github.io/qdrant-helm
helm install qdrant qdrant/qdrant
```

---

## **Collections Initialization**

Sobald Qdrant läuft:

```bash
cd /home/user/Mark-LIII

# Starte init script
python3 scripts/init_qdrant_collections.py

# Output:
# ============================================================
# 🚀 QDRANT COLLECTION INITIALIZATION
# ============================================================
#
# 🎯 Qdrant Collection Initializer
#    Target: http://localhost:6333
#
# ✅ Qdrant connected
# 📋 Creating Collections:
#
# documents
#   Purpose: Store document embeddings for semantic search
#   Status: ✅ Active
#
# document_classes
#   Purpose: Store document classification embeddings
#   Status: ✅ Active
#
# conversation_history
#   Purpose: Store conversation context embeddings
#   Status: ✅ Active
#
# ================================================== ===========
# 📊 INITIALIZATION SUMMARY
# ================================================== ===========
# ✅ Successfully created: 3/3 collections
#    ✓ documents
#    ✓ document_classes
#    ✓ conversation_history
```

---

## **Collections Created**

| Name | Size | Purpose |
|------|------|---------|
| `documents` | 1536 | Store document embeddings for semantic search |
| `document_classes` | 1536 | Store document classification embeddings |
| `conversation_history` | 1536 | Store conversation context embeddings |

All use **Cosine Distance** metric for similarity search.

---

## **n8n Integration**

Sobald Collections erstellt:

1. **n8n Credential:**
   ```
   Type: HTTP
   Name: Qdrant Vector DB
   URL: http://localhost:6333
   ```

2. **Workflow Nodes:**
   ```
   Qdrant Search Node:
   - Collection: documents
   - Vector Size: 1536
   - Top K: 5
   - Threshold: 0.7
   ```

3. **Test Workflow:**
   ```
   Input (text)
     ↓
   Generate Embedding (OpenAI)
     ↓
   Qdrant Search (documents collection)
     ↓
   Return Results
   ```

---

## **Quick Commands**

```bash
# Check Qdrant health
curl http://localhost:6333/health

# List collections
curl http://localhost:6333/collections

# Delete collection (if needed)
curl -X DELETE http://localhost:6333/collections/documents

# Get collection info
curl http://localhost:6333/collections/documents
```

---

## **Troubleshooting**

### Port 6333 already in use
```bash
# Find process using port
lsof -i :6333

# Kill it
kill -9 <PID>

# Or use different port
docker run -d -p 6334:6333 qdrant/qdrant:latest
# Update scripts to use 6334
```

### Collection creation failed
```bash
# Check logs
docker logs <container_id>

# Recreate collection with retry
python3 scripts/init_qdrant_collections.py --retry 3
```

### Connection timeout
```bash
# Verify Qdrant is running
docker ps | grep qdrant

# Check if port is open
nc -zv localhost 6333
```

---

## **Performance Settings**

For production optimization:

```json
{
  "collection_config": {
    "vectors": {
      "size": 1536,
      "distance": "Cosine"
    },
    "quantization_config": {
      "scalar": {
        "type": "int8",
        "quantile": 0.99
      }
    },
    "hnsw_config": {
      "m": 16,
      "ef_construct": 200,
      "full_scan_threshold": 10000
    }
  }
}
```

---

## **Next Steps**

1. ✅ Install Qdrant locally
2. ✅ Run `python3 scripts/init_qdrant_collections.py`
3. ✅ Verify collections in n8n
4. ✅ Deploy workflows using Qdrant

---

**Status:** Ready for local deployment 🚀
