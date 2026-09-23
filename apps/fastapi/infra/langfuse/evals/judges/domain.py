"""judges domain — pure helpers (no I/O, deterministic)."""
from __future__ import annotations
from . import params, patterns

import json



def parse_rubric_score(raw: str | None) -> float:
    """First 1-5 digit in a judge response, else 0.0 (unparseable = failure)."""
    if not raw:
        return 0.0
    m = patterns.SCORE_RE.search(raw)
    if m is None:
        return 0.0
    return float(m.group())


def render_outline_prompt(template: str, input_: dict, expected: dict, actual: dict) -> str:
    """JSON-dump render for outline judges (faithfulness, citation_accuracy)."""
    return template.format(
        input_json    = json.dumps(input_,    ensure_ascii = False)[:params.OUTLINE_INPUT_CAP],
        expected_json = json.dumps(expected, ensure_ascii = False)[:params.OUTLINE_EXPECTED_CAP],
        actual_json   = json.dumps(actual,   ensure_ascii = False)[:params.OUTLINE_ACTUAL_CAP],
    )


def render_answer_prompt(template: str, input_: dict, expected: dict, actual: dict) -> str:
    """Key-picking render for the answer judge (ragas_relevance)."""
    return template.format(
        question        = (input_.get("question") or "")[:params.ANSWER_QUESTION_CAP],
        expected_answer = (expected.get("answer") or expected.get("ground_truth") or "(none)")[:params.ANSWER_EXPECTED_CAP],
        actual_answer   = (actual.get("answer")   or "")[:params.ANSWER_ACTUAL_CAP],
    )


async def novelty(input_: dict, expected: dict, actual: dict) -> float:
    """1.0 − Jaccard(actual ∩ prior, actual). Async for runner-interface
    uniformity only — the body is pure (no I/O).

    1.0 = all arxiv_ids are new, 0.0 = all appeared in prior digests.
    `expected` is ignored (freshness, not match-to-expected)."""
    _ = expected
    prior = set(input_.get("prior_arxiv_ids") or [])
    current = set(actual.get("arxiv_ids") or [])
    if not current:
        return 0.0
    overlap = prior & current
    return float(round(1.0 - (len(overlap) / len(current)), params.NOVELTY_DECIMALS))
