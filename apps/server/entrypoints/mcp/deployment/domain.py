"""Pure deployment-profile validation for the MCP entrypoint."""
from __future__ import annotations

from . import params


def validate_profile(
    profile: str,
    environment: str,
    demo_book_ids: frozenset[str],
    web_search_enabled: bool,
) -> None:
    """Reject unsupported or unsafe combinations before composing servers."""
    if profile not in params.MCP_PROFILES:
        raise ValueError("BD_MCP_PROFILE must be disabled, local_demo, or protected")
    if profile == "local_demo" and environment in params.PRODUCTION_ENVIRONMENTS:
        raise ValueError("BD_MCP_PROFILE=local_demo is forbidden in production")
    if profile == "local_demo" and not demo_book_ids:
        raise ValueError("BD_MCP_DEMO_BOOK_IDS is required for the local demo profile")
    if profile == "local_demo" and web_search_enabled:
        raise ValueError("Settings-backed Search MCP is disabled in the unauthenticated local demo profile")
