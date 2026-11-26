"""Authorization scopes and bounded execution settings for RAG index tools."""
from __future__ import annotations

import os

READ_SCOPE = os.environ.get("BD_MCP_RAG_INDEX_READ_SCOPE", "bd.rag.index.read").strip()
WRITE_SCOPE = os.environ.get("BD_MCP_RAG_INDEX_WRITE_SCOPE", "bd.rag.index.write").strip()
REPLAY_SCOPE = os.environ.get("BD_MCP_RAG_INDEX_REPLAY_SCOPE", "bd.rag.index.replay").strip()
TOOL_TIMEOUT_S = 120.0
READ_TIMEOUT_S = 20.0
