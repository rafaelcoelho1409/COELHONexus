"""
Knowledge Distiller — Classical (Deterministic) Summary / Assembler (Phase 5, 2026-05-13)

Replaces the single large LLM call (ASSEMBLER_PROMPT in schemas/knowledge/
prompts.py) with a deterministic Python-built chapter index + reading plan
plus one small-LLM call for the irreducibly-creative content: the
1-paragraph framing, the market roadmap, and 3-5 money projects.

Drop-in shape: same signature as `_call_assembler_llm` — accepts framework
+ user_profile + chapter previews, returns the assembled `summary.md`
markdown string ready for `storage.write`.

Per-step replacement:

  Deterministic (Python):
    - Header (`# {framework} Study`)
    - "## Reading Plan" — bulleted list with chapter links + goal as
      one-line takeaway. Sourced directly from the chapter previews.

  Small-LLM (one structured-output call via kd-reduce-label rotator):
    - `framing` — single dense paragraph anchoring the study
    - `market_roadmap` — empty if user_profile.target_markets is empty;
      otherwise a paragraph on how to leverage the framework there
    - `money_projects` — 3-5 concrete monetizable project ideas, each
      with name + description + target market

Token cost vs LLM-only assembler:
  - LLM-only: ASSEMBLER_PROMPT sees full chapter index (~3K-5K input
    tokens for 10 chapters at 500 char preview each) + emits the full
    summary.md document (~1500-3000 output tokens)
  - Classical: same input tokens, but output is structured JSON for
    just framing+roadmap+projects (~600 output tokens — the reading
    plan list is built deterministically and doesn't transit the LLM)
  → ~70-80% output-token reduction, ~30% input-token reduction (the
  user_profile slice is trimmed; full chapter previews still pass).

Pattern source: KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md Phase 5 / Step F.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from schemas.knowledge.inputs import UserProfile


logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic schema for the small-LLM creative-content call
# =============================================================================
class _MoneyProject(BaseModel):
    """One monetizable project idea."""
    name: str = Field(
        description="Short project name. 2-6 words. Example: "
                    "'Async API Gateway for UAE Fintech'.",
    )
    description: str = Field(
        description="1-3 sentence elevator pitch. Concrete: name the buyer, "
                    "the deliverable, and the technical hook. ≤300 chars.",
    )
    target_market: str = Field(
        description="Which target market this addresses. One of the user's "
                    "target_markets, or 'general' if none fits.",
    )


class _SummaryCreative(BaseModel):
    """
    Bundle of creative artifacts the small LLM generates. The reading plan
    is NOT here — it's built deterministically from chapter data.
    """
    framing: str = Field(
        description="One paragraph (60-120 words) framing why this study "
                    "exists, what it covers, and who it's for. No bullet "
                    "points. Dense, production-focused tone.",
    )
    market_roadmap: str = Field(
        description="Empty string if no target markets are declared. "
                    "Otherwise one paragraph (40-100 words) on how to "
                    "leverage the framework in those specific markets — "
                    "name companies, compliance regimes, or buyer profiles.",
    )
    money_projects: list[_MoneyProject] = Field(
        min_length=3,
        max_length=5,
        description="3-5 concrete monetizable project ideas using this "
                    "framework, aligned with the user's target_markets and "
                    "portfolio_refs.",
    )


# =============================================================================
# Deterministic builders
# =============================================================================
def _build_reading_plan(
    previews: list[tuple[int, str, str, str]],
) -> str:
    """
    Build the deterministic '## Reading Plan' section from chapter
    previews. Each entry: `1. [Chapter NN — Title](chapterNN/README.md) — goal`.
    Goal is trimmed to its first sentence to keep the line tight.

    `previews` shape: (number, title, goal, preview_text). The preview
    text is unused here — only number/title/goal feed the reading plan.
    """
    if not previews:
        return ""

    sorted_previews = sorted(previews, key=lambda p: p[0])
    lines: list[str] = ["## Reading Plan", ""]
    for num, title, goal, _preview in sorted_previews:
        goal_trim = (goal or "").strip().split(". ")[0].rstrip(".")
        if len(goal_trim) > 140:
            goal_trim = goal_trim[:137].rsplit(" ", 1)[0] + "..."
        lines.append(
            f"{num}. [Chapter {num:02d} — {title}]"
            f"(chapter{num:02d}/README.md) — {goal_trim}"
        )
    return "\n".join(lines)


def _format_money_projects(projects: list[_MoneyProject]) -> str:
    """Render the LLM's money_projects list as a markdown section."""
    if not projects:
        return ""
    lines = ["## Money Projects", ""]
    for i, p in enumerate(projects, 1):
        lines.append(f"### {i}. {p.name}")
        lines.append("")
        lines.append(p.description.strip())
        if p.target_market and p.target_market.lower() != "general":
            lines.append("")
            lines.append(f"*Target market: {p.target_market}*")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_market_roadmap(roadmap: str) -> str:
    """Wrap the LLM's market_roadmap paragraph in its section heading."""
    rm = (roadmap or "").strip()
    if not rm:
        return ""
    return f"## Market Roadmap\n\n{rm}\n"


# =============================================================================
# Public API
# =============================================================================
async def build_summary_classically(
    framework: str,
    user_profile: UserProfile,
    previews: list[tuple[int, str, str, str]],
    llm=None,
) -> str:
    """
    Build summary.md classically — deterministic header + reading plan +
    one small-LLM call for framing/roadmap/money-projects.

    `llm` argument is kept for backwards compatibility with the LLM-path
    signature but is IGNORED — the small-LLM call routes through the
    `kd-reduce-label` rotator group (same pool used by REDUCE meta-labels,
    grader market_analysis, outline challenges/flashcards). Falls back to
    a minimal summary if the rotator is unavailable.
    """
    if not previews:
        return (
            f"# {framework} Study\n\n"
            f"No chapters available. See `DEBT.md` for the failure log.\n"
        )

    # ---- Deterministic header + reading plan ----
    reading_plan = _build_reading_plan(previews)

    # ---- Small-LLM creative-content call ----
    targets = user_profile.target_markets or []
    portfolio = user_profile.portfolio_refs or []
    markets_str = ", ".join(targets) if targets else "general"
    portfolio_str = ", ".join(portfolio) if portfolio else "none declared"

    # Build a compact chapter index for the LLM — just titles + goals so
    # the LLM understands what the study covers without ingesting all
    # preview prose.
    chapter_index_lines = []
    for num, title, goal, _ in sorted(previews, key=lambda p: p[0]):
        chapter_index_lines.append(
            f"{num}. {title} — {(goal or '').strip()}"
        )
    chapter_index = "\n".join(chapter_index_lines)

    prompt_text = (
        f"You are generating the creative artifacts for a `{framework}` "
        f"code-framework study summary. Reader is "
        f"{user_profile.level}-level; target markets: [{markets_str}]; "
        f"portfolio refs: [{portfolio_str}].\n\n"
        f"Chapter index:\n{chapter_index}\n\n"
        f"Generate JSON with three fields:\n"
        f"1. `framing` — one paragraph (60-120 words) on why this study "
        f"exists, what it covers, and who it's for. Production-focused, "
        f"no fluff.\n"
        f"2. `market_roadmap` — one paragraph (40-100 words) on leveraging "
        f"`{framework}` in [{markets_str}]; name companies, compliance "
        f"regimes, or buyer profiles. Empty string if markets='general'.\n"
        f"3. `money_projects` — 3-5 concrete monetizable project ideas "
        f"using `{framework}`, each with name + description + target_market."
    )

    creative: _SummaryCreative | None = None
    try:
        from services.llm_chain import build_reduce_label_chain
        rotator_llm = build_reduce_label_chain()
        chain = rotator_llm.with_structured_output(
            _SummaryCreative, method="json_schema",
        )
        creative = await chain.ainvoke(prompt_text)
    except Exception as e:
        logger.warning(
            f"[summary-classical] creative LLM call failed "
            f"({type(e).__name__}: {str(e)[:160]}); writing fallback"
        )
        creative = None

    # ---- Assemble the markdown ----
    parts: list[str] = []
    parts.append(f"# {framework} Study")
    parts.append("")
    if creative and creative.framing:
        parts.append(creative.framing.strip())
        parts.append("")
    else:
        parts.append(
            f"This study distills {len(previews)} chapter(s) on `{framework}` "
            f"for a {user_profile.level}-level reader. Begin with Chapter 01 "
            f"and proceed in order. See `DEBT.md` for any chapter that did "
            f"not reach the acceptance threshold."
        )
        parts.append("")
    parts.append(reading_plan)
    parts.append("")
    if creative:
        rm = _format_market_roadmap(creative.market_roadmap)
        if rm:
            parts.append(rm)
        mp = _format_money_projects(creative.money_projects)
        if mp:
            parts.append(mp)
    return "\n".join(parts).rstrip() + "\n"
