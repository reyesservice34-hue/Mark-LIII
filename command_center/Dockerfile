# JARVIS Command Center — multi-stage: build the Vite frontend, serve it from the FastAPI backend.
# Build context is the repository root:  docker build -f command_center/Dockerfile .

# ── stage 1: frontend ────────────────────────────────────────────────────
# bookworm statt alpine: die Laufzeit unten ist glibc, und nur so lässt sich
# die Node-Werkzeugkette später dorthin kopieren, damit JARVIS sein eigenes
# Dashboard neu bauen kann.
FROM node:22-bookworm-slim AS frontend
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
# git gehört dazu, seit er Repositories in den Arbeitsbereich klonen kann.
# Ohne das meldet repo.clone ehrlich, dass es fehlt — aber dann fehlt es eben.
RUN apt-get update && apt-get install -y --no-install-recommends curl tini git && rm -rf /var/lib/apt/lists/* \
    && groupadd -r jarvis && useradd -r -g jarvis -d /app -s /usr/sbin/nologin jarvis
COPY command_center/requirements.txt /app/command_center/requirements.txt
RUN pip install -r /app/command_center/requirements.txt
# Only what the server needs from the repo: the command center and the shared core client/persona.
COPY command_center/backend /app/command_center/backend
COPY command_center/__init__.py /app/command_center/__init__.py
COPY core/__init__.py core/control_plane.py core/prompt.txt /app/core/
COPY config/__init__.py /app/config/
# Der Kalenderkern des Desktops: reine Standardbibliothek, und die einzige
# Stelle, an der die Regeln fürs Datumslesen und der lokale Speicher stehen.
# Ohne ihn meldete der lokale Kalender im Dashboard "core not importable" und
# war offline — ein Termin ohne Google-Konto ging damit gar nicht.
COPY plugins/__init__.py plugins/_calendar_core.py /app/plugins/
# Das Hauptgedächtnis: was JARVIS über diese Anlage weiß. Beim ersten Start
# wandert es in den Wissensspeicher, danach wird es dort gepflegt und nicht
# mehr überschrieben. Ohne diese Zeile läge es im Repository und wäre für ihn
# unsichtbar — er liest keine Datei, die niemand in seinen Systemtext stellt.
COPY .claude/HAUPTGEDAECHTNIS.md .claude/STEHENDE_ANWEISUNGEN.md /app/.claude/
COPY --from=frontend /build/dist /app/command_center/backend/static

# ── sich selbst umbauen können ───────────────────────────────────────────
# Ohne Node und die Frontend-Quellen im Laufzeit-Image kann er das Dashboard
# zwar ändern, aber nicht wirksam machen: eine Änderung an .tsx braucht einen
# Build, keinen Neustart. Das kostet rund 300 MB Image — der Preis dafür, dass
# „er baut sich selbst um" wahr ist und nicht bloß behauptet.
# Wer das nicht will:  --build-arg JARVIS_CC_SELF_BUILD=false
ARG JARVIS_CC_SELF_BUILD=true
ENV JARVIS_CC_SELF_BUILD=${JARVIS_CC_SELF_BUILD}
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=frontend /build /app/command_center/frontend
RUN if [ "$JARVIS_CC_SELF_BUILD" != "true" ]; then \
        rm -rf /usr/local/bin/node /usr/local/lib/node_modules /app/command_center/frontend; \
    else \
        ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm; \
    fi

# Ein echter Browser auf dem Server. Aus, weil Chromium mehrere hundert MB
# kostet und die meisten Seiten mit web.fetch gelesen sind. Wer ihn braucht:
#   --build-arg JARVIS_CC_BROWSER=true
# Ohne ihn melden die browser.*-Werkzeuge ehrlich, was fehlt.
ARG JARVIS_CC_BROWSER=false
ENV JARVIS_CC_BROWSER=${JARVIS_CC_BROWSER}
RUN if [ "$JARVIS_CC_BROWSER" = "true" ]; then \
        pip install playwright==1.56.0 \
        && playwright install --with-deps chromium; \
    fi

# Welcher Stand im Bild steckt. Ohne das ist von außen nicht zu sehen, ob ein
# `git pull` auf dem Server durchging — ein misslungener sieht genauso aus wie
# ein geglückter, und man sucht den Fehler dann in der Anwendung.
ARG JARVIS_CC_BUILD=""
ENV JARVIS_CC_BUILD=${JARVIS_CC_BUILD}

RUN mkdir -p /data && chown -R jarvis:jarvis /app /data
USER jarvis
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "command_center.backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
