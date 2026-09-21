"""Research Radar — the academic-papers + news radar agent.

3rd COELHO Nexus feature alongside Docs Distiller (dd/) and YouTube Content
Search (ycs/). Architecture: docs/RESEARCH-RADAR-AGENT-ARCHITECTURE-2026-06-11.md.

`task.py` is excluded from this eager chain (§8 Exception 1 —
`import infra.celery.service` (use `infra.celery.service.app`) at module level); reach it via a direct
`from domains.rr.task import run_radar_scan`.
"""
from __future__ import annotations

from . import agent, domain, entities, keys, params, patterns, runtime, schemas, service, stores
