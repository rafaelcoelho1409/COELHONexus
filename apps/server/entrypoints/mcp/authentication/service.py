"""Authentication provider construction for the external MCP server."""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier

from . import params


def build_auth_provider(mcp_profile: str) -> JWTVerifier | None:
    """Create JWT verification only for the protected deployment profile."""
    if mcp_profile != "protected":
        return None
    required = {
        "BD_MCP_JWKS_URI": params.JWT_JWKS_URI,
        "BD_MCP_ISSUER": params.JWT_ISSUER,
        "BD_MCP_AUDIENCE": params.JWT_AUDIENCE,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "BD_MCP_PROFILE=protected requires " + ", ".join(missing),
        )
    return JWTVerifier(
        jwks_uri = params.JWT_JWKS_URI,
        issuer = params.JWT_ISSUER,
        audience = params.JWT_AUDIENCE,
        required_scopes = [params.JWT_REQUIRED_SCOPE] if params.JWT_REQUIRED_SCOPE else [],
        ssrf_safe = True,
    )
