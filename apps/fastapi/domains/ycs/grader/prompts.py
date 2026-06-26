"""ycs/grader — relevance-grading prompt + version marker.
Version
string is here per `docs/CODE-CONVENTIONS.md` §2: cache-invalidation
knobs live with the prompts they identify.

2026-06-16 — switched the binary grade ("relevant" / "not_relevant")
to a ternary with a `likely_relevant` middle class. Rationale lives
in `schemas.py::GradeResult`; in short: binary grading dropped ~60%
of DEEP sub-question retrievals because abstract sub-questions don't
match documents on literal phrasing, only on theme. The new middle
class lets the LLM signal "this is on-topic without being a direct
answer" — `service.py` keeps those, which closes the no-docs gap on
abstract sub-questions."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


GRADER_PROMPT_VERSION = "ternary-2026-06-16"


GRADING_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a relevance grader. Given a user question and a "
        "retrieved transcript excerpt, classify how useful the "
        "excerpt is for answering the question. Respond with EXACTLY "
        "one of:\n"
        "- 'relevant' — the excerpt directly answers the question or "
        "contains explicit evidence for it.\n"
        "- 'likely_relevant' — the excerpt is on-topic and touches "
        "the question's domain even though it doesn't answer it "
        "literally (e.g. discussing the theme by example, providing "
        "context, or describing a related pattern). Use this for "
        "ABSTRACT questions where literal-phrase matches are rare.\n"
        "- 'not_relevant' — the excerpt has no useful information "
        "for the question.\n\n"
        "Default to 'likely_relevant' over 'not_relevant' when "
        "uncertain — downstream synthesis is good at filtering "
        "weak evidence; missing relevant docs is a bigger cost than "
        "including a tangential one.",
    ),
    (
        "human",
        "Question: {question}\n\nDocument content:\n{document}",
    ),
])
