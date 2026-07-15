# Backend API. Needs Python (FastAPI) AND Node — the backend spawns the Bold
# magento2-mcp server (a Node stdio process) as a subprocess.
# Build context is the PROJECT ROOT (see docker-compose.yml).
FROM python:3.12-slim

# Node.js for the MCP server. Ensure a `node` binary exists (config uses "node").
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && ln -sf "$(command -v nodejs)" /usr/local/bin/node \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Node deps for the MCP server (path mirrors config.py: <root>/mcp-servers/...)
COPY mcp-servers/ mcp-servers/
RUN cd mcp-servers/magento2-mcp && npm install --omit=dev

# App code
COPY backend/ backend/

EXPOSE 8000
CMD ["uvicorn", "main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
