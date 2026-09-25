"""Research Radar — MCP sub-server registration.

Pattern: each domain owns a `register(mcp)` function that registers all its
MCP capabilities (tools, resources, prompts, domain-specific middleware) on
the root FastMCP server. Mirrors the apps/fasthtml `features.X.register(rt)`
convention so the three peer apps (fastapi · fasthtml · fastmcp) share a
uniform "register feature on root app" idiom.

Step-7 (2026-06-12): Resources + Prompts added (architecture-doc §2.2).
The radar repo now exercises all 4 FastMCP primitives that ship in v3.x:

  Tools     — arxiv_search, semantic_scholar_search, huggingface_daily_papers,
              hn_search, openalex_search
  Resources — radar://latest_digest, radar://concept/{name}
  Prompts   — /digest_today
  Auth      — TODO (JWT verifier — only matters when we expose
              externally; ClusterIP is enough today)
  Composition — TODO (mount() — only matters when a 2nd domain exposes
              MCP tools, e.g. DD-as-MCP)

Layout (per docs/CODE-CONVENTIONS.md §4):
  tools/      5 sources × {tool, service, domain, schemas, config, keys}
  resources/  resource.py boundary + service/domain/keys/params
  prompts/    prompt.py boundary + prompts/versions/keys/domain
  server.py   ← THIS — register(mcp) dispatch only: one dotted call per
              capability, no tools/resources/prompts defined here.
              New capability = one line below; new domain = a second
              register() mounted via Composition, not lines here.

openalex_search (2026-09-20) is not yet wired into the RR agent's 4-source
discovery phase (prompts/keys/triage normalizer all still assume exactly
4) — it's registered here as a usable MCP tool; integrating it as a 5th
discovery subagent is a separate, larger change.
"""
from __future__ import annotations

import domains

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register every Research Radar MCP capability on the root server."""
    domains.rr.tools.arxiv.register(mcp)
    domains.rr.tools.semantic_scholar.register(mcp)
    domains.rr.tools.huggingface_daily_papers.register(mcp)
    domains.rr.tools.hn.register(mcp)
    domains.rr.tools.openalex.register(mcp)
    domains.rr.resources.register(mcp)
    domains.rr.prompts.register(mcp)
    # TODO Auth + Composition: see module docstring.
