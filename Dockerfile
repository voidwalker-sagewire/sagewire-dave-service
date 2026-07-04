# DAVE — SageWire Vet AI Service
# Built to the SageWire Service Creation Playbook standard.
#
# IMPORTANT — Dave's brain (the vet-knowledge ChromaDB) is DATA, not code.
# It must live on a PERSISTENT VOLUME mounted at /data/herdmate_vet_db so it
# survives redeploys and reboots. The container reads that volume; it does not
# rebuild the knowledge base on every deploy.
#
# Coolify setup:
#   - Persistent Storage: mount a volume at  /data/herdmate_vet_db
#   - Environment vars:    ANTHROPIC_API_KEY, CREDENTIALS_FILE, (optional) CLAUDE_MODEL, RAG_SERVICE_URL
#   - Credentials:         mount credentials.json (service account) at /data/credentials.json
#   - Port Exposes:        5005

FROM python:3.11-slim

# System deps: build tools for chromadb/sentence-transformers wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model at build time so first request isn't slow
# and so the container doesn't need network to load the model at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# App code
COPY herdmate_vet_api.py .
COPY herdmate_vet_ingest.py .

# Dave reads its knowledge base from the persistent volume.
# CHROMA path defaults here point at the mounted volume.
ENV CHROMA_DB_PATH=/data/herdmate_vet_db
ENV CREDENTIALS_FILE=/data/credentials.json
ENV PORT=5005

EXPOSE 5005

CMD ["python", "herdmate_vet_api.py"]
