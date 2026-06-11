"""Tool API-key management for FastMCP source tools.

Surfaces a list of OPTIONAL keys the user may supply via the global /settings
UI to unlock higher rate limits or extra features on third-party data sources
(Semantic Scholar, OpenAlex with API key, GitHub PAT, etc.). Stored encrypted
in the SAME MinIO+Fernet store as the LLM rotator's BYOK provider keys
(llm/credentials.enc) — different whitelist, same secure transport.
"""
from .router import router

__all__ = ["router"]
