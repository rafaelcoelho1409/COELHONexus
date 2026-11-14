"""Compose YCS DeepAgents and LangGraph with the existing ingestion domain."""
from __future__ import annotations

import ai
import domains, infra
from . import entities

import asyncio
import logging
import threading
from typing import Any

from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
from langchain_core.documents import Document


logger = logging.getLogger(__name__)


def build_graph_extractor(model: Any, *, run_id: str | None = None) -> entities.GraphExtractor:
    """Build one DeepAgent and one reusable transcript-extraction graph per Neo4j task."""
    return entities.GraphExtractor(
        graph = ai.ycs.graphs.ingestion.graph_extraction.graphs.graph_extraction_graph(),
        agent = ai.ycs.agents.ingestion.graph_extractor.agent.graph_extractor(model),
        run_id = run_id,
    )


async def extract_graph_document(
    extractor: entities.GraphExtractor,
    document: Document,
) -> GraphDocument:
    """Run the evidence-checked graph and adapt accepted candidates to LangChain graph objects."""
    metadata = document.metadata if isinstance(document.metadata, dict) else {}
    state = await extractor.graph.ainvoke(
        {
            "transcript": document.page_content or "",
            "metadata": {
                key: str(metadata.get(key) or "")
                for key in ("video_id", "title", "channel")
            },
        },
        context = ai.ycs.graphs.ingestion.graph_extraction.entities.GraphExtractionContext(
            agent = extractor.agent,
            run_id = extractor.run_id,
        ),
    )
    accepted = ai.ycs.agents.schemas.GraphExtraction.model_validate(
        state.get("accepted_extraction", {}),
    )
    nodes_by_id: dict[str, Node] = {}
    for entity in accepted.entities:
        nodes_by_id.setdefault(
            entity.entity_id.casefold(),
            Node(id = entity.entity_id, type = entity.entity_type),
        )
    relationships: list[Relationship] = []
    for relation in accepted.relationships:
        source = nodes_by_id.get(relation.source_id.casefold())
        target = nodes_by_id.get(relation.target_id.casefold())
        if source is None or target is None:
            continue
        relationships.append(Relationship(
            source = source,
            target = target,
            type = relation.relationship_type,
        ))
    return GraphDocument(
        nodes = list(nodes_by_id.values()),
        relationships = relationships,
        source = document,
    )


class _IngestionProgressReporter:
    """Translate parallel graph-node progress into the existing Celery UI contract."""

    def __init__(
        self,
        video_ids: list[str],
        callback: Any,
        include_transcription: bool,
    ) -> None:
        self.video_ids = list(dict.fromkeys(video_ids))
        self.callback = callback
        self.include_transcription = include_transcription
        self._lock = threading.RLock()
        self.metadata_done: set[str] = set()
        self.metadata_items: dict[str, dict[str, Any]] = {}
        self.transcription_completed_ids: set[str] = set()
        self.transcription_failed_ids: set[str] = set()
        self.no_transcript_ids: set[str] = set()
        self.metadata_indexed_ids: set[str] = set()
        self.metadata_index_failed_ids: set[str] = set()
        self.elasticsearch_completed_ids: set[str] = set()
        self.elasticsearch_failed_ids: set[str] = set()
        self._elasticsearch_started = False
        self._dispatched_ids: list[str] = []
        self._dispatched_id_set: set[str] = set()
        self._partition_totals: dict[str, int] = {}

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.callback is None:
            return
        try:
            payload["node_progress"] = self._node_progress()
            self.callback(payload)
        except Exception as error:
            logger.warning(
                f"[ycs:ingestion:graph] progress callback failed: "
                f"{type(error).__name__}: {error}"
            )

    def _all_items(self) -> list[dict[str, Any]]:
        return [
            self.metadata_items[video_id]
            for video_id in self.video_ids
            if video_id in self.metadata_items
        ]

    @property
    def all_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._all_items()

    @property
    def dispatched_ids(self) -> list[str]:
        with self._lock:
            return list(self._dispatched_ids)

    def register_dispatched(
        self,
        video_ids: list[str],
        transcript_docs: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            for video_id in video_ids:
                if video_id not in self._dispatched_id_set:
                    self._dispatched_id_set.add(video_id)
                    self._dispatched_ids.append(video_id)
            for document in transcript_docs:
                video_id = str(document.get("video_id") or "")
                part_total = document.get("part_total")
                if video_id and part_total:
                    self._partition_totals[video_id] = int(part_total)

    def partition_total(self, video_id: str) -> int | None:
        with self._lock:
            return self._partition_totals.get(video_id)

    async def fail_unreported_stores(self, redis: Any, extract_id: str) -> None:
        for phase in ("qdrant", "neo4j"):
            for video_id in self.dispatched_ids:
                try:
                    await ai.ycs.nodes.ingestion.support.mark_failed_if_unreported(
                        redis,
                        extract_id,
                        phase,
                        video_id,
                        "Ingestion graph stopped before this store node reported an outcome.",
                        self.partition_total(video_id),
                    )
                except Exception as error:
                    logger.warning(
                        f"[ycs:ingestion:graph] unable to reconcile {phase}/{video_id}: "
                        f"{type(error).__name__}: {error}"
                    )

    def _failed_ids(self) -> list[str]:
        return sorted(
            self.transcription_failed_ids | self.elasticsearch_failed_ids,
        )

    def _node_progress(self) -> dict[str, dict[str, Any]]:
        playwright_completed = (
            self.metadata_done - self.transcription_failed_ids
            if not self.include_transcription
            else self.transcription_completed_ids
        )
        playwright_terminal = (
            self.metadata_done
            if not self.include_transcription
            else (
                self.transcription_completed_ids
                | self.transcription_failed_ids
                | self.no_transcript_ids
            )
        )
        elasticsearch_failed = (
            self.metadata_index_failed_ids
            | self.transcription_failed_ids
            | self.elasticsearch_failed_ids
        )
        elasticsearch_completed = (
            self.metadata_indexed_ids
            if not self.include_transcription
            else (
                self.metadata_indexed_ids
                & (self.elasticsearch_completed_ids | self.no_transcript_ids)
            )
        ) - elasticsearch_failed
        elasticsearch_terminal = (
            elasticsearch_completed
            | elasticsearch_failed
            | (self.metadata_indexed_ids & self.no_transcript_ids)
        )
        return {
            "playwright": {
                "phase": "transcription",
                "current": len(playwright_terminal),
                "total": len(self.video_ids),
                "completed_ids": sorted(playwright_completed),
                "failed_ids": sorted(self.transcription_failed_ids),
                "no_transcript_ids": sorted(self.no_transcript_ids),
            },
            "elasticsearch": {
                "phase": "es_indexing" if self._elasticsearch_started else "metadata",
                "current": len(elasticsearch_terminal),
                "total": len(self.video_ids),
                "completed_ids": sorted(elasticsearch_completed),
                "failed_ids": sorted(elasticsearch_failed),
                "no_transcript_ids": sorted(self.no_transcript_ids),
            },
        }

    def _transcription_payload(self) -> dict[str, Any]:
        return {
            "phase": "transcription",
            "current": len(self.transcription_completed_ids | self.transcription_failed_ids | self.no_transcript_ids),
            "total": len(self.video_ids),
            "completed_ids": sorted(self.transcription_completed_ids),
            "failed_ids": self._failed_ids(),
            "no_transcript_ids": sorted(self.no_transcript_ids),
            "all_items": self._all_items(),
        }

    def metadata_finished(
        self,
        metadata_by_id: dict[str, dict[str, Any]],
        failed_ids: list[str],
    ) -> None:
        with self._lock:
            self.metadata_items.update(metadata_by_id)
            self.metadata_done.update(metadata_by_id)
            self.metadata_done.update(failed_ids)
            self.transcription_failed_ids.update(failed_ids)
            current = len(self.metadata_done)
            phase = "metadata_done" if current >= len(self.video_ids) else "metadata"
            self._emit({
                "phase": phase,
                "current": current,
                "total": len(self.video_ids),
                "failed_ids": self._failed_ids(),
                "all_items": self._all_items(),
            })

    def metadata_index_finished(
        self,
        indexed_ids: list[str],
        failed_ids: list[str],
    ) -> None:
        with self._lock:
            self._elasticsearch_started = True
            self.metadata_indexed_ids.update(indexed_ids)
            self.metadata_index_failed_ids.update(failed_ids)
            self._emit({
                "phase": "metadata_indexing",
                "current": len(self.metadata_indexed_ids | self.metadata_index_failed_ids),
                "total": len(self.video_ids),
                "failed_ids": self._failed_ids(),
                "all_items": self._all_items(),
            })

    def elasticsearch_started(self) -> None:
        with self._lock:
            self._elasticsearch_started = True
            self._emit({
                "phase": "es_indexing",
                "current": len(self.elasticsearch_completed_ids | self.elasticsearch_failed_ids),
                "total": len(self.video_ids),
                "failed_ids": self._failed_ids(),
                "all_items": self._all_items(),
            })

    def transcription_finished(
        self,
        video_id: str,
        *,
        success: bool,
        no_transcript: bool = False,
    ) -> None:
        with self._lock:
            if no_transcript:
                self.no_transcript_ids.add(video_id)
                self.transcription_completed_ids.discard(video_id)
                self.transcription_failed_ids.discard(video_id)
            elif success:
                self.transcription_completed_ids.add(video_id)
                self.transcription_failed_ids.discard(video_id)
            else:
                self.transcription_failed_ids.add(video_id)
                self.transcription_completed_ids.discard(video_id)
            self._emit(self._transcription_payload())

    def elasticsearch_finished(self, video_id: str, *, success: bool) -> None:
        with self._lock:
            if success:
                self.elasticsearch_completed_ids.add(video_id)
                self.elasticsearch_failed_ids.discard(video_id)
            else:
                self.elasticsearch_failed_ids.add(video_id)
                self.elasticsearch_completed_ids.discard(video_id)
            self._emit({
                "phase": "es_indexing",
                "current": len(self.elasticsearch_completed_ids | self.elasticsearch_failed_ids),
                "total": len(self.video_ids),
                "completed_ids": sorted(self.transcription_completed_ids),
                "failed_ids": self._failed_ids(),
                "no_transcript_ids": sorted(self.no_transcript_ids),
                "all_items": self._all_items(),
            })


async def run_ingestion_graph_async(
    video_ids: list[str],
    include_transcription: bool,
    languages: list[str] | None,
    progress_cb: Any = None,
    extract_id: str | None = None,
) -> dict[str, Any]:
    """Run the four-node ingestion graph in bounded, streaming batches.

    The graph owns stage execution and parallel Qdrant/Neo4j fan-out. The
    existing Celery task remains the run boundary, and Redis remains the
    source of truth for cancellation and store progress.
    """
    video_ids = list(dict.fromkeys(video_ids))
    graph = ai.ycs.graphs.ingestion.graphs.ingestion_graph()
    reporter = _IngestionProgressReporter(video_ids, progress_cb, include_transcription)
    if progress_cb:
        progress_cb({"phase": "metadata", "current": 0, "total": len(video_ids)})

    es = infra.elasticsearch.service.new_es()
    transcript_service = None
    redis = None

    async def cancel_check() -> bool:
        if redis is None or extract_id is None:
            return False
        return await domains.ycs.ingestion.pipeline_task.service.is_pipeline_cancelled(redis, extract_id)

    try:
        if extract_id:
            redis = domains.ycs.ingestion.pipeline_task.service.build_redis_client()
        if extract_id and include_transcription and video_ids:
            await domains.ycs.ingestion.pipeline_task.service.set_phase_total(
                redis,
                extract_id,
                "neo4j",
                len(video_ids),
            )
            await domains.ycs.ingestion.pipeline_task.service.set_phase_total(
                redis,
                extract_id,
                "qdrant",
                len(video_ids),
            )
        if include_transcription and video_ids:
            transcript_service = await domains.ycs.ingestion.transcript.service.init_transcript_service(
                max_concurrent = domains.ycs.ingestion.transcript.params.MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries = 3,
            )

        chunk_size = domains.ycs.ingestion.graph_builder.params.EXTRACT_CONCURRENCY
        chunks = [video_ids[index:index + chunk_size] for index in range(0, len(video_ids), chunk_size)]
        concurrency = asyncio.Semaphore(ai.ycs.graphs.ingestion.params.MAX_CONCURRENT_BATCHES)
        context = ai.ycs.graphs.ingestion.entities.IngestionContext(
            es_client = es,
            extractor = domains.ycs.ingestion.extract.service.get_extractor(),
            transcript_service = transcript_service,
            run_id = extract_id,
            include_transcription = include_transcription,
            languages = languages,
            total_videos = len(video_ids),
            reporter = reporter,
            qdrant_semaphore = asyncio.Semaphore(
                domains.ycs.ingestion.graph_builder.params.EXTRACT_CONCURRENCY,
            ),
            neo4j_semaphore = asyncio.Semaphore(
                ai.ycs.graphs.ingestion.params.MAX_CONCURRENT_NEO4J_BATCHES,
            ),
            cancel_check = cancel_check if redis is not None else None,
        )

        async def run_chunk(chunk: list[str]) -> dict[str, Any] | None:
            async with concurrency:
                if await cancel_check():
                    logger.info(f"[ycs:ingestion:graph] {extract_id}: skipped queued batch after cancellation")
                    return None
                return await graph.ainvoke(
                    {
                        "video_ids": chunk,
                        "extract_id": extract_id,
                    },
                    context = context,
                )

        outcomes = await asyncio.gather(
            *(run_chunk(chunk) for chunk in chunks),
            return_exceptions = True,
        )
        graph_errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if graph_errors:
            raise graph_errors[0]
        completed = [outcome for outcome in outcomes if outcome is not None]

        metadata_indexed = 0
        metadata_failed = 0
        transcript_indexed = 0
        transcript_failed = 0
        transcript_cached = 0
        transcript_fetch_failed = 0
        transcript_no_transcript = 0
        dispatched_ids: list[str] = reporter.dispatched_ids
        all_items: list[dict[str, Any]] = []
        for outcome in completed:
            metadata_result = outcome.get("metadata_result", {})
            metadata_indexed += int(metadata_result.get("indexed", 0) or 0)
            metadata_failed += int(metadata_result.get("failed", 0) or 0)
            transcription_result = outcome.get("transcription_result", {})
            transcript_indexed += int(transcription_result.get("indexed", 0) or 0)
            transcript_failed += int(transcription_result.get("failed", 0) or 0)
            transcript_cached += int(transcription_result.get("cached", 0) or 0)
            transcript_stats = outcome.get("transcription_stats", {})
            transcript_fetch_failed += int(transcript_stats.get("fetched_failed", 0) or 0)
            transcript_no_transcript += int(transcript_stats.get("no_transcript", 0) or 0)
            dispatched_ids.extend(outcome.get("dispatched_ids", []))
            all_items.extend(outcome.get("metadata_videos", []))

        if extract_id:
            await domains.ycs.ingestion.extract.service.dispatch_streaming_totals(
                extract_id,
                video_ids,
                list(dict.fromkeys(dispatched_ids)),
                include_transcription,
            )

        return {
            "total_videos": len(video_ids),
            "metadata": {"indexed": metadata_indexed, "failed": metadata_failed},
            "transcriptions": {
                "indexed": transcript_indexed,
                "failed": transcript_failed,
                "cached": transcript_cached,
                "fetch_failed": transcript_fetch_failed,
                "no_transcript": transcript_no_transcript,
            },
            "all_items": reporter.all_items or all_items,
            "no_transcript_ids": sorted(reporter.no_transcript_ids),
            "dispatched_ids": list(dict.fromkeys(dispatched_ids)),
            "status": "cancelled" if await cancel_check() else "complete",
        }
    except Exception:
        if extract_id and redis is not None:
            await reporter.fail_unreported_stores(redis, extract_id)
            await domains.ycs.ingestion.extract.service.dispatch_streaming_totals(
                extract_id,
                video_ids,
                reporter.dispatched_ids,
                include_transcription,
            )
        raise
    finally:
        try:
            if transcript_service is not None:
                await domains.ycs.ingestion.transcript.service.close_transcript_service()
        finally:
            try:
                if redis is not None:
                    await redis.close()
            finally:
                await es.close()
