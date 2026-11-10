"""Search service descriptors and bounded connection-check limits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProviderId = Literal[
    "coelho_search",
    "linkup",
    "you",
    "parallel",
    "exa",
    "geekflare",
    "tavily",
    "tinyfish",
    "firecrawl",
    "serper",
    "jina",
    "serpapi",
]


@dataclass(frozen = True, slots = True)
class SearchProvider:
    provider_id: ProviderId
    name: str
    endpoint: str | None
    account_url: str | None
    key_env: str
    required_search_tools: tuple[str, ...]
    key_required: bool = True


PROVIDERS: tuple[SearchProvider, ...] = (
    SearchProvider(
        provider_id = "coelho_search",
        name = "COELHO Search MCP",
        endpoint = None,
        account_url = None,
        key_env = "COELHO_SEARCH_API_KEY",
        required_search_tools = (),
        key_required = False,
    ),
    SearchProvider(
        provider_id = "linkup",
        name = "Linkup",
        endpoint = "https://api.linkup.so/v1/search",
        account_url = "https://app.linkup.so/",
        key_env = "LINKUP_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "you",
        name = "You.com",
        endpoint = "https://ydc-index.io/v1/search",
        account_url = "https://you.com/platform/api-keys",
        key_env = "YOUCOM_API_KEY",
        required_search_tools = (),
        key_required = False,
    ),
    SearchProvider(
        provider_id = "parallel",
        name = "Parallel",
        endpoint = "https://search.parallel.ai/mcp",
        account_url = "https://platform.parallel.ai/",
        key_env = "PARALLEL_SEARCH_MCP_API_KEY",
        required_search_tools = ("web_search",),
        key_required = False,
    ),
    SearchProvider(
        provider_id = "exa",
        name = "Exa",
        endpoint = "https://api.exa.ai/search",
        account_url = "https://dashboard.exa.ai/api-keys",
        key_env = "EXA_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "geekflare",
        name = "Geekflare",
        endpoint = "https://api.geekflare.com/search",
        account_url = "https://auth.geekflare.com/register?service=geekflare_api",
        key_env = "GEEKTFLARE_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "tavily",
        name = "Tavily",
        endpoint = "https://api.tavily.com/search",
        account_url = "https://app.tavily.com/home",
        key_env = "TAVILY_API_KEY",
        required_search_tools = (),
        key_required = False,
    ),
    SearchProvider(
        provider_id = "tinyfish",
        name = "TinyFish",
        endpoint = "https://api.search.tinyfish.ai/",
        account_url = "https://agent.tinyfish.ai/api-keys",
        key_env = "TINYFISH_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "firecrawl",
        name = "Firecrawl",
        endpoint = "https://api.firecrawl.dev/v1/search",
        account_url = "https://www.firecrawl.dev/app",
        key_env = "FIRECRAWL_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "serper",
        name = "Serper",
        endpoint = "https://google.serper.dev/search",
        account_url = "https://serper.dev/api-keys",
        key_env = "SERPER_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "jina",
        name = "Jina",
        endpoint = "https://s.jina.ai/",
        account_url = "https://jina.ai/api-dashboard/key-manager/",
        key_env = "JINA_API_KEY",
        required_search_tools = (),
    ),
    SearchProvider(
        provider_id = "serpapi",
        name = "SerpApi",
        endpoint = "https://serpapi.com/search.json",
        account_url = "https://serpapi.com/dashboard",
        key_env = "SERPAPI_API_KEY",
        required_search_tools = (),
    ),
)

MCP_CONNECT_TIMEOUT_S = 18.0
SEARCH_REQUEST_TIMEOUT_S = 12.0
MAX_DISCOVERED_TOOLS = 40
