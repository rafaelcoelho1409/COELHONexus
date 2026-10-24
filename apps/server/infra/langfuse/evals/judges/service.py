"""judges service — shared LLM-judge invocation (I/O shell).

Single choke point for every rubric judge: one prompt in, one 1-5 float
out, 0.0 on any failure. `label` tags the log line per judge.
"""
from __future__ import annotations
import domains
from . import domain, params, prompts

import logging


logger = logging.getLogger(__name__)


async def run_rubric_judge(
    prompt: str,
    label: str,
    *,
    chat_fn=None,
) -> float:
    """Single-shot deterministic judge call + parse. Never raises.

    `chat_fn` is the injectable LLM edge — `await chat_fn(prompt,
    max_tokens, temperature) -> str`. Defaults to the settings chat
    endpoint (the legacy infra→domains edge — `domains/__init__.py`'s
    2026-09-24 lazy-import fix means this costs nothing until actually
    called); pass a fake in tests or a custom endpoint in notebooks to
    bypass it entirely.
    """
    if chat_fn is None:
        async def chat_fn(prompt, max_tokens, temperature):
            return await domains.settings.chat.service.chat_judge_async(
                prompt, max_tokens = max_tokens, temperature = temperature,
            )
    try:
        raw = await chat_fn(
            prompt,
            params.JUDGE_MAX_TOKENS,
            params.JUDGE_TEMPERATURE,
        )
    except Exception as e:
        logger.warning(
            f"[{label}] judge call failed: {type(e).__name__}: {e}"
        )
        return 0.0
    score = domain.parse_rubric_score(raw)
    if score == 0.0:
        logger.debug(f"[{label}] non-numeric judge response: {raw!r}")
    return score


async def faithfulness(input_: dict, expected: dict, actual: dict, *, chat_fn=None) -> float:
    """Score an actual chapter outline against the expected outline on a
    1-5 rubric. Returns 1.0-5.0 on success, 0.0 when the judge call fails
    or its response isn't parseable."""
    prompt = domain.render_outline_prompt(prompts.FAITHFULNESS_PROMPT, input_, expected, actual)
    return await run_rubric_judge(prompt, "faithfulness", chat_fn = chat_fn)


async def citation_accuracy(input_: dict, expected: dict, actual: dict, *, chat_fn=None) -> float:
    """For a chapter outline, does each chapter's `key_concepts` list
    overlap meaningfully with the expected one? LLM judge, 1-5 rubric."""
    prompt = domain.render_outline_prompt(prompts.CITATION_ACCURACY_PROMPT, input_, expected, actual)
    return await run_rubric_judge(prompt, "citation_accuracy", chat_fn = chat_fn)


async def ragas_relevance(input_: dict, expected: dict, actual: dict, *, chat_fn=None) -> float:
    """RAGAS-style answer relevance for YCS Ask outputs — given (question,
    answer), score 1-5 for how well the answer addresses the question.

    Inputs:
      input_   {"question": "..."}
      expected {"answer": "...", "ground_truth": "..."} (optional)
      actual   {"answer": "..."}
    """
    prompt = domain.render_answer_prompt(prompts.RAGAS_RELEVANCE_PROMPT, input_, expected, actual)
    return await run_rubric_judge(prompt, "ragas_relevance", chat_fn = chat_fn)
