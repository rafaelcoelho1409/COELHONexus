"""Coordinate the parallel YCS ingestion graph and translate its progress."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import ai
import infra

from ... import rag
from .. import domain, extract, pipeline_task, transcript

logger = logging.getLogger(__name__)


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
        except Exception as error:  # noqa: BLE001 - report progress callback failures without stopping ingestion.
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
                    await pipeline_task.service.mark_failed_if_unreported(
                        redis,
                        extract_id,
                        phase,
                        video_id,
                        "Ingestion graph stopped before this store node reported an outcome.",
                        self.partition_total(video_id),
                    )
                except Exception as error:  # noqa: BLE001 - reconcile failures are logged while other items continue.
                    logger.warning(
                        f"[ycs:ingestion:graph] unable to reconcile {phase}/{video_id}: "
                        f"{type(error).__name__}: {error}"
                    )

    def _failed_ids(self) -> list[str]:
        return sorted(
            self.transcription_failed_ids | self.elasticsearch_failed_ids,
        )

    @staticmethod
    def _node_state(
        *,
        started: bool,
        current: int,
        total: int,
        failed_ids: set[str],
    ) -> str:
        if not started:
            return "PENDING"
        if total == 0:
            return "SUCCESS"
        if current >= total:
            if not failed_ids:
                return "SUCCESS"
            return "FAILURE" if len(failed_ids) >= total else "PARTIAL"
        return "PROGRESS"

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
                "state": self._node_state(
                    started = True,
                    current = len(playwright_terminal),
                    total = len(self.video_ids),
                    failed_ids = self.transcription_failed_ids,
                ),
                "phase": "transcription",
                "current": len(playwright_terminal),
                "total": len(self.video_ids),
                "completed_ids": sorted(playwright_completed),
                "failed_ids": sorted(self.transcription_failed_ids),
                "no_transcript_ids": sorted(self.no_transcript_ids),
            },
            "elasticsearch": {
                "state": self._node_state(
                    started = self._elasticsearch_started,
                    current = len(elasticsearch_terminal),
                    total = len(self.video_ids),
                    failed_ids = elasticsearch_failed,
                ),
                "phase": "es_indexing" if self._elasticsearch_started else "metadata",
                "current": len(elasticsearch_terminal),
                "total": len(self.video_ids),
                "completed_ids": sorted(elasticsearch_completed),
                "failed_ids": sorted(elasticsearch_failed),
                "no_transcript_ids": sorted(self.no_transcript_ids),
            },
        }

    @property
    def node_progress(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._node_progress()

    def seed_from_checkpoint(self, replay_node: str, state: dict[str, Any]) -> None:
        """Restore the already-completed node counters that precede a replay target."""
        if replay_node == "playwright":
            return
        with self._lock:
            metadata_videos = state.get("metadata_videos", [])
            metadata_by_id = {
                str(video["id"]): extract.domain.project_video_meta(video)
                for video in metadata_videos
                if isinstance(video, dict) and video.get("id")
            }
            self.metadata_items.update(metadata_by_id)
            self.metadata_done.update(metadata_by_id)
            fetch_failed_ids = {
                str(video_id) for video_id in state.get("fetch_failed_ids", []) if video_id
            }
            self.metadata_done.update(fetch_failed_ids)
            self.transcription_failed_ids.update(fetch_failed_ids)
            self.no_transcript_ids.update(
                str(video_id) for video_id in state.get("no_transcript_ids", []) if video_id
            )
            completed_transcription_ids = {
                str(video_id) for video_id in state.get("cached_ids", []) if video_id
            }
            for document in state.get("transcript_docs", []):
                if isinstance(document, dict):
                    video_id = str(document.get("parent_video_id") or document.get("video_id") or "")
                    if video_id:
                        completed_transcription_ids.add(domain.parent_video_id(video_id))
            self.transcription_completed_ids.update(completed_transcription_ids - fetch_failed_ids)

            if replay_node in {"qdrant", "neo4j"}:
                metadata_result = state.get("metadata_result", {})
                self.metadata_indexed_ids.update(
                    str(video_id) for video_id in metadata_result.get("indexed_ids", []) if video_id
                )
                self.metadata_index_failed_ids.update(
                    str(video_id) for video_id in metadata_result.get("failed_ids", []) if video_id
                )
                dispatched_ids = [
                    str(video_id) for video_id in state.get("dispatched_ids", []) if video_id
                ]
                index_failed_ids = {
                    str(video_id) for video_id in state.get("index_failed_ids", []) if video_id
                }
                self.elasticsearch_completed_ids.update(set(dispatched_ids) - index_failed_ids)
                self.elasticsearch_failed_ids.update(index_failed_ids)
                self._elasticsearch_started = True
                self.register_dispatched(dispatched_ids, state.get("transcript_docs", []))

    def started(self, replay_node: str | None = None) -> None:
        with self._lock:
            self._emit({
                "phase": "streaming" if replay_node in {"qdrant", "neo4j"} else "metadata",
                "current": 0 if replay_node == "playwright" or replay_node is None else len(self.metadata_done),
                "total": len(self.video_ids),
            })

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


async def _checkpoint_before_node(graph: Any, config: dict[str, Any], node_id: str) -> dict[str, Any]:
    async for snapshot in graph.aget_state_history(config):
        if node_id in tuple(snapshot.next or ()) and isinstance(snapshot.config, dict):
            return snapshot.config
    raise ValueError(
        f"No saved YCS ingestion checkpoint exists before {node_id!r}; run the current workflow once before node replay."
    )


async def validate_replay_checkpoints(
    video_ids: list[str],
    checkpoint_extract_id: str,
    node_id: str,
    checkpointer: Any,
) -> None:
    """Reject a stage replay before dispatch unless every original chunk has its pre-node checkpoint."""
    if node_id not in {"playwright", "elasticsearch", "qdrant", "neo4j"}:
        raise ValueError(f"unknown YCS ingestion node {node_id!r}")
    graph = ai.ycs.graphs.ingestion.graphs.ingestion_graph(checkpointer=checkpointer)
    chunk_size = rag.source.indexes.neo4j_index.params.EXTRACT_CONCURRENCY
    chunks = [video_ids[index:index + chunk_size] for index in range(0, len(video_ids), chunk_size)]
    for index, _chunk in enumerate(chunks):
        config = {"configurable": {"thread_id": f"ycs-ingestion:{checkpoint_extract_id}:{index}"}}
        await _checkpoint_before_node(graph, config, node_id)


async def run_ingestion_graph_async(
    video_ids: list[str],
    include_transcription: bool,
    languages: list[str] | None,
    progress_cb: Any = None,
    extract_id: str | None = None,
    checkpointer: Any | None = None,
    checkpoint_extract_id: str | None = None,
    replay_from_node: str | None = None,
    preserved_node_progress: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the four-node ingestion graph in bounded, streaming batches.

    The graph owns stage execution and parallel Qdrant/Neo4j fan-out. The
    existing Celery task remains the run boundary, and Redis remains the
    source of truth for cancellation and store progress.
    """
    video_ids = list(dict.fromkeys(video_ids))
    graph = ai.ycs.graphs.ingestion.graphs.ingestion_graph(checkpointer=checkpointer)
    reporter = _IngestionProgressReporter(video_ids, progress_cb, include_transcription)
    if replay_from_node is not None and (checkpointer is None or checkpoint_extract_id is None):
        raise ValueError("YCS node replay requires its saved graph checkpoint")

    es = infra.elasticsearch.service.new_es()
    transcript_service = None
    redis = None

    async def cancel_check() -> bool:
        if redis is None or extract_id is None:
            return False
        return await pipeline_task.service.is_pipeline_cancelled(redis, extract_id)

    try:
        if extract_id:
            redis = pipeline_task.service.build_redis_client()
        if extract_id and include_transcription and video_ids:
            if replay_from_node == "qdrant":
                phases_to_initialize = ("qdrant",)
            elif replay_from_node == "neo4j":
                phases_to_initialize = ("neo4j",)
            else:
                phases_to_initialize = ("neo4j", "qdrant")
            for phase in phases_to_initialize:
                await pipeline_task.service.set_phase_total(
                    redis,
                    extract_id,
                    phase,
                    len(video_ids),
                )
        should_fetch_transcripts = replay_from_node in {None, "playwright"}
        if include_transcription and video_ids and should_fetch_transcripts:
            transcript_service = await transcript.service.init_transcript_service(
                max_concurrent = transcript.params.MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries = 3,
            )

        chunk_size = rag.source.indexes.neo4j_index.params.EXTRACT_CONCURRENCY
        chunks = [video_ids[index:index + chunk_size] for index in range(0, len(video_ids), chunk_size)]
        replay_checkpoints: dict[int, dict[str, Any]] = {}
        if replay_from_node is not None and checkpointer is not None and checkpoint_extract_id:
            for chunk_index, _chunk in enumerate(chunks):
                base_config = {
                    "configurable": {
                        "thread_id": f"ycs-ingestion:{checkpoint_extract_id}:{chunk_index}",
                    },
                }
                checkpoint_config = await _checkpoint_before_node(graph, base_config, replay_from_node)
                checkpoint = await graph.aget_state(checkpoint_config)
                replay_checkpoints[chunk_index] = checkpoint_config
                reporter.seed_from_checkpoint(replay_from_node, dict(checkpoint.values or {}))
        reporter.started(replay_from_node)
        concurrency = asyncio.Semaphore(ai.ycs.graphs.ingestion.params.MAX_CONCURRENT_BATCHES)
        context = ai.ycs.graphs.ingestion.entities.IngestionContext(
            es_client = es,
            extractor = extract.service.get_extractor(),
            transcript_service = transcript_service,
            run_id = extract_id,
            include_transcription = include_transcription,
            languages = languages,
            total_videos = len(video_ids),
            reporter = reporter,
            qdrant_semaphore = asyncio.Semaphore(
                rag.source.indexes.neo4j_index.params.EXTRACT_CONCURRENCY,
            ),
            neo4j_semaphore = asyncio.Semaphore(
                ai.ycs.graphs.ingestion.params.MAX_CONCURRENT_NEO4J_BATCHES,
            ),
            cancel_check = cancel_check if redis is not None else None,
        )

        async def run_chunk(chunk_index: int, chunk: list[str]) -> dict[str, Any] | None:
            async with concurrency:
                if await cancel_check():
                    logger.info(f"[ycs:ingestion:graph] {extract_id}: skipped queued batch after cancellation")
                    return None
                thread_id = checkpoint_extract_id or extract_id
                graph_config = None
                graph_input: dict[str, Any] | None = {
                    "video_ids": chunk,
                    "extract_id": extract_id,
                }
                if checkpointer is not None and thread_id:
                    graph_config = {"configurable": {"thread_id": f"ycs-ingestion:{thread_id}:{chunk_index}"}}
                    if replay_from_node is not None:
                        checkpoint_config = replay_checkpoints[chunk_index]
                        replay_state = {"extract_id": extract_id, "replay_node": replay_from_node}
                        if replay_from_node in {"qdrant", "neo4j"}:
                            graph_config = await graph.aupdate_state(
                                checkpoint_config,
                                replay_state,
                                as_node="elasticsearch",
                            )
                        else:
                            graph_config = await graph.aupdate_state(checkpoint_config, replay_state)
                        graph_input = None
                return await graph.ainvoke(
                    graph_input,
                    config=graph_config,
                    context=context,
                    **({"durability": "sync"} if checkpointer is not None else {}),
                )

        outcomes = await asyncio.gather(
            *(run_chunk(index, chunk) for index, chunk in enumerate(chunks)),
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
        qdrant_completed_ids: set[str] = set()
        qdrant_failed_ids: set[str] = set()
        qdrant_cancelled_ids: set[str] = set()
        neo4j_completed_ids: set[str] = set()
        neo4j_failed_ids: set[str] = set()
        neo4j_cancelled_ids: set[str] = set()
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
            qdrant_result = outcome.get("qdrant_result", {})
            qdrant_completed_ids.update(qdrant_result.get("processed_ids", []))
            qdrant_failed_ids.update(qdrant_result.get("failed_ids", []))
            qdrant_cancelled_ids.update(qdrant_result.get("cancelled_ids", []))
            neo4j_result = outcome.get("neo4j_result", {})
            neo4j_completed_ids.update(
                neo4j_result.get("completed_video_ids", [])
                or neo4j_result.get("processed_ids", [])
            )
            neo4j_failed_ids.update(
                neo4j_result.get("failed_video_ids", [])
                or neo4j_result.get("failed_ids", [])
            )
            neo4j_cancelled_ids.update(neo4j_result.get("cancelled_ids", []))

        dispatched_ids = list(dict.fromkeys(dispatched_ids))
        expected_store_ids = {
            domain.parent_video_id(video_id)
            for video_id in dispatched_ids
        }

        def store_progress(
            completed_ids: set[str],
            failed_ids: set[str],
            cancelled_ids: set[str],
        ) -> dict[str, Any]:
            completed_parents = {
                domain.parent_video_id(video_id)
                for video_id in completed_ids
            }
            failed_parents = {
                domain.parent_video_id(video_id)
                for video_id in failed_ids
            }
            cancelled_parents = {
                domain.parent_video_id(video_id)
                for video_id in cancelled_ids
            }
            completed_parents -= failed_parents
            missing = expected_store_ids - completed_parents - failed_parents - cancelled_parents
            failed_parents.update(missing)
            current = len(completed_parents | failed_parents | cancelled_parents)
            if cancelled_parents:
                state = "CANCELLED"
            elif failed_parents:
                state = "FAILURE" if not completed_parents else "PARTIAL"
            elif current >= len(expected_store_ids):
                state = "SUCCESS"
            else:
                state = "FAILURE"
            return {
                "state": state,
                "phase": "streaming",
                "current": current,
                "total": len(expected_store_ids),
                "completed_ids": sorted(completed_parents),
                "failed_ids": sorted(failed_parents),
            }

        node_progress = reporter.node_progress
        node_progress["qdrant"] = store_progress(
            qdrant_completed_ids,
            qdrant_failed_ids,
            qdrant_cancelled_ids,
        )
        node_progress["neo4j"] = store_progress(
            neo4j_completed_ids,
            neo4j_failed_ids,
            neo4j_cancelled_ids,
        )
        if replay_from_node in {"qdrant", "neo4j"} and isinstance(preserved_node_progress, dict):
            untouched_node = "neo4j" if replay_from_node == "qdrant" else "qdrant"
            preserved_progress = preserved_node_progress.get(untouched_node)
            if isinstance(preserved_progress, dict):
                node_progress[untouched_node] = preserved_progress

        if extract_id:
            await extract.service.dispatch_streaming_totals(
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
            "node_progress": node_progress,
            "status": "cancelled" if await cancel_check() else "complete",
        }
    except Exception:
        if extract_id and redis is not None:
            await reporter.fail_unreported_stores(redis, extract_id)
            await extract.service.dispatch_streaming_totals(
                extract_id,
                video_ids,
                reporter.dispatched_ids,
                include_transcription,
            )
        raise
    finally:
        try:
            if transcript_service is not None:
                await transcript.service.close_transcript_service()
        finally:
            try:
                if redis is not None:
                    await redis.close()
            finally:
                await es.close()
