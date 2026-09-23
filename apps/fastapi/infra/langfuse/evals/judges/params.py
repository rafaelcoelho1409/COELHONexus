"""judges params — shared truncation caps + judge-call kwargs (no behavior).

One place for the numbers duplicated across the rubric judges, so a
retune touches a single file. Grouped by judge family: outline judges
(faithfulness, citation_accuracy) share caps; the answer judge
(ragas_relevance) has its own.
"""
from __future__ import annotations


# Outline judges — JSON blobs into the rubric template.
OUTLINE_INPUT_CAP    = 2000
OUTLINE_EXPECTED_CAP = 2000
OUTLINE_ACTUAL_CAP   = 4000

# Answer judge — question/answer slices into the relevance template.
ANSWER_QUESTION_CAP = 1500
ANSWER_EXPECTED_CAP = 2000
ANSWER_ACTUAL_CAP   = 3000

# Shared LLM-judge call shape — single-shot, deterministic.
JUDGE_MAX_TOKENS  = 8
JUDGE_TEMPERATURE = 0.0

# Novelty output precision (presentation only, not part of the definition).
NOVELTY_DECIMALS = 4
