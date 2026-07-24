"""Gateway API entry point.

Boots the MCP connection + LLM provider, seeds the default admin user, and wires
the routers (grouped by tag for the Swagger docs at /docs).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai.mcp_client import MCPClient
from ai.runtime import runtime
from api import auth, cart, chat, checkout, health, reindex, session
from commerce import magento_token
from commerce.customer import CustomerError
from core import config
from core.log import logger
from db import users
from vector.qdrant_client import qdrant_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp = MCPClient(config.MCP_COMMAND, config.MCP_ARGS, magento_token.mcp_env())
    await mcp.start()
    runtime.mcp = mcp
    users.seed_admin()
    try:
        await qdrant_manager.connect()
    except Exception as e:  # noqa: BLE001 — Qdrant is additive; don't block chat if it's down
        logger.warning("qdrant unavailable, semantic search disabled: %s", e)
    logger.info("startup complete: %d MCP tools, model=%s", len(mcp.tool_names()), config.LLM_MODEL)
    try:
        yield
    finally:
        await qdrant_manager.disconnect()
        await mcp.close()


app = FastAPI(
    title="AI Commerce Assistant (POC)",
    description="Conversational commerce API — auth, chat, and cart.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.exception_handler(CustomerError)
async def _customer_error(_: Request, exc: CustomerError):
    # Customer token couldn't be minted (bad creds / password not cached after restart).
    return JSONResponse(status_code=401, content={"detail": str(exc)})

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(session.router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(reindex.router)
