"""Citation accuracy judge — for a chapter outline, does each chapter's
`key_concepts` list overlap meaningfully with the expected one? LLM judge
on a 1-5 rubric; routes through the rotator (free-tier).
"""
from __future__ import annotations

import json
import logging
import re


logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """You are a strict evaluator. Score the ACTUAL chapter outline against the EXPECTED outline on a 1-5 citation-accuracy scale.

INPUT:
{input_json}

EXPECTED outline:
{expected_json}

ACTUAL outline:
{actual_json}

Criteria (for each pair of expected/actual chapter):
1. Each ACTUAL chapter's key_concepts has ≥1 expected concept (semantic match, not lexical)
2. No spurious concepts that don't belong to the topic
3. Concept granularity matches (specific names, not categories)

Score:
- 5 = every chapter has high concept overlap; no spurious concepts
- 4 = high overlap, minor wording variance
- 3 = 1 chapter has weak overlap or 1 spurious concept
- 2 = 2 chapters with overlap issues
- 1 = systemic mismatch

Respond with ONLY a single integer 1-5. No prose."""


async def citation_accuracy(input_: dict, expected: dict, actual: dict) -> float:
    """LLM-judged citation accuracy on the chapter outline."""
    from domains.llm.rotator.chain import chat_judge_async
    prompt = _PROMPT_TEMPLATE.format(
        input_json    = json.dumps(input_,   ensure_ascii = False)[:2000],
        expected_json = json.dumps(expected, ensure_ascii = False)[:2000],
        actual_json   = json.dumps(actual,   ensure_ascii = False)[:4000],
    )
    try:
        raw = await chat_judge_async(prompt, max_tokens = 8, temperature = 0.0)
    except Exception as e:
        logger.warning(
            f"[citation_accuracy] judge call failed: {type(e).__name__}: {e}"
        )
        return 0.0
    m = re.search(r"[1-5]", raw)
    if m is None:
        return 0.0
    return float(m.group())
