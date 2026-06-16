"""Per-node detail content for the Pipeline page side drawer.

Click any node in the Cytoscape graph → drawer slides in from the right →
shows the implementation reference for that node so the operator can
study the DeepAgents + FastMCP pattern in-place.

Content is bundled at render time (small enough — ~25KB inline JSON) so
the drawer opens instantly without a server round-trip. Skill .md files
are read from `domains/rr/agent/skills/` (a verbatim symlink across the
repo would be cleaner but cross-app imports + reading the file at render
time is robust enough at this scale)."""
from __future__ import annotations

from pathlib import Path


# Skill .md files live in TWO places:
#   - apps/fastapi/domains/rr/agent/skills/  (the canonical source — loaded
#                                              by the DeepAgents skill loader)
#   - apps/fasthtml/features/rr/skills/      (verbatim mirror — bundled in
#                                              the fasthtml container so the
#                                              per-node drawer can read them
#                                              at request time)
#
# Same duplication pattern as `taxonomy.py`. Keep both in sync; if you edit
# a skill, copy it over with:
#   cp apps/fastapi/domains/rr/agent/skills/*.md \
#      apps/fasthtml/features/rr/skills/
def _resolve_skills_dir() -> Path | None:
    here = Path(__file__).resolve()
    # 1) Local mirror — what the prod fasthtml image carries.
    local = here.parent / "skills"
    if local.is_dir():
        return local
    # 2) Dev-time layout — sibling fastapi app's tree (when running from
    #    a checkout that has both apps on disk).
    for ancestor in here.parents:
        candidate = ancestor / "apps" / "fastapi" / "domains" / "rr" / "agent" / "skills"
        if candidate.is_dir():
            return candidate
    return None


_RR_SKILLS_DIR = _resolve_skills_dir()


# Per-node "Live state" mapping — which mirrored fs path the drawer fetches
# when the operator clicks each node. main.js sees this via the inline JSON
# and issues `GET /api/v1/rr/scan/{id}/fs/{path}` if a scan_id is present.
NODE_LIVE_FS_PATH: dict[str, str] = {
    "discovery_arxiv":                      "discovery/arxiv.json",
    "discovery_semantic_scholar":           "discovery/semantic_scholar.json",
    "discovery_huggingface_daily_papers":   "discovery/huggingface_daily_papers.json",
    "discovery_hn":                         "discovery/hn.json",
    "triage":                               "triage/top_n.json",
    "synthesis":                            "synthesis/report.json",
    "report":                               "digest.json",
    # orchestrator + deep_read + graph_build + persist don't have a single
    # canonical fs entry — extractions are 1-per-paper, persist's outputs
    # are in Postgres/Neo4j/MinIO. The drawer skips the live-state section
    # for these and shows only the static reference content.
}


# Per-node "Phase counter" mapping — which phase bucket inside the
# `GET /scan/{id}/llm-counters` payload powers the per-node LLM activity
# section. Multiple nodes can map to the same phase (e.g. all 4 discovery
# subagents share the "discovery" bucket because they fan out in parallel
# and we attribute their LLM calls to one rollup). Nodes with no LLM
# activity (triage_candidates / graph_build are deterministic tools) get
# an empty value → drawer hides the section.
NODE_LLM_PHASE: dict[str, str] = {
    "orchestrator":                          "orchestrator",
    "discovery_arxiv":                       "discovery",
    "discovery_semantic_scholar":            "discovery",
    "discovery_huggingface_daily_papers":    "discovery",
    "discovery_hn":                          "discovery",
    "deep_read":                             "deep_read",
    "synthesis":                             "synthesis",
    # triage + graph_build + persist + report do no LLM work (triage runs
    # the NIM rerank but it's a one-shot embedding call, not the chat
    # rotator). Omitting means the drawer hides the "LLM activity" block
    # for these nodes.
}


def _load_skill(name: str) -> str:
    """Load a skill .md as a string. Empty string on missing — the drawer
    just hides the skill block in that case."""
    if _RR_SKILLS_DIR is None:
        return ""
    path = _RR_SKILLS_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# Per-node detail map. The `body` field is a list of `(heading, content)`
# pairs the drawer renders as collapsible sections.
def build_node_details() -> dict[str, dict]:
    out = _build_node_details_inner()
    # Attach the live-state fs path per node so the drawer JS can fetch
    # `GET /scan/{id}/fs/{path}` on click. Nodes without a path get an
    # empty value — main.js then skips the live section.
    # Also attach the LLM-counter phase bucket so the drawer can render
    # per-node LLM activity (Path A 2026-06-16).
    for key, entry in out.items():
        entry["live_fs_path"] = NODE_LIVE_FS_PATH.get(key, "")
        entry["llm_phase"]    = NODE_LLM_PHASE.get(key, "")
    return out


def _build_node_details_inner() -> dict[str, dict]:
    paper_extraction       = _load_skill("paper_extraction")
    cross_paper_synthesis  = _load_skill("cross_paper_synthesis")
    digest_rendering       = _load_skill("digest_rendering")
    arxiv_query_shaping    = _load_skill("arxiv_query_shaping")
    rotator_etiquette      = _load_skill("rotator_etiquette")

    return {
        "orchestrator": {
            "title":       "Orchestrator (DeepAgent)",
            "subtitle":    "Coordinates 7 subagents + 2 tools + 2 middlewares",
            "kind":        "agent",
            "source":      "apps/fastapi/domains/rr/agent/graph.py",
            "body": [
                ("Role", (
                    "The root agent built via `create_deep_agent(...)`. Holds the "
                    "system prompt, dispatches subagents via `task()`, owns the "
                    "FS state, and gates termination via `response_format=ScanComplete`."
                )),
                ("Middleware", (
                    "PhaseEnforcerMiddleware — corrects the orchestrator when it "
                    "tries to end before all phase artifacts exist in fs.\n\n"
                    "PhaseEventsMiddleware — emits Redis pub/sub events on every "
                    "phase transition so the SSE stream stays live."
                )),
                ("Response format", (
                    "`ScanComplete` Pydantic model (`schemas.py`) — DeepAgents "
                    "validates the orchestrator's final output against this shape "
                    "before allowing termination."
                )),
            ],
        },

        "discovery_arxiv": {
            "title":       "discovery_arxiv (subagent)",
            "subtitle":    "Fetches arXiv via FastMCP arxiv_search",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/discovery_arxiv.py",
            "body": [
                ("Skill — arxiv_query_shaping.md",  arxiv_query_shaping),
                ("Skill — rotator_etiquette.md",    rotator_etiquette),
                ("Tool", "FastMCP `arxiv_search` (apps/fastmcp/domains/rr/tools/arxiv/)"),
            ],
        },
        "discovery_semantic_scholar": {
            "title":       "discovery_semantic_scholar (subagent)",
            "subtitle":    "Fetches Semantic Scholar via FastMCP semantic_scholar_search",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/discovery_semantic_scholar.py",
            "body": [
                ("Skill — rotator_etiquette.md",    rotator_etiquette),
                ("Tool", "FastMCP `semantic_scholar_search` (apps/fastmcp/domains/rr/tools/semantic_scholar/)"),
            ],
        },
        "discovery_huggingface_daily_papers": {
            "title":       "discovery_huggingface_daily_papers (subagent)",
            "subtitle":    "Fetches HF Daily Papers curation",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/discovery_huggingface_daily_papers.py",
            "body": [
                ("Skill — rotator_etiquette.md",    rotator_etiquette),
                ("Tool", "FastMCP `huggingface_daily_papers` (apps/fastmcp/domains/rr/tools/huggingface_daily_papers/)"),
            ],
        },
        "discovery_hn": {
            "title":       "discovery_hn (subagent)",
            "subtitle":    "Fetches Hacker News via Algolia",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/discovery_hn.py",
            "body": [
                ("Skill — rotator_etiquette.md",    rotator_etiquette),
                ("Tool", "FastMCP `hn_search` (apps/fastmcp/domains/rr/tools/hn/)"),
            ],
        },

        "triage": {
            "title":       "triage (tool)",
            "subtitle":    "Composite signal score + cross-source dedup",
            "kind":        "tool",
            "source":      "apps/fastapi/domains/rr/agent/tools/triage.py",
            "body": [
                ("Role", (
                    "Pure deterministic tool — no LLM. Reads all 4 discovery "
                    "stashes, normalizes via `NormalizedPaper`, dedups by "
                    "arxiv_id with UNION of sources, scores via "
                    "`domain.signal_score`, returns top-N."
                )),
                ("Signal weights", (
                    "See `domains/rr/params.py::SignalWeights`. Defaults: "
                    "relevance=0.30, recency=0.20 (180-day half-life), "
                    "citation_velocity=0.15, influential_ratio=0.10, "
                    "vertical_fit=0.15, cross_tier_buzz=0.10, has_code=0.05."
                )),
            ],
        },

        "deep_read": {
            "title":       "deep_read (subagent)",
            "subtitle":    "Per-paper 5-field extraction",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/deep_read.py",
            "body": [
                ("Skill — paper_extraction.md", paper_extraction),
                ("Output", (
                    "One JSON file per paper in `fs/extractions/{arxiv_id}.json` "
                    "with fields: problem · method · math · how_to_build · "
                    "money_angle · confidence."
                )),
            ],
        },

        "graph_build": {
            "title":       "graph_build (tool)",
            "subtitle":    "Persist papers to Neo4j + Qdrant in parallel",
            "kind":        "tool",
            "source":      "apps/fastapi/domains/rr/agent/tools/graph_build.py",
            "body": [
                ("Role", (
                    "Pure tool — no LLM. Fans out via `asyncio.gather` (semaphore=4) "
                    "over the triage top-N: per paper, embeds the abstract via NIM "
                    "rotator and writes Neo4j Paper/Author/Concept + Qdrant vector."
                )),
                ("Concurrency", (
                    "Per-loop driver cache in `infra/neo4j/service.py` + "
                    "`infra/qdrant/service.py` so Celery's `asyncio.run()` per "
                    "task doesn't reuse a driver from a dead event loop."
                )),
            ],
        },

        "synthesis": {
            "title":       "synthesis (subagent)",
            "subtitle":    "Cluster extractions into 3-7 themes",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/synthesis.py",
            "body": [
                ("Skill — cross_paper_synthesis.md", cross_paper_synthesis),
                ("Output", "`fs/synthesis/report.json` — themes + 2-3 sentence executive summary."),
            ],
        },

        "report": {
            "title":       "report (subagent)",
            "subtitle":    "Assemble the final digest JSON",
            "kind":        "subagent",
            "source":      "apps/fastapi/domains/rr/agent/subagents/report.py",
            "body": [
                ("Skill — digest_rendering.md", digest_rendering),
                ("Output", "`fs/digest.json` — the artifact the Celery task persists to MinIO."),
            ],
        },

        "persist": {
            "title":       "persist (stores)",
            "subtitle":    "MinIO digest + Postgres findings + seen-set update",
            "kind":        "store",
            "source":      "apps/fastapi/domains/rr/service.py::persist_scan_result",
            "body": [
                ("Stores written", (
                    "MinIO: `rr/scans/{scan_id}/digest.json`\n"
                    "Postgres: `radar_scans` (status=done) + `radar_findings` (one row per item)\n"
                    "Postgres: `radar_seen` — mark every arxiv_id as seen for this profile"
                )),
            ],
        },
    }
