"""ycs/grader — LLM document-relevance grader with parallel `gather`.
"""
from .prompts import GRADER_PROMPT_VERSION, GRADING_PROMPT
from .schemas import GradeResult
from .service import DocumentGrader


__all__ = [
    "DocumentGrader",
    "GRADER_PROMPT_VERSION",
    "GRADING_PROMPT",
    "GradeResult",
]
