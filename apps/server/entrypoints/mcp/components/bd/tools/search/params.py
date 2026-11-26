"""Configuration and resource limits for BD Search MCP."""
from __future__ import annotations

import os


WEB_SEARCH_ENABLED = os.environ.get("BD_MCP_WEB_SEARCH_ENABLED", "false").strip().casefold() in {
    "1", "true", "yes", "on",
}
MAX_QUERY_CHARS = 1000
MAX_WEB_QUERY_CHARS = 400
MAX_TOP_K = 8
MAX_WEB_RESULTS = 5
RETRIEVAL_TIMEOUT_S = 95.0
WEB_SEARCH_TIMEOUT_S = 20.0
WEB_SEARCHES_PER_MINUTE = 10
REDIS_TIMEOUT_S = 3.0
