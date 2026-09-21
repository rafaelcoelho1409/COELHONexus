"""Build-tab code synthesis — paper-extraction → complete Python.
Imperative Shell.

Reads the 5 structured fields a deep_read produced (money_angle, problem,
method, how_to_build, math) and asks the configured external endpoint to
write COMPLETE, runnable Python that the operator reads to spark ideas.

  - build_chat_model() — same external endpoint Planner/Synth use.
    Code-gen calls (150-400-line completions) share the endpoint with
    RR's other (much shorter) extraction calls; per-call timeouts in
    `resilient_ainvoke` bound each round.

Cache-invalidation contract: bump `params.CODE_SYNTH_PROMPT_VERSION`
whenever the system prompt or refine-loop logic changes. MinIO keys
embed the version so old cached outputs don't shadow the new prompt.
"""
from __future__ import annotations

import logging
from typing import Any

from . import domain, params, prompts
from .... import runtime


logger = logging.getLogger(__name__)


async def synth_code(finding: dict[str, Any]) -> dict[str, str]:
    """Generate complete Python from a finding's extraction.

    Args:
        finding: The radar_findings.digest_json row for one paper. Must carry
            `title`, `arxiv_id`, and an `extraction` dict with the 5 fields
            (money_angle, problem, method, how_to_build, math).

    Returns:
        {"code": "<python source>", "model_id": "<rotator-resolved arm>"}.
        Raises RuntimeError on empty output or fenced-block extraction
        failure — caller should NOT cache failures.
    """
    # Lazy imports — keep cold-start light and avoid pulling the chat
    # client into smoke tests that import this module.
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from domains.settings.chat import service as chat_service

    extraction = finding.get("extraction") or {}
    user_msg   = domain.build_user_message(finding, extraction)
    chain      = chat_service.build_chat_model()

    async def _call(messages: list) -> Any:
        return await runtime.service.resilient_ainvoke(
            chain, messages,
            operation    = "code_synth",
            timeout_s    = params.CALL_TIMEOUT_S,
            max_attempts = 2,
        )

    # Round 1 — generate.
    gen_response = await _call([
        SystemMessage(content = prompts.SYSTEM_PROMPT),
        HumanMessage(content  = user_msg),
    ])
    draft_raw   = domain.content_to_text(gen_response)
    if not draft_raw:
        raise RuntimeError("code_synth: empty draft from rotator")
    draft_code  = domain.extract_python_block(draft_raw)

    # Round 2 — critique (forces the model to surface completeness gaps).
    critique_response = await _call([
        SystemMessage(content = prompts.SYSTEM_PROMPT),
        HumanMessage(content  = user_msg),
        AIMessage(content     = f"```python\n{draft_code}\n```"),
        HumanMessage(content  = prompts.CRITIQUE_PROMPT),
    ])
    critique_raw = domain.content_to_text(critique_response)

    # If the critique says PASS, skip the revise round — saves a turn and
    # avoids the model "fixing" things that don't need fixing.
    if critique_raw.upper().startswith("PASS"):
        model_id = domain.resolve_model_id(gen_response) or "rr-strong"
        return {"code": draft_code, "model_id": model_id}

    # Round 3 — revise.
    revised_response = await _call([
        SystemMessage(content = prompts.SYSTEM_PROMPT),
        HumanMessage(content  = user_msg),
        AIMessage(content     = f"```python\n{draft_code}\n```"),
        HumanMessage(content  = prompts.CRITIQUE_PROMPT),
        AIMessage(content     = critique_raw),
        HumanMessage(content  = prompts.REVISE_PROMPT),
    ])
    revised_raw = domain.content_to_text(revised_response)
    if not revised_raw:
        # Revise failed — fall back to the draft. Better to ship something
        # than to fail closed.
        logger.warning(
            f"code_synth: revise pass empty for arxiv_id="
            f"{finding.get('arxiv_id')!r}; returning draft"
        )
        model_id = domain.resolve_model_id(gen_response) or "rr-strong"
        return {"code": draft_code, "model_id": model_id}
    revised_code = domain.extract_python_block(revised_raw)
    model_id     = domain.resolve_model_id(revised_response) or "rr-strong"
    return {"code": revised_code, "model_id": model_id}
