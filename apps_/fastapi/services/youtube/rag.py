"""YCS end-to-end RAG pipeline.

Wraps the four validated services (ingestion / store / rotator-rerank /
rotator-LLM) into two functions the FastAPI router can call directly:

  - index_video(url)      one video → chunks in Qdrant (idempotent)
  - answer(question)      retrieve → rerank → generate (with citations)

Smoke-tested 2026-05-19 against Rick Astley (`?v=dQw4w9WgXcQ`):
  index_video → 2 chunks upserted
  answer("Will you ever give me up?") → "Never gonna give you up."
"""
from services.llm.chain import (
    chat_judge_bandit_async,
    rerank_via_router_async,
)
from services.youtube.ingestion import fetch_transcript
from services.youtube.store import ensure_collection, search, upsert_chunks


_ANSWER_PROMPT = """\
Answer the question using ONLY the context below. If the answer isn't in the
context, say "I don't know."

Context:
{context}

Question: {question}

Answer:"""


async def index_video(video_url: str) -> dict:
    """Fetch + chunk + embed + upsert one video. Idempotent on re-runs.

    Returns ``{video_id, title, lang, chunks_upserted}``. ``chunks_upserted``
    is ``0`` for caption-disabled videos (no transcript).
    """
    await ensure_collection()
    t = await fetch_transcript(video_url)
    chunks = await upsert_chunks(
        video_id=t["video_id"],
        title=t["title"],
        lang=t["lang"],
        transcript_text=t["transcript_text"],
    )
    return {
        "video_id": t["video_id"],
        "title": t["title"],
        "lang": t["lang"],
        "chunks_upserted": chunks,
    }


async def answer(
    question: str,
    top_k: int = 5,
    rerank_top_n: int = 3,
) -> dict:
    """Retrieve → rerank → generate. Returns answer + citations.

    Returns ``{answer, citations, model, latency_s}``. ``citations`` is the
    list of payload entries that fed the LLM context (post-rerank), in
    descending rerank order.
    """
    hits = await search(question, top_k=top_k)
    if not hits:
        return {
            "answer": "I don't know.",
            "citations": [],
            "model": None,
            "latency_s": None,
        }
    ranked = await rerank_via_router_async(
        query=question,
        documents=[h["content"] for h in hits],
    )
    top = [hits[i] for i, _ in ranked[:rerank_top_n]]
    context = "\n\n---\n\n".join(h["content"] for h in top)
    text, meta = await chat_judge_bandit_async(
        _ANSWER_PROMPT.format(context=context, question=question),
        max_tokens=400,
        temperature=0.0,
        timeout_s=60.0,
    )
    return {
        "answer": text,
        "citations": [
            {
                "video_id": h["video_id"],
                "title": h["title"],
                "chunk_index": h["chunk_index"],
                "total_chunks": h["total_chunks"],
            }
            for h in top
        ],
        "model": meta.get("deployment"),
        "latency_s": meta.get("latency_s"),
    }
