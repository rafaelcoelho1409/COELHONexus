"""judges prompts — LLM rubric templates, one per judge (no behavior).

Shared catalog: every prompt this suite sends, visible side by side.
Each template is consumed exactly once, by the judge file of the same name.
"""
from __future__ import annotations


FAITHFULNESS_PROMPT = """You are a strict evaluator. Score the ACTUAL chapter outline against the EXPECTED outline on a 1-5 faithfulness scale.

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



CITATION_ACCURACY_PROMPT = """You are a strict evaluator. Score the ACTUAL chapter outline against the EXPECTED outline on a 1-5 citation-accuracy scale.

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



RAGAS_RELEVANCE_PROMPT = """You are a strict evaluator. Score the ACTUAL answer's relevance to the QUESTION on a 1-5 scale.

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

