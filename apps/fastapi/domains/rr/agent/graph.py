"""Research Radar agent factory.

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
import domains, infra
from . import keys, memory, middleware, params, prompts, schemas, subagents, tools
from .. import runtime

import logging
import os
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver


logger = logging.getLogger(__name__)


# Model factories — orchestrator + subagents both build through
# `build_chat_model`, a `ChatOpenAI` pointed at the Settings-page-
# configured external endpoint (same endpoint DD's Planner/Synth and
# YCS's Ingestion/Ask/Query use). Module-level callback
# instance — one handler reused across all model bindings. Path-A LLM-
# counter: every chat completion bumps the per-scan Redis counters.
# Skips silently when no scan_id is in the contextvar (non-RR callers
# reuse the same chat model). Attached globally via
# `agent.ainvoke(config={"callbacks":[...]})` in task.py so we don't
# need to mutate the model (model wrapping breaks DeepAgents'
# `isinstance(model, BaseChatModel)` check — see service.py comments).
_LLM_COUNTER_CB = runtime.llm_counter.service.RRLlmCounterCallback()


def _orchestrator_model() -> BaseChatModel:
    """Strong-tier model for the orchestrator. Deterministic (temperature
    from `AgentParams.orchestrator_temperature`).

    2026-09-18: `max_retries=1` — this model is passed to DeepAgents as
    the bare model object, so it can't be wrapped in `resilient_ainvoke`
    the way YCS Ask or RR's own backfill/code_synth are (breaks
    DeepAgents' `isinstance(model, BaseChatModel)` check). Confirmed
    live: one transient endpoint 504 with zero retry anywhere crashed an
    entire scan 90% through, discarding 7/8 already-written
    extractions. This is the only retry lever compatible with staying
    a bare `BaseChatModel`."""
    return domains.settings.chat.service.build_chat_model(
        temperature  = params.PARAMS.orchestrator_temperature,
        max_retries  = 1,
    )


def _subagent_model() -> BaseChatModel:
    """Strong-tier model for the LLM subagents. Phase attribution for
    usage/counter purposes happens in the counter callback, by reading
    the `_phase_var` contextvar that each fs-tool write updates
    (`stash_discovery_result` → discovery, `write_extraction` → deep_read,
    etc.). The first LLM call by a subagent before its first fs-write
    attributes to the PRIOR phase; subsequent calls are correct.

    Parallel-fan-out behavior (Wave 1 — deep_read): the orchestrator emits
    multiple `task(subagent_type="deep_read", arxiv_id=…)` tool_calls in
    ONE message. LangGraph 1.x's async ToolNode dispatches them via
    `asyncio.gather`, so each subagent's `ainvoke` runs concurrently
    against the SAME external endpoint.

    2026-09-18: `max_retries=1` — same reasoning as `_orchestrator_model`
    above. This is in fact the exact model that crashed a live scan:
    the deep_read subagent's 8th extraction call hit an endpoint 504
    with zero retry, discarding 7 already-
    written extractions and the whole discovery phase along with it."""
    return domains.settings.chat.service.build_chat_model(
        temperature  = params.PARAMS.subagent_temperature,
        max_retries  = 1,
    )


def _ensure_checkpointer() -> Any:
    """InMemorySaver — RR doesn't need cross-task resume, and bypasses
    the langgraph 4.1.1 msgpack AIMessage bug."""
    return InMemorySaver()


def _discovery_mode() -> str:
    """Read RR_DISCOVERY_MODE env. Default = subagents (learning path)."""
    val = os.environ.get(keys.DISCOVERY_MODE_ENV, keys.DISCOVERY_MODE_DEFAULT).strip().lower()
    if val not in (keys.DISCOVERY_MODE_TOOLS, keys.DISCOVERY_MODE_AGENTS):
        logger.warning(
            f"[rr-agent] {keys.DISCOVERY_MODE_ENV}={val!r} not recognized; "
            f"falling back to default {keys.DISCOVERY_MODE_DEFAULT!r}"
        )
        return keys.DISCOVERY_MODE_DEFAULT
    return val


def _build_orchestrator_prompt(mode: str) -> str:
    """Pick the mode-appropriate prompt + substitute memory content.

    The base prompt is sourced from LangFuse (label `production`) when
    available; otherwise from the local constant. Memory block is
    always appended locally — it's per-build dynamic content."""
    local_base = (
        prompts.ORCHESTRATOR_SYSTEM_PROMPT_SUBAGENTS
        if mode == keys.DISCOVERY_MODE_AGENTS
        else prompts.ORCHESTRATOR_SYSTEM_PROMPT_TOOLS
    )
    try:
        prompt_name = (
            "rr.agent.orchestrator_subagents"
            if mode == keys.DISCOVERY_MODE_AGENTS
            else "rr.agent.orchestrator_tools"
        )
        base = infra.langfuse.prompts.get_prompt(
            prompt_name, label = "production", fallback = local_base,
        ) or local_base
    except Exception:
        base = local_base

    memory_block = prompts.ORCHESTRATOR_MEMORY_TEMPLATE.format(
        operator_profile = memory.service.MEMORY_OPERATOR_PROFILE or "(no operator profile yet)",
        themes_seen      = memory.service.MEMORY_THEMES_SEEN      or "(no themes seen yet)",
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
      "subagents": + 5 discovery subagents (report subagent retired
                     synthesis now owns per-paper themes;
                     digest assembly is Python in task.py for both modes)
      "tools":     + 5 discover_* tools (replaces discovery subagents)

    Report subagent removed from both modes.
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
    subagent_list: list[dict[str, Any]] = [
        subagents.service.build_deep_read(subagent_model),
        subagents.service.build_synthesis(subagent_model),
    ]
    tool_list: list[Any] = [
        tools.triage.service.triage_candidates,
        tools.graph_build.service.graph_build_papers,
    ]

    if mode == keys.DISCOVERY_MODE_AGENTS:
        subagent_list = [
            await subagents.service.build_discovery_arxiv(subagent_model),
            await subagents.service.build_discovery_semantic_scholar(subagent_model),
            await subagents.service.build_discovery_huggingface_daily_papers(subagent_model),
            await subagents.service.build_discovery_hn(subagent_model),
            await subagents.service.build_discovery_openalex(subagent_model),
        ] + subagent_list
    else:  # DISCOVERY_MODE_TOOLS
        tool_list = [
            tools.discovery.service.discover_arxiv,
            tools.discovery.service.discover_semantic_scholar,
            tools.discovery.service.discover_huggingface_daily_papers,
            tools.discovery.service.discover_hn,
            tools.discovery.service.discover_openalex,
        ] + tool_list

    _phase_events_mw = middleware.service.PhaseEventsMiddleware()
    middleware_list = [
        middleware.service.PhaseEnforcerMiddleware(),
        _phase_events_mw,
    ]

    system_prompt = _build_orchestrator_prompt(mode)
    checkpointer  = _ensure_checkpointer()

    radar_agent = create_deep_agent(
        model         = orchestrator_model,
        tools         = tool_list,
        system_prompt = system_prompt,
        subagents     = subagent_list,
        middleware    = middleware_list,
        response_format = schemas.ScanComplete,
        checkpointer  = checkpointer,
    )
    # Expose the LLM-counter callback + phase-events middleware on the agent
    # so task.py can attach both without needing to re-instantiate them.
    radar_agent._rr_llm_counter_cb  = _LLM_COUNTER_CB  # type: ignore[attr-defined]
    radar_agent._rr_phase_middleware = _phase_events_mw  # type: ignore[attr-defined]

    logger.info(
        f"[rr-agent] built mode={mode!r} "
        f"tools={len(tool_list)} subagents={len(subagent_list)} "
        f"middleware=[PhaseEnforcer, PhaseEvents] "
        f"response_format=ScanComplete "
        f"skills=5 memory=2"
    )
    logger.info(
        f"[rr-agent] subagent_names={[s['name'] for s in subagent_list]} "
        f"tool_names={[t.name for t in tool_list]}"
    )
    return radar_agent


# Subagent parallelism model — Wave 1.4 findings
# DeepAgents 0.6.8 ships TWO subagent flavors:
#   1. **SubAgent dict** (what we use)   — in-process compiled langgraph,
#      dispatched via SubAgentMiddleware.atask() which awaits
#      `subagent.ainvoke(state, config)`. When the orchestrator emits N
#      `task(...)` tool_calls in ONE assistant message, LangGraph's async
#      ToolNode runs them concurrently via `asyncio.gather`. Result: 4
#      `task()` calls → 4 concurrent subagent loops, all sharing this
#      Celery worker's event loop.
#   2. **AsyncSubAgent**                  — remote LangGraph Platform /
#      Agent Protocol server. The subagent runs on a SEPARATE service;
#      DeepAgents talks to it via langgraph_sdk.get_client(). Requires a
#      deployed LangGraph server endpoint (or self-hosted ASGI). Not
#      applicable to our single-Celery-worker deployment.
# We stay on flavor #1. The 10-20 min for 4 deep_reads observed pre-Wave-1
# was NOT a DeepAgents dispatch problem — it was a ROTATOR problem:
#   - 4-arm rr-strong pool + LiteLLM Router simple-shuffle
#   - 4 concurrent ainvoke calls each randomly picked one of 4 arms
#   - High collision probability → 429 → cascade serial cooldown waits
# Wave 1.2 (bandit-routed chain) + 1.3 (10-arm pool) eliminate this:
#   - FGTS-VA Thompson sampling picks DIFFERENT top-K arms across the
#     4 concurrent subagent calls (RNG-driven exploration)
#   - 2.5× wider candidate pool absorbs cooldowns without cascade
# Wave 1.5 (asyncio.Semaphore at task.py entry) caps parallel LLM dispatch
# so the orchestrator + 4 subagents + cascade retries don't burst past
# what NIM's 40 RPM / Groq's 30 RPM windows can absorb.
# Hook for v2: when horizontal scaling beyond one worker is needed,
# the AsyncSubAgent flavor is one option — but Celery-distributed
# subagents (workers picking up `deep_read_one_paper` tasks from a queue)
# is the cheaper path. Both deferred.
