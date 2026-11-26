"""Authorization scopes and bounded execution settings for ingestion tools."""
from __future__ import annotations

import os

READ_SCOPE = os.environ.get("BD_MCP_INGESTION_READ_SCOPE", "bd.ingestion.read").strip()
WRITE_SCOPE = os.environ.get("BD_MCP_INGESTION_WRITE_SCOPE", "bd.ingestion.write").strip()
REPLAY_SCOPE = os.environ.get("BD_MCP_INGESTION_REPLAY_SCOPE", "bd.ingestion.replay").strip()
TOOL_TIMEOUT_S = 120.0
READ_TIMEOUT_S = 20.0
