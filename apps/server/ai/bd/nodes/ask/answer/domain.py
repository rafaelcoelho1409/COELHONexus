"""Pure answer and citation formatting rules for BD Ask."""
from __future__ import annotations
from . import patterns


def format_citations(state: dict) -> dict:
    documents = state.get("documents") or []
    requested = set(state.get("evidence_ids") or [])
    selected = [item for item in documents if item.get("id") in requested]
    if not selected and state.get("grounded"):
        selected = documents[:3]
    citations = []
    seen = set()
    for item in selected:
        citation = item.get("citation") or {}
        key = (citation.get("book_id"), citation.get("page"), citation.get("title"), citation.get("source"))
        if key not in seen:
            seen.add(key)
            citations.append(citation)
    answer = patterns.INLINE_CITATION_RE.sub("", str(state.get("generation", ""))).strip()
    if citations:
        markers = [
            f"[Page: {item['page']}]" if item.get("page") else f"[Source: {item.get('title', 'Book source')}]"
            for item in citations
        ]
        answer = f"{answer.rstrip()}\n\nSources: {' '.join(markers)}"
    return {"generation": answer, "citations": citations}
