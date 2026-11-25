"""Compiled patterns used by BD Search MCP validation."""
from __future__ import annotations

import re


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
