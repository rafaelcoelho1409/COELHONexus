"""Validated, bounded DeepAgent calls for Learning Guide editorial review."""
from __future__ import annotations
import domains
from . import entities, params
import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any


logger = logging.getLogger(__name__)


async def review_packet(
    reviewers: entities.ReportReviewer,
    packet: dict[str, Any],
    *,
    run_id: str,
    context_kind: str = "chapter",
) -> domains.bd.distill.schemas.ReportReviewOutput:
    """Try structured review once, then one bounded JSON fallback if needed."""
    return await asyncio.wait_for(
        _review_packet(
            reviewers,
            packet,
            run_id = run_id,
            context_kind = context_kind,
        ),
        timeout = params.TOTAL_TIMEOUT_S,
    )


async def _review_packet(
    reviewers: entities.ReportReviewer,
    packet: dict[str, Any],
    *,
    run_id: str,
    context_kind: str,
) -> domains.bd.distill.schemas.ReportReviewOutput:
    packet_json = json.dumps(packet, ensure_ascii = False)
    text_fallback_packet = (
        f"Review packet JSON:\n{packet_json}\n\n"
        "Your structured-output attempt did not produce a valid ReportReviewOutput. "
        "Return exactly one JSON object with a findings array. Each finding must include "
        "severity (note, warning, or critical), area (coverage, clarity, consistency, "
        "evidence, visuals, or decision_brief), issue, and recommendation. "
        "Return {\"findings\": []} if no useful findings exist. Do not include Markdown."
    )
    review_modes = (
        ("structured", reviewers.structured, packet_json),
        ("text_fallback", reviewers.text_fallback, text_fallback_packet),
    )

    for output_attempt, (mode, agent, user_content) in enumerate(
        review_modes[:params.OUTPUT_REPAIR_ATTEMPTS],
        start = 1,
    ):
        messages: list[Any] = [{"role": "user", "content": user_content}]
        async def invoke():
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd",
                run_id = run_id,
                node_id = "report_reviewer",
                stage = "distill",
            ):
                return await domains.settings.chat.service.resilient_ainvoke(
                    lambda: agent.ainvoke({"messages": messages}, config = {
                        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                    }),
                    attempts = params.PROVIDER_CALL_ATTEMPTS,
                    wait_base_s = domains.bd.distill.params.AGENT_CALL_RETRY_WAIT_BASE_S,
                )

        try:
            result = await invoke()
        except Exception as error:  # noqa: BLE001 — try the text-mode agent when structured output is unsupported.
            logger.warning(
                "[bd-distill] report reviewer agent failed context=%s mode=%s error_type=%s",
                context_kind,
                mode,
                type(error).__name__,
            )
            if mode == "structured":
                continue
            raise
        review = domains.bd.distill.domain.report_review_from_agent_result(result)
        if isinstance(review, domains.bd.distill.schemas.ReportReviewOutput):
            return review

        response_messages = result.get("messages") if isinstance(result, Mapping) else None
        logger.warning(
            "[bd-distill] report reviewer returned invalid output context=%s attempt=%s/%s structured_response=%s message_count=%s",
            context_kind,
            output_attempt,
            len(review_modes[:params.OUTPUT_REPAIR_ATTEMPTS]),
            isinstance(result, Mapping) and result.get("structured_response") is not None,
            len(response_messages) if isinstance(response_messages, list) else 0,
        )

    raise ValueError(
        "report reviewer returned no valid ReportReviewOutput after "
        f"{len(review_modes[:params.OUTPUT_REPAIR_ATTEMPTS])} DeepAgent attempts",
    )
