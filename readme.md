# AI Commerce Assistant — Proof of Concept

A chat layer that lets a customer discover products in plain language. This POC
wires a **Python (FastAPI) backend** to the **Bold `magento2-mcp` server** so an
LLM (Claude) can search a Magento catalogue from a conversation.

Scope of this POC: **product search only** (find → recommend). Cart/checkout are
future work — Bold's MCP doesn't cover them.

## Architecture

```
Browser chat  ──HTTP──>  FastAPI (/chat)  ──>  Orchestrator ──> Claude (Anthropic)
                                                     │  tool calls
                                                     ▼
                                          MCP client (stdio)
                                                     │
                                          Bold magento2-mcp (Node)
                                                     │  REST
                                                     ▼
                                                 Magento
```

Backend is organised by layer (Swagger at `/docs`, grouped by tag):

```
backend/
├── main.py            # FastAPI app: lifespan, CORS, router wiring
├── api/               # routes, grouped by Swagger tag
│   ├── auth.py        #   /auth/*   (signup, login)
│   ├── chat.py        #   /chat, /allmessage
│   ├── cart.py        #   /cart/*
│   ├── health.py      #   /health
│   └── deps.py        #   current_user (JWT) dependency
├── ai/                # LLM + orchestration
│   ├── llm.py         #   pluggable provider (NVIDIA Nemotron)
│   ├── orchestrator.py#   the tool-use loop (model ⇄ MCP + cart tools)
│   ├── mcp_client.py  #   Commerce Connector boundary (stdio MCP client)
│   ├── memory.py      #   LangChain per-user conversation buffer
│   └── runtime.py     #   holds the live MCP + LLM instances
├── prompts/system.py  # system prompt (Langfuse/LangSmith later)
├── db/                # MongoDB: mongo.py, users.py, messages.py
├── commerce/          # Magento REST: magento.py, cart.py (guest cart)
└── core/              # config.py, security.py (JWT), log.py
```

- **`frontend/`** — React (Vite) chat app, runs as its own dev server.
- **`mcp-servers/magento2-mcp/`** — cloned Bold MCP server (Node).

## Setup

The Docker build installs the Python and Node (MCP server) dependencies itself —
you only need a filled-in `.env`:

```bash
cp .env.example .env    # then fill in the values below
```

- `NVIDIA_API_KEY` — LLM provider key (NVIDIA-hosted Nemotron).
- `MAGENTO_BASE_URL` — **must** include the `/rest/V1` suffix.
- `MAGENTO_ADMIN_USER` / `MAGENTO_ADMIN_PASSWORD` — admin login. The backend mints
  the Magento token on demand and refreshes it automatically (no manual token).

## Run with Docker (three containers)

Needs Docker + Compose, and a filled-in `.env` (see Setup step 3).

```bash
docker compose up --build
```

- **frontend** (nginx serving the React build) → http://localhost:3000
- **backend** (FastAPI + the Node MCP server) → http://localhost:8000
- **mongo** (user accounts) → internal, port 27017

Sign in with the seeded demo account **admin / admin@123**, or create a new
account from the login screen. Auth uses JWTs; each user gets their own cart and
conversation memory.

The backend image bundles Node so it can run the Bold MCP server. `.env` is
injected at runtime (not baked into the image). To change where the browser
calls the API, set the `VITE_API_BASE` build arg in `docker-compose.yml`.
Stop with `docker compose down`.

`GET http://localhost:8000/health` lists the model and MCP tools.

## Notes

- Bold's MCP is **admin-token** auth and covers **search + analytics only**. It's a
  fine fit for the search leg; the full purchase flow will need our own MCP tools.
- Model is NVIDIA-hosted Nemotron; change `NVIDIA_MODEL` in `.env`.
