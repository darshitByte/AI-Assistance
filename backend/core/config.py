"""Central configuration, loaded from the project .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/core/config.py -> parents[2] is the repo root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# --- LLM (NVIDIA-hosted, OpenAI-compatible API) ---
LLM_API_KEY = os.getenv("NVIDIA_API_KEY", "")
LLM_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# --- Magento / MCP ---
MAGENTO_BASE_URL = os.getenv("MAGENTO_BASE_URL", "")
MAGENTO_API_TOKEN = os.getenv("MAGENTO_API_TOKEN", "")  # legacy static token (smoke_test only)
# Admin creds → mint a fresh token on demand (admin JWTs expire ~hourly).
MAGENTO_ADMIN_USER = os.getenv("MAGENTO_ADMIN_USER", "")
MAGENTO_ADMIN_PASSWORD = os.getenv("MAGENTO_ADMIN_PASSWORD", "")
# Re-mint once the cached token is older than this (safely under the ~1h lifetime).
MAGENTO_AUTH_TTL = int(os.getenv("MAGENTO_AUTH_TTL", "2700"))

MCP_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "magento2-mcp" / "mcp-server.js"
MCP_COMMAND = "node"
MCP_ARGS = [str(MCP_SERVER_PATH)]
MCP_ENV = {
    "MAGENTO_BASE_URL": MAGENTO_BASE_URL,
    "MAGENTO_API_TOKEN": MAGENTO_API_TOKEN,
}

# --- Auth / MongoDB ---
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "grocerzy")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")

# --- Server ---
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
