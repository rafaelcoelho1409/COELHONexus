"""YCS observability domain — pure LangFuse payload builders (no I/O)."""
from __future__ import annotations


def langfuse_ycs_input(
    *,
    question: str,
    route: str,
    force_mode: str,
    channel_ids: list[str],
    thread_id: str,
) -> dict:
    return {
        "question": question,
        "route": route,
        "force_mode": force_mode or "",
        "channel_ids": list(channel_ids or []),
        "thread_id": thread_id,
    }


def langfuse_ycs_output(
    *,
    status: str,
    answer: str,
    mode: str,
    grounded: bool,
    citations: list,
    sub_questions: list | None = None,
    confidence_score: float | None = None,
    error: str | None = None,
) -> dict:
    output = {
        "status": status,
        "answer": answer[:4000],
        "mode": mode or "",
        "grounded": bool(grounded),
        "citation_count": len(citations or []),
    }
    if sub_questions is not None:
        output["sub_question_count"] = len(sub_questions or [])
    if confidence_score is not None:
        output["confidence_score"] = confidence_score
    if error:
        output["error"] = error
    return output
