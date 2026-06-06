"""ycs/grader — LLM document-relevance grader with parallel `gather`.

Direct port of deprecated `services/youtube/grader.py`."""
from .prompts import GRADER_PROMPT_VERSION, GRADING_PROMPT
from .schemas import GradeResult
from .service import DocumentGrader


__all__ = [
    "DocumentGrader",
    "GRADER_PROMPT_VERSION",
    "GRADING_PROMPT",
    "GradeResult",
]
