"""JWT verification settings for the protected MCP profile."""
from __future__ import annotations
import os


JWT_JWKS_URI = os.environ.get("BD_MCP_JWKS_URI", "").strip()
JWT_ISSUER = os.environ.get("BD_MCP_ISSUER", "").strip()
JWT_AUDIENCE = os.environ.get("BD_MCP_AUDIENCE", "").strip()
JWT_REQUIRED_SCOPE = os.environ.get("BD_MCP_REQUIRED_SCOPE", "bd.search.read").strip()
