"""Pure readiness projection for the adaptive BD Ask book index."""
from __future__ import annotations
from . import schemas


def readiness(
    *,
    book_id: str,
    extraction_status: str | None,
    extraction_run_id: str | None,
    ingestion_run: dict | None,
    index_run: dict | None,
    current_index: dict | None,
    configured_embedding_model: str,
    updated_at: float | None,
) -> schemas.PipelineReadiness:
    """Project persisted stage summaries into one book readiness state."""
    indexed_model = (current_index or {}).get("embedding_model")
    normalized_configured = configured_embedding_model.casefold().strip()
    profile_matches = None
    if current_index is not None and normalized_configured not in {"", "auto"}:
        profile_matches = (
            normalized_configured == (indexed_model or "").casefold().strip()
        )

    warnings: list[str] = []
    if profile_matches is False:
        warnings.append(
            "The current Qdrant index uses a different embedding model. Reindex the book with the configured model."
        )
    if ingestion_run is not None and ingestion_run.get("status") == "failed":
        warnings.append(
            "The latest Ingestion run failed; the last published index may still be queryable."
        )
    if index_run is not None and index_run.get("status") == "failed":
        warnings.append(
            "The latest RAG indexing run failed; the last published index may still be queryable."
        )

    published_run_id = (current_index or {}).get("agentic_run_id")
    latest_run_id = (ingestion_run or {}).get("agentic_run_id")
    latest_is_published = (
        published_run_id is not None and published_run_id == latest_run_id
    )

    if (
        ingestion_run is not None
        and not latest_is_published
        and ingestion_run.get("status") in {"queued", "running"}
    ):
        state = "ingesting"
        if current_index is not None:
            warnings.append(
                "The previous retrieval index remains queryable while this Ingestion run is processing."
            )
    elif (
        ingestion_run is not None
        and not latest_is_published
        and ingestion_run.get("status") in {"failed", "cancelled"}
    ):
        state = "failed"
    elif (
        ingestion_run is not None
        and not latest_is_published
        and ingestion_run.get("status") == "done"
    ):
        if index_run is not None and index_run.get("status") in {"failed", "cancelled"}:
            state = "failed"
        else:
            state = "indexing"
        if current_index is not None:
            warnings.append(
                "The previous retrieval index remains queryable while this run's index is being built."
            )
    elif current_index is not None:
        state = "reindex_required" if profile_matches is False else "ready"
    elif ingestion_run is None:
        state = "not_extracted" if extraction_status != "done" else "awaiting_ingestion"
    elif ingestion_run.get("status") in {"queued", "running"}:
        state = "ingesting"
    elif (
        ingestion_run.get("status") in {"failed", "cancelled"}
        or index_run is not None
        and index_run.get("status") in {"failed", "cancelled"}
    ):
        state = "failed"
    else:
        state = "indexing"

    return schemas.PipelineReadiness(
        book_id=book_id,
        state=state,
        ready=state == "ready",
        queryable=current_index is not None,
        extraction_status=extraction_status,
        extraction_run_id=extraction_run_id,
        ingestion_run=ingestion_run,
        index_run=index_run,
        current_index=current_index,
        configured_embedding_model=configured_embedding_model,
        embedding_profile_matches=profile_matches,
        warnings=warnings,
        updated_at=updated_at,
    )
