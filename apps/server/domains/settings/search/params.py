"""Search MCP provider descriptors and bounded connection-check limits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProviderId = Literal["coelho_search", "tavily", "exa", "jina"]


@dataclass(frozen = True, slots = True)
class SearchProvider:
    provider_id: ProviderId
    name: str
    endpoint: str | None
    key_env: str
    required_search_tools: tuple[str, ...]
    requires_search_key: bool = False


PROVIDERS: tuple[SearchProvider, ...] = (
    SearchProvider(
        provider_id = "coelho_search",
        name = "COELHO Search MCP",
        endpoint = None,
        key_env = "COELHO_SEARCH_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "tavily",
        name = "Tavily",
        endpoint = "https://mcp.tavily.com/mcp/",
        key_env = "TAVILY_API_KEY",
        required_search_tools = ("tavily_search", "tavily-search"),
        requires_search_key = True,
    ),
    SearchProvider(
        provider_id = "exa",
        name = "Exa",
        endpoint = "https://mcp.exa.ai/mcp",
        key_env = "EXA_API_KEY",
        required_search_tools = ("web_search_exa",),
    ),
    SearchProvider(
        provider_id = "jina",
        name = "Jina",
        endpoint = "https://mcp.jina.ai/v1?include_tags=search,read&exclude_tools=search_images,search_ssrn",
        key_env = "JINA_API_KEY",
        required_search_tools = ("search_web",),
        requires_search_key = True,
    ),
)

MCP_CONNECT_TIMEOUT_S = 18.0
MAX_DISCOVERED_TOOLS = 40
