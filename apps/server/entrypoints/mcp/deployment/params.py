"""Deployment profile selected for the external MCP endpoint."""
from __future__ import annotations

import os


MCP_PROFILES = frozenset({"disabled", "local_demo", "protected"})
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})

MCP_PROFILE = os.environ.get("BD_MCP_PROFILE", "disabled").strip().casefold()
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local").strip().casefold()
