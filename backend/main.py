"""Gateway API entry point.

Boots the MCP connection + LLM provider, seeds the default admin user, and wires
the routers (grouped by tag for the Swagger docs at /docs).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai.mcp_client import MCPClient
from ai.runtime import runtime
from api import auth, cart, chat, checkout, health, session
from commerce import magento_token
from core import config
from core.log import logger
from db import users


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp = MCPClient(config.MCP_COMMAND, config.MCP_ARGS, magento_token.mcp_env())
    await mcp.connect()
    runtime.mcp = mcp
    users.seed_admin()
    logger.info("startup complete: %d MCP tools, model=%s", len(mcp.tool_names()), config.LLM_MODEL)
    try:
        yield
    finally:
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

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(session.router)
app.include_router(cart.router)
app.include_router(checkout.router)
