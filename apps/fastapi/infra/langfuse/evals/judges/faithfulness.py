"""Faithfulness judge — score an actual chapter outline against the
expected outline on a 1-5 rubric. Free-tier: routes through the
rotator's `chat_judge_async` (the same path the DD planner uses for its
USC vote).

Returns 1.0-5.0 on success, 0.0 when the judge call fails or its response
isn't parseable. The dataset runner records 0.0 the same way it records
any other score so a failed judge doesn't drop the item from the run.
"""
from __future__ import annotations

import json
import logging
import re


logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """You are a strict evaluator. Score the ACTUAL chapter outline against the EXPECTED outline on a 1-5 faithfulness scale.

INPUT:
{input_json}

EXPECTED outline:
{expected_json}

ACTUAL outline:
{actual_json}

Criteria:
1. Right number of chapters (actual within ±1 of expected)
2. Each title is concrete and specific (no "Introduction", "Overview", "Conclusion", "Getting Started")
3. Each chapter's key concepts overlap meaningfully with expected concepts
4. No two chapters cover the same scope

Score:
- 5 = all criteria met, semantic alignment
- 4 = all criteria met, slight rewording acceptable
- 3 = 1 criterion missed
- 2 = 2 criteria missed
- 1 = 3+ criteria missed or fundamentally wrong

Respond with ONLY a single integer 1-5. No prose."""


async def faithfulness(input_: dict, expected: dict, actual: dict) -> float:
    """Single-shot LLM-as-judge over the chapter-outline rubric."""
    from domains.llm.rotator.chain import chat_judge_async
    prompt = _PROMPT_TEMPLATE.format(
        input_json    = json.dumps(input_,    ensure_ascii = False)[:2000],
        expected_json = json.dumps(expected, ensure_ascii = False)[:2000],
        actual_json   = json.dumps(actual,   ensure_ascii = False)[:4000],
    )
    try:
        raw = await chat_judge_async(prompt, max_tokens = 8, temperature = 0.0)
    except Exception as e:
        logger.warning(
            f"[faithfulness] judge call failed: {type(e).__name__}: {e}"
        )
        return 0.0
    m = re.search(r"[1-5]", raw)
    if m is None:
        logger.debug(f"[faithfulness] non-numeric judge response: {raw!r}")
        return 0.0
    return float(m.group())
