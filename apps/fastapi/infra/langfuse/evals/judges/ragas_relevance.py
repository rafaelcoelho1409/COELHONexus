"""RAGAS-style answer relevance judge — given (question, answer), score
1-5 for how well the answer addresses the question.

Used for YCS Ask outputs. Free-tier: LLM judge via rotator.

Inputs:
  input_   {"question": "..."}
  expected {"answer": "...", "ground_truth": "..."} (optional)
  actual   {"answer": "..."}
"""
from __future__ import annotations

import json
import logging
import re


logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """You are a strict evaluator. Score the ACTUAL answer's relevance to the QUESTION on a 1-5 scale.

QUESTION:
{question}

EXPECTED (reference answer, when available):
{expected_answer}

ACTUAL answer:
{actual_answer}

Criteria:
1. Directly addresses the asked question (no off-topic content)
2. Information is grounded (no hallucinated specifics)
3. Completeness — covers what the reference does
4. Concision — no unnecessary preamble or padding

Score:
- 5 = answers precisely + fully + grounded
- 4 = answers fully w/ minor wording variance
- 3 = partial answer or some off-topic content
- 2 = barely answers OR has hallucination
- 1 = fails to answer or wrong

Respond with ONLY a single integer 1-5. No prose."""


async def ragas_relevance(input_: dict, expected: dict, actual: dict) -> float:
    """LLM-judged answer relevance for YCS Ask outputs."""
    from domains.llm.rotator.chain import chat_judge_async
    prompt = _PROMPT_TEMPLATE.format(
        question        = (input_.get("question") or "")[:1500],
        expected_answer = (expected.get("answer") or expected.get("ground_truth") or "(none)")[:2000],
        actual_answer   = (actual.get("answer")   or "")[:3000],
    )
    try:
        raw = await chat_judge_async(prompt, max_tokens = 8, temperature = 0.0)
    except Exception as e:
        logger.warning(
            f"[ragas_relevance] judge call failed: {type(e).__name__}: {e}"
        )
        return 0.0
    m = re.search(r"[1-5]", raw)
    if m is None:
        return 0.0
    return float(m.group())
