"""LLM-driven SubAgent dict factories for the RR agent — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §8 strict-merge: every function here builds
the same shape (a DeepAgents SubAgent dict: name/description/system_prompt/
tools/model), same role, one file.

2026-06-12 step-7: 4 discovery subagents + report subagent re-activated.
2026-06-16 (post-f52fb84a): report subagent RETIRED again — it kept
emitting `{` for write_digest. Per-paper theme assignment moved to the
synthesis subagent's `write_synthesis_report.per_paper_themes`. Digest
assembly is now Python-canonical in `../../task.py::_build_digest_from_fs`.

Active subagents:
  "subagents" mode: discovery_arxiv · discovery_semantic_scholar ·
                    discovery_huggingface_daily_papers · discovery_hn ·
                    discovery_openalex + deep_read + synthesis
  "tools"     mode: deep_read + synthesis (discoveries become tools)

`build_report` is kept as a reference + reusable scaffolding (the
DigestSchema and prompt patterns remain useful), but it is no longer
wired into either topology.
"""
from __future__ import annotations
import infra
from .. import keys, prompts, schemas, service, skills, tools

from typing import Any

from langchain_core.language_models import BaseChatModel


# ---------------------------------------------------------------------------
# arXiv discovery (active in SUBAGENTS mode)
#
# The DeepAgents subagent pattern at its purest:
#   - holds ONE MCP tool (arxiv_search) + ONE fs tool (stash_discovery_result)
#   - its system_prompt is augmented with two skills: arxiv_query_shaping
#     (how to construct args) + rotator_etiquette (how to behave inside
#     the rotator cascade)
#   - the orchestrator dispatches it via `task(subagent_type=...)`
#   - DeepAgents gives this subagent an ISOLATED context so the orchestrator
#     never sees the bulky MCP tool result
#
# In TOOLS mode, this function is dormant — replaced by the deterministic
# discover_arxiv Python @tool in `../tools/discovery/service.py`. Both code paths
# ship in the repo to serve as a DeepAgents reference.
# ---------------------------------------------------------------------------

def _resolve_role(static: str, name: str) -> str:
    """Managed-override layer for a subagent ROLE block. Skills/memory stay
    local (dynamic per-build); only the static role text is replaceable.
    Returns `static` unless a template is published under `name`."""
    try:
        return infra.langfuse.prompts.get_prompt(
            name, label = "production", fallback = static,
        ) or static
    except Exception:
        return static



async def build_discovery_arxiv(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the arXiv discovery worker (SUBAGENTS mode)."""
    mcp_tools = await service.get_tools_by_name(keys.TOOL_ARXIV_SEARCH)
    full_prompt = (
        f"=== SKILL: arxiv_query_shaping ===\n\n"
        f"{skills.service.SKILL_ARXIV_QUERY_SHAPING}\n\n"
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{skills.service.SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.DISCOVERY_ARXIV_SYSTEM_PROMPT, "rr.agent.discovery_arxiv")}"
    )
    return {
        "name":          keys.SUBAGENT_DISCOVERY_ARXIV,
        "description": (
            "Searches arXiv for preprints matching the user's interest "
            "verticals. Calls arxiv_search MCP tool, then stash_discovery_"
            "result (InjectedState — no JSON copying). Best for frontier "
            "ML / CS preprints not yet citation-tracked."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, tools.fs.service.stash_discovery_result],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# Semantic Scholar discovery (active in SUBAGENTS mode)
#
# See build_discovery_arxiv above for the pattern. This subagent holds the
# `semantic_scholar_search` MCP tool + `stash_discovery_result` and shares
# the `rotator_etiquette` skill.
# ---------------------------------------------------------------------------

async def build_discovery_semantic_scholar(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the Semantic Scholar discovery worker."""
    mcp_tools = await service.get_tools_by_name(keys.TOOL_S2_SEARCH)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{skills.service.SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.DISCOVERY_S2_SYSTEM_PROMPT, "rr.agent.discovery_s2")}"
    )
    return {
        "name":          keys.SUBAGENT_DISCOVERY_S2,
        "description": (
            "Searches Semantic Scholar for papers with citation-graph + "
            "influence signals (citations, influential_citation_count, "
            "tldr). Calls semantic_scholar_search MCP + stash_discovery_"
            "result (InjectedState). Optionally uses BYOK SEMANTIC_SCHOLAR_"
            "API_KEY for higher RPS."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, tools.fs.service.stash_discovery_result],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# HuggingFace Daily Papers discovery (active in SUBAGENTS mode)
#
# The HF feed is DATE-AXIS, not text-search — no `query` parameter. The
# subagent's job: call `huggingface_daily_papers` (server-side defaults
# to yesterday UTC), then stash via InjectedState.
# ---------------------------------------------------------------------------

async def build_discovery_huggingface_daily_papers(
    model: BaseChatModel,
) -> dict[str, Any]:
    """SubAgent dict for the HF Daily Papers discovery worker."""
    mcp_tools = await service.get_tools_by_name(keys.TOOL_HF_DAILY)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{skills.service.SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.DISCOVERY_HF_SYSTEM_PROMPT, "rr.agent.discovery_hf")}"
    )
    return {
        "name":          keys.SUBAGENT_DISCOVERY_HF,
        "description": (
            "Fetches HuggingFace's curated Daily Papers feed. Date-axis "
            "(not query-axis) — community-filtered notable papers. Calls "
            "huggingface_daily_papers MCP + stash_discovery_result. Carries "
            "arxiv_id always — the cross-source dedup primary lane."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, tools.fs.service.stash_discovery_result],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# Hacker News discovery (active in SUBAGENTS mode)
#
# Wraps the `hn_search` MCP tool + `stash_discovery_result`. Picks up the
# cross-tier dedup arxiv_id when an HN story's URL points at arxiv.org or
# huggingface.co/papers.
# ---------------------------------------------------------------------------

async def build_discovery_hn(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the Hacker News discovery worker."""
    mcp_tools = await service.get_tools_by_name(keys.TOOL_HN_SEARCH)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{skills.service.SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.DISCOVERY_HN_SYSTEM_PROMPT, "rr.agent.discovery_hn")}"
    )
    return {
        "name":          keys.SUBAGENT_DISCOVERY_HN,
        "description": (
            "Searches Hacker News via Algolia. Calls hn_search MCP + "
            "stash_discovery_result. Returns Hit records with community "
            "traction (points, num_comments) + extracted arxiv_id when "
            "the story URL points at arxiv.org or HF papers."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, tools.fs.service.stash_discovery_result],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# OpenAlex discovery (active in SUBAGENTS mode)
#
# Added 2026-09-20 as a 5th discovery source: free, keyless, no documented
# hard rate limit for polite callers, 320M+ works — structurally avoids
# arXiv's 406 load-shedding under concurrency and S2's contested shared
# pool. Wraps `openalex_search` MCP + `stash_discovery_result`.
# ---------------------------------------------------------------------------

async def build_discovery_openalex(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the OpenAlex discovery worker."""
    mcp_tools = await service.get_tools_by_name(keys.TOOL_OPENALEX_SEARCH)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{skills.service.SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.DISCOVERY_OPENALEX_SYSTEM_PROMPT, "rr.agent.discovery_openalex")}"
    )
    return {
        "name":          keys.SUBAGENT_DISCOVERY_OPENALEX,
        "description": (
            "Searches OpenAlex (320M+ works, free, no key) for works "
            "matching the topic. Broader recall than arxiv — matches "
            "title/abstract/fulltext. Calls openalex_search MCP + "
            "stash_discovery_result (InjectedState). Surfaces open-access "
            "status/URL and topic names; no native arxiv_id (recovered "
            "opportunistically from DOI downstream when present)."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, tools.fs.service.stash_discovery_result],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# deep_read — extracts {problem, method, math, how_to_build, money_angle,
# confidence} for ONE paper.
#
# The orchestrator dispatches this subagent in parallel (one `task` call per
# top-N paper). Each instance gets an isolated context — the DeepAgents
# subagent-isolation payoff.
#
# Augmented with the `paper_extraction` Markdown skill at build time
# (architecture-doc §9.2 pattern — see agent/skills/paper_extraction.md
# for the reusable "how to extract" reference).
# ---------------------------------------------------------------------------

def build_deep_read(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the deep_read worker.

    System prompt = `paper_extraction` skill content + the deep_read
    glue prompt. The skill provides the field rubrics + failure-mode
    warnings; the glue tells the LLM what tools to use.
    """
    full_prompt = (
        f"=== SKILL: paper_extraction ===\n\n"
        f"{skills.service.SKILL_PAPER_EXTRACTION}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.DEEP_READ_SYSTEM_PROMPT, "rr.agent.deep_read")}"
    )
    return {
        "name":          keys.SUBAGENT_DEEP_READ,
        "description": (
            "Read ONE paper (arxiv_id provided in the task description, "
            "abstract loaded from fs/triage/top_n.json) and produce a "
            "structured 5-field extraction. Persists via write_extraction. "
            "Dispatch one per paper, in parallel for Phase 3 fan-out."
        ),
        "system_prompt": full_prompt,
        "tools":         [tools.fs.service.read_top_n_papers, tools.fs.service.write_extraction],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# synthesis — names themes + finds cross-paper convergence.
#
# The orchestrator dispatches this ONCE after deep_read fan-out completes.
# Reads all extractions from the virtual fs + the triage ranking; uses LLM
# reasoning to spot what's notable about THIS scan.
#
# Augmented with the `cross_paper_synthesis` Markdown skill at build time
# (architecture-doc §9.2). See agent/skills/cross_paper_synthesis.md.
# ---------------------------------------------------------------------------

def build_synthesis(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the synthesis worker."""
    full_prompt = (
        f"=== SKILL: cross_paper_synthesis ===\n\n"
        f"{skills.service.SKILL_CROSS_PAPER_SYNTHESIS}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.SYNTHESIS_SYSTEM_PROMPT, "rr.agent.synthesis")}"
    )
    return {
        "name":          keys.SUBAGENT_SYNTHESIS,
        "description": (
            "Read the deep_read extractions for this scan's top-N papers "
            "and produce a SynthesisReport with: (1) 3-7 emerging themes "
            "spanning ≥2 papers each, (2) cross-paper convergence notes, "
            "(3) a 2-3 sentence executive summary. Persists via the "
            "write_synthesis_report tool. Dispatch ONCE after deep_read."
        ),
        "system_prompt": full_prompt,
        "tools": [
            tools.fs.service.read_top_n_papers,
            tools.fs.service.list_extractions,
            tools.fs.service.read_extraction,
            tools.fs.service.write_synthesis_report,
        ],
        "model":         model,
    }


# ---------------------------------------------------------------------------
# report — assembles the final ranked digest (SUBAGENTS mode). RETIRED —
# kept as reference + reusable scaffolding, not wired into either topology.
#
# Active when `RR_DISCOVERY_MODE=subagents`. The orchestrator dispatches
# this LAST. Reads triage + synthesis + extractions from fs, renders a
# digest JSON with per-paper cards (arxiv_id, title, 1-line summary,
# themes, sources, extraction), and writes it to fs/digest.json.
#
# In tools mode, this subagent is bypassed — the Celery task's
# `_build_digest_from_fs` does the assembly in Python.
#
# Augmented with the `digest_rendering` Markdown skill at build time
# (architecture-doc §9.2). See agent/skills/digest_rendering.md.
#
# 2026-06-15: Bound to `DigestSchema` via SubAgent.response_format. The
# DeepAgents ToolStrategy injects a `respond_in_format` tool the LLM must
# call to terminate, with Pydantic-validated args. This eliminates the
# malformed-JSON failure class (the `Invalid \uXXXX escape` + `missing
# comma` write_digest bounce that burned ~10min/scan). write_digest stays
# in the tool list as the side-effect persistence channel; on failure it
# no longer bounces the subagent (it returns success-with-warning so the
# LLM moves on to respond_in_format).
# ---------------------------------------------------------------------------

def build_report(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the report worker."""
    full_prompt = (
        f"=== SKILL: digest_rendering ===\n\n"
        f"{skills.service.SKILL_DIGEST_RENDERING}\n\n"
        f"=== ROLE ===\n\n"
        f"{_resolve_role(prompts.REPORT_SYSTEM_PROMPT, "rr.agent.report")}\n\n"
        f"=== TERMINATION ===\n\n"
        f"After write_digest persists the digest, call `respond_in_format` "
        f"ONCE with the same payload (Pydantic DigestSchema). The framework "
        f"validates fields on your behalf — emit prose ONLY inside the "
        f"`summary` field, never as your terminal message. write_digest is "
        f"tolerant of partial JSON now; treat any non-ERROR return as "
        f"success and proceed to respond_in_format immediately."
    )
    return {
        "name":          keys.SUBAGENT_REPORT,
        "description": (
            "Assemble the final ranked digest from this scan's triage, "
            "synthesis, and extractions. Outputs a structured JSON "
            "digest with per-paper cards. Persists via write_digest + "
            "terminates via respond_in_format(DigestSchema). Dispatch "
            "LAST, after synthesis. (Active in subagents mode only; "
            "tools mode uses Python assembly in task.py.)"
        ),
        "system_prompt": full_prompt,
        "tools": [
            tools.fs.service.read_top_n_papers,
            tools.fs.service.read_synthesis_report,
            tools.fs.service.list_extractions,
            tools.fs.service.read_extraction,
            tools.fs.service.write_digest,
        ],
        "model":           model,
        "response_format": schemas.DigestSchema,
    }
