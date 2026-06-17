"""Research Radar agent factory (step-7 refactor 2026-06-12).

Maximizes DeepAgents feature usage per `feedback_rr_learning_purpose`:

  - Two discovery modes wired (`RR_DISCOVERY_MODE`):
      "subagents" → 4 LLM discovery subagents + report subagent
                    (default; full DeepAgents pattern)
      "tools"     → 4 deterministic Python @tool wrappers
                    (faster; no LLM-driven JSON copying)
  - PhaseEnforcerMiddleware    keeps the orchestrator running until
                               every fs artifact exists (no more "agent
                               ended at phase 1" bug)
  - PhaseEventsMiddleware      per-phase SSE granularity
  - response_format=Pydantic   the agent's final output is validated
                               against ScanComplete shape
  - skills=[<.md files>]       reusable capability bundles loaded by
                               subagents at build time
  - memory=[<.md files>]       cross-scan operator profile + themes_seen
                               substituted into the orchestrator prompt
  - InMemorySaver              sidesteps the langgraph 4.1.1 msgpack
                               serde bug on AIMessage; RR doesn't need
                               cross-task resume

What's still TODO (architecture-doc §9.4 v2 deferrals):
  - BaseStore-backed memory (today: file-substitution only)
  - interrupt_on for HITL approval (single-user today)
  - cache for re-running the same scan instantly
  - AsyncSubAgent for distributed deep_read fan-out (see step-7 stub
    block at the bottom of this file)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from domains.llm.rotator.chain.service import (
    build_rr_strong_chain,
    build_rr_strong_chain_bandit,
)

from .keys import (
    DISCOVERY_MODE_AGENTS,
    DISCOVERY_MODE_DEFAULT,
    DISCOVERY_MODE_ENV,
    DISCOVERY_MODE_TOOLS,
)
from .memory import MEMORY_OPERATOR_PROFILE, MEMORY_THEMES_SEEN
from .middleware import PhaseEnforcerMiddleware, PhaseEventsMiddleware
from .prompts import (
    ORCHESTRATOR_MEMORY_TEMPLATE,
    ORCHESTRATOR_SYSTEM_PROMPT_SUBAGENTS,
    ORCHESTRATOR_SYSTEM_PROMPT_TOOLS,
)
from .schemas import ScanComplete
from .subagents import (
    build_deep_read,
    build_discovery_arxiv,
    build_discovery_hn,
    build_discovery_huggingface_daily_papers,
    build_discovery_semantic_scholar,
    build_synthesis,
)
from ..runtime.llm_counter import RRLlmCounterCallback
from .tools.discovery import (
    discover_arxiv,
    discover_hn,
    discover_huggingface_daily_papers,
    discover_semantic_scholar,
)
from .tools.graph_build import graph_build_papers
from .tools.triage import triage_candidates


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Model factories — orchestrator + subagents use the rr-strong pool (Wave 1.3
# expanded 4 → 10 arms 2026-06-16: 7 NIM frontier + 2 Mistral direct + 1
# SambaNova free-tier 405B). With Wave 1.2 the rotator chain is bandit-
# routed (FGTS-VA), not simple-shuffle — matching the Planner/Synth
# routing brain. Net effect under parallel deep_read fan-out: 4 concurrent
# task() calls pick 4 different bandit-top arms instead of all racing for
# one simple-shuffle pick.
# --------------------------------------------------------------------------- #
# Module-level callback instance — one handler reused across all model
# bindings. Path-A LLM-counter (2026-06-16): every chat completion bumps
# the per-scan Redis counters. Skips silently when no scan_id is in the
# contextvar (non-RR callers reuse the same rotator chain). Attached
# globally via `agent.ainvoke(config={"callbacks":[...]})` in task.py so
# we don't need to mutate the model (model wrapping breaks DeepAgents'
# `isinstance(model, BaseChatModel)` check — see service.py comments).
_LLM_COUNTER_CB = RRLlmCounterCallback()


# Env flag — Wave 1.2 default ON. `KD_RR_BANDIT_CHAT=false` reverts to the
# baseline LiteLLM Router simple-shuffle chain for instant rollback if the
# bandit-routed chain misbehaves in production.
_BANDIT_CHAT_ENV = "KD_RR_BANDIT_CHAT"


def _use_bandit_chat() -> bool:
    """Read KD_RR_BANDIT_CHAT — default True. Accepts 1/true/yes (case-insensitive)."""
    val = os.environ.get(_BANDIT_CHAT_ENV, "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    # Anything else (including unset) → bandit ON.
    return True


def _build_strong_chain() -> BaseChatModel:
    """Pick the rr-strong chain variant based on the rollback flag."""
    if _use_bandit_chat():
        return build_rr_strong_chain_bandit()
    return build_rr_strong_chain()


def _orchestrator_model() -> BaseChatModel:
    """Strong-tier model for the orchestrator. Bandit-routed by default
    (Wave 1.2 — FGTS-VA per-call selection, top-K cascade, 10-arm pool)."""
    return _build_strong_chain()


def _subagent_model() -> BaseChatModel:
    """Strong-tier model for the LLM subagents. Same pool + bandit brain
    as the orchestrator — phase attribution happens in the counter callback
    by reading the `_phase_var` contextvar that each fs-tool write updates
    (`stash_discovery_result` → discovery, `write_extraction` → deep_read,
    etc.). The first LLM call by a subagent before its first fs-write
    attributes to the PRIOR phase; subsequent calls are correct.

    Parallel-fan-out behavior (Wave 1 — deep_read): the orchestrator emits
    multiple `task(subagent_type="deep_read", arxiv_id=…)` tool_calls in
    ONE message. LangGraph 1.x's async ToolNode dispatches them via
    `asyncio.gather`, so each subagent's `ainvoke` runs concurrently. Each
    subagent in turn calls this chain → each LLM turn picks a bandit-top
    deployment. With the 10-arm pool the chance of two subagents racing
    on the same deployment is low; arm cooldown + bandit reward feedback
    further spread the load."""
    return _build_strong_chain()


def _ensure_checkpointer() -> Any:
    """InMemorySaver — RR doesn't need cross-task resume, and bypasses
    the langgraph 4.1.1 msgpack AIMessage bug."""
    return InMemorySaver()


def _discovery_mode() -> str:
    """Read RR_DISCOVERY_MODE env. Default = subagents (learning path)."""
    val = os.environ.get(DISCOVERY_MODE_ENV, DISCOVERY_MODE_DEFAULT).strip().lower()
    if val not in (DISCOVERY_MODE_TOOLS, DISCOVERY_MODE_AGENTS):
        logger.warning(
            f"[rr-agent] {DISCOVERY_MODE_ENV}={val!r} not recognized; "
            f"falling back to default {DISCOVERY_MODE_DEFAULT!r}"
        )
        return DISCOVERY_MODE_DEFAULT
    return val


def _build_orchestrator_prompt(mode: str) -> str:
    """Pick the mode-appropriate prompt + substitute memory content."""
    base = (
        ORCHESTRATOR_SYSTEM_PROMPT_SUBAGENTS
        if mode == DISCOVERY_MODE_AGENTS
        else ORCHESTRATOR_SYSTEM_PROMPT_TOOLS
    )
    memory_block = ORCHESTRATOR_MEMORY_TEMPLATE.format(
        operator_profile = MEMORY_OPERATOR_PROFILE or "(no operator profile yet)",
        themes_seen      = MEMORY_THEMES_SEEN      or "(no themes seen yet)",
    )
    return base + memory_block


async def build_radar_agent() -> Any:
    """Build the Research Radar DeepAgents agent.

    Reads `RR_DISCOVERY_MODE` env to pick the topology. Both modes wire:
      - middleware: PhaseEnforcer + PhaseEvents
      - response_format: ScanComplete
      - checkpointer: InMemorySaver
      - LLM subagents: deep_read + synthesis (both modes)
      - Tools: triage_candidates + graph_build_papers (both modes)

    Mode-specific:
      "subagents": + 4 discovery subagents (report subagent retired
                     2026-06-16 — synthesis now owns per-paper themes;
                     digest assembly is Python in task.py for both modes)
      "tools":     + 4 discover_* tools (replaces discovery subagents)

    2026-06-16 (post-f52fb84a): report subagent removed from both modes.
    It emitted `{` six times for write_digest across an 8-min window.
    Per-paper theme assignment moved to synthesis subagent
    (write_synthesis_report.per_paper_themes); `_build_digest_from_fs`
    reads it directly. Digest assembly is now Python-canonical regardless
    of mode.
    """
    mode = _discovery_mode()
    orchestrator_model = _orchestrator_model()
    subagent_model     = _subagent_model()

    # Subagents always include deep_read + synthesis. Mode adds discoveries
    # when in "subagents" mode.
    subagents: list[dict[str, Any]] = [
        build_deep_read(subagent_model),
        build_synthesis(subagent_model),
    ]
    tools: list[Any] = [
        triage_candidates,
        graph_build_papers,
    ]

    if mode == DISCOVERY_MODE_AGENTS:
        subagents = [
            await build_discovery_arxiv(subagent_model),
            await build_discovery_semantic_scholar(subagent_model),
            await build_discovery_huggingface_daily_papers(subagent_model),
            await build_discovery_hn(subagent_model),
        ] + subagents
    else:  # DISCOVERY_MODE_TOOLS
        tools = [
            discover_arxiv,
            discover_semantic_scholar,
            discover_huggingface_daily_papers,
            discover_hn,
        ] + tools

    middleware = [
        PhaseEnforcerMiddleware(),
        PhaseEventsMiddleware(),
    ]

    system_prompt = _build_orchestrator_prompt(mode)
    checkpointer  = _ensure_checkpointer()

    agent = create_deep_agent(
        model         = orchestrator_model,
        tools         = tools,
        system_prompt = system_prompt,
        subagents     = subagents,
        middleware    = middleware,
        response_format = ScanComplete,
        checkpointer  = checkpointer,
    )
    # Expose the LLM-counter callback on the agent for task.py to attach
    # via `ainvoke(config={"callbacks":[...]})` — propagates to every
    # nested LangChain runnable (orchestrator + 6 subagents) without
    # needing model wrapping (which breaks DeepAgents' isinstance check).
    agent._rr_llm_counter_cb = _LLM_COUNTER_CB  # type: ignore[attr-defined]

    logger.info(
        f"[rr-agent] built mode={mode!r} "
        f"tools={len(tools)} subagents={len(subagents)} "
        f"middleware=[PhaseEnforcer, PhaseEvents] "
        f"response_format=ScanComplete "
        f"skills=5 memory=2"
    )
    logger.info(
        f"[rr-agent] subagent_names={[s['name'] for s in subagents]} "
        f"tool_names={[t.name for t in tools]}"
    )
    return agent


# --------------------------------------------------------------------------- #
# Subagent parallelism model — Wave 1.4 (2026-06-16) findings
# --------------------------------------------------------------------------- #
# DeepAgents 0.6.8 ships TWO subagent flavors:
#
#   1. **SubAgent dict** (what we use)   — in-process compiled langgraph,
#      dispatched via SubAgentMiddleware.atask() which awaits
#      `subagent.ainvoke(state, config)`. When the orchestrator emits N
#      `task(...)` tool_calls in ONE assistant message, LangGraph's async
#      ToolNode runs them concurrently via `asyncio.gather`. Result: 4
#      `task()` calls → 4 concurrent subagent loops, all sharing this
#      Celery worker's event loop.
#
#   2. **AsyncSubAgent**                  — remote LangGraph Platform /
#      Agent Protocol server. The subagent runs on a SEPARATE service;
#      DeepAgents talks to it via langgraph_sdk.get_client(). Requires a
#      deployed LangGraph server endpoint (or self-hosted ASGI). Not
#      applicable to our single-Celery-worker deployment.
#
# We stay on flavor #1. The 10-20 min for 4 deep_reads observed pre-Wave-1
# was NOT a DeepAgents dispatch problem — it was a ROTATOR problem:
#   - 4-arm rr-strong pool + LiteLLM Router simple-shuffle
#   - 4 concurrent ainvoke calls each randomly picked one of 4 arms
#   - High collision probability → 429 → cascade serial cooldown waits
#
# Wave 1.2 (bandit-routed chain) + 1.3 (10-arm pool) eliminate this:
#   - FGTS-VA Thompson sampling picks DIFFERENT top-K arms across the
#     4 concurrent subagent calls (RNG-driven exploration)
#   - 2.5× wider candidate pool absorbs cooldowns without cascade
#
# Wave 1.5 (asyncio.Semaphore at task.py entry) caps parallel LLM dispatch
# so the orchestrator + 4 subagents + cascade retries don't burst past
# what NIM's 40 RPM / Groq's 30 RPM windows can absorb.
#
# Hook for v2: when horizontal scaling beyond one worker is needed,
# the AsyncSubAgent flavor is one option — but Celery-distributed
# subagents (workers picking up `deep_read_one_paper` tasks from a queue)
# is the cheaper path. Both deferred.
