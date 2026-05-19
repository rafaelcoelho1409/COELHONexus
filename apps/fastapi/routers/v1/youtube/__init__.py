"""YCS feature router — v0 single-endpoint slice.

POST /runs  body={video_url, question}  ->  index_video → answer.

Synchronous for v0 — index_video runs in-request (10-30s typical for the
yt-dlp + embedding round-trip). When this grows we split per-concern
(routers/v1/youtube/{runs,ingestion,search}.py) and move ingestion to
Celery, mirroring routers/v1/docs_distiller/.
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.youtube.rag import answer, index_video


router = APIRouter()


class RunRequest(BaseModel):
    video_url: str = Field(..., description="YouTube watch URL")
    question: str = Field(..., min_length=1, description="Question to answer over the video transcript")


@router.post("/runs")
async def create_run(req: RunRequest) -> dict:
    """Ingest one video (idempotent) then answer one question over it."""
    indexed = await index_video(req.video_url)
    response = await answer(req.question)
    return {"indexed": indexed, **response}
