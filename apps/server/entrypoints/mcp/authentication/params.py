"""JWT verification settings for the protected MCP profile."""
from __future__ import annotations

import os

JWT_JWKS_URI = os.environ.get("BD_MCP_JWKS_URI", "").strip()
JWT_ISSUER = os.environ.get("BD_MCP_ISSUER", "").strip()
JWT_AUDIENCE = os.environ.get("BD_MCP_AUDIENCE", "").strip()
# Scope checks belong to the capability that handles a request. Keep this optional
# for deployments that still require one endpoint-wide baseline scope.
JWT_REQUIRED_SCOPE = os.environ.get("BD_MCP_REQUIRED_SCOPE", "").strip()
