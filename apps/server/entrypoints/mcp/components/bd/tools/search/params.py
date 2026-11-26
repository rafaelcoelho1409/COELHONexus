"""Configuration and resource limits for BD Search MCP."""
from __future__ import annotations

import os

MCP_PROFILE = os.environ.get("BD_MCP_PROFILE", "disabled").strip().casefold()


DEMO_BOOK_IDS = frozenset(
    value.strip()
    for value in os.environ.get("BD_MCP_DEMO_BOOK_IDS", "").split(",")
    if value.strip()
)
WEB_SEARCH_ENABLED = os.environ.get("BD_MCP_WEB_SEARCH_ENABLED", "false").strip().casefold() in {
    "1", "true", "yes", "on",
}
WEB_SEARCH_SCOPE = os.environ.get("BD_MCP_WEB_SEARCH_SCOPE", "bd.search.web").strip()
READ_SCOPE = os.environ.get("BD_MCP_SEARCH_READ_SCOPE", "bd.search.read").strip()

MAX_QUERY_CHARS = 1000
MAX_WEB_QUERY_CHARS = 400
MAX_TOP_K = 8
MAX_WEB_RESULTS = 5
RETRIEVAL_TIMEOUT_S = 95.0
WEB_SEARCH_TIMEOUT_S = 20.0
WEB_SEARCHES_PER_MINUTE = 10
REDIS_TIMEOUT_S = 3.0
