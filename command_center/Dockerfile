# JARVIS Command Center — multi-stage: build the Vite frontend, serve it from the FastAPI backend.
# Build context is the repository root:  docker build -f command_center/Dockerfile .

# ── stage 1: frontend ────────────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /build
COPY command_center/frontend/package.json command_center/frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY command_center/frontend/ ./
# vite.config.ts writes to ../backend/static → redirect into /build/dist here
RUN npx tsc --noEmit -p tsconfig.json && npx vite build --outDir /build/dist --emptyOutDir

# ── stage 2: backend ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    JARVIS_CC_DATA_DIR=/data JARVIS_CC_WORKSPACE_DIR=/data/workspace \
    JARVIS_CC_STATIC_DIR=/app/command_center/backend/static JARVIS_CC_PORT=8080
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl tini && rm -rf /var/lib/apt/lists/* \
    && groupadd -r jarvis && useradd -r -g jarvis -d /app -s /usr/sbin/nologin jarvis
COPY command_center/requirements.txt /app/command_center/requirements.txt
RUN pip install -r /app/command_center/requirements.txt
# Only what the server needs from the repo: the command center and the shared core client/persona.
COPY command_center/backend /app/command_center/backend
COPY command_center/__init__.py /app/command_center/__init__.py
COPY core/__init__.py core/control_plane.py core/prompt.txt /app/core/
COPY config/__init__.py /app/config/
COPY --from=frontend /build/dist /app/command_center/backend/static
RUN mkdir -p /data && chown -R jarvis:jarvis /app /data
USER jarvis
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "command_center.backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
