"""Run LangChain graph extraction and validate transcript grounding."""
from __future__ import annotations

import ai
import domains, infra
from . import entities

import asyncio
import logging
import threading
from typing import Any

from langchain_core.documents import Document
from langchain_neo4j import LLMGraphTransformer
from langchain_neo4j.graphs.graph_document import GraphDocument, Node, Relationship


logger = logging.getLogger(__name__)


def build_graph_transformer(
    model: Any,
    *,
    run_id: str | None = None,
) -> entities.GraphTransformerRuntime:
    """Create a per-task runtime for transcript graph transformation."""
    if run_id:
        model = model.with_config(config = {
            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
        })
    return entities.GraphTransformerRuntime(model = model, run_id = run_id)


def _transformer(
    runtime: entities.GraphTransformerRuntime,
    *,
    additional_instructions: str = "",
    unstructured: bool = False,
) -> LLMGraphTransformer:
    from domains.ycs.ingestion.graph_builder import prompts

    instructions = prompts.EXTRACTION_INSTRUCTIONS
    if additional_instructions:
        instructions = f"{instructions}\n\n{additional_instructions}"
    options: dict[str, Any] = {
        "llm": runtime.model,
        "strict_mode": False,
        "additional_instructions": instructions,
    }
    if unstructured:
        # Some configured providers reject LangChain's nested structured
        # graph schema or return an empty graph for it. This path omits
        # property schemas; the caller applies conservative text checks.
        options["ignore_tool_usage"] = True
    else:
        options["node_properties"] = ["evidence"]
        options["relationship_properties"] = ["evidence"]
    return LLMGraphTransformer(**options)


def _validated_graph_document(
    candidate: GraphDocument,
    source: Document,
    *,
    require_evidence: bool,
) -> tuple[GraphDocument, list[str]]:
    """Validate candidates in the domain and adapt accepted facts to LangChain."""
    from domains.ycs.ingestion.graph_builder import domain

    nodes, relations, issues = domain.validate_graph_candidates(
        candidate,
        source.page_content or "",
        require_evidence = require_evidence,
    )
    nodes_by_id = {
        item["id"].casefold(): Node(id = item["id"], type = item["type"])
        for item in nodes
    }
    accepted_relationships = [
        Relationship(
            source = nodes_by_id[relation["source_id"].casefold()],
            target = nodes_by_id[relation["target_id"].casefold()],
            type = relation["type"],
        )
        for relation in relations
    ]
    return GraphDocument(
        nodes = list(nodes_by_id.values()),
        relationships = accepted_relationships,
        source = source,
    ), issues


def _is_structured_schema_error(error: Exception) -> bool:
    message = f"{type(error).__name__}: {error}".casefold()
    return any(marker in message for marker in (
        "dynamicgraph",
        "anyof",
        "json schema",
        "structured output",
        "invalid schema",
        "schema validation",
        "function_calling",
        "function calling",
        "tool call schema",
    ))


async def _process(
    runtime: entities.GraphTransformerRuntime,
    document: Document,
    *,
    additional_instructions: str = "",
) -> tuple[GraphDocument, bool]:
    """Return a candidate and whether provider compatibility fallback ran."""
    try:
        transformer = _transformer(
            runtime,
            additional_instructions = additional_instructions,
        )
        result = await transformer.aprocess_response(document)
        return result, False
    except Exception as error:
        if not _is_structured_schema_error(error):
            raise
        logger.warning(
            "[ycs:neo4j] provider rejected structured graph output; "
            "retrying with the compatibility parser (%s)",
            type(error).__name__,
        )
    transformer = _transformer(
        runtime,
        additional_instructions = additional_instructions,
        unstructured = True,
    )
    return await transformer.aprocess_response(document), True


async def extract_graph_document(
    runtime: entities.GraphTransformerRuntime,
    document: Document,
) -> GraphDocument:
    """Transform one transcript, validate facts, and preserve its source tag."""
    candidate, compatibility_fallback = await _process(runtime, document)
    if not compatibility_fallback and not (candidate.nodes or candidate.relationships):
        # Known provider behavior: some endpoints accept the structured
        # request but silently emit an empty graph. Try the compatibility
        # mode once; the caller still rejects an empty result as a failure.
        candidate = await _transformer(runtime, unstructured = True).aprocess_response(document)
        compatibility_fallback = True
        logger.warning("[ycs:neo4j] structured graph output was empty; used compatibility parser")

    accepted, issues = _validated_graph_document(
        candidate,
        document,
        require_evidence = not compatibility_fallback,
    )
    if issues and not compatibility_fallback:
        feedback = (
            f"{len(issues)} candidate(s) were rejected because they lacked "
            "exact source evidence, referenced missing endpoints, had "
            "conflicting types, or exceeded extraction limits."
        )
        retry_candidate, retry_fallback = await _process(
            runtime,
            document,
            additional_instructions = (
                "Correct the prior unsupported candidates using only exact "
                "transcript evidence. Every node and relationship must have "
                "an evidence property copied verbatim from the transcript. "
                "Validation feedback:\n" + feedback
            ),
        )
        retried, retry_issues = _validated_graph_document(
            retry_candidate,
            document,
            require_evidence = not retry_fallback,
        )
        if (
            len(retried.nodes) + len(retried.relationships)
            > len(accepted.nodes) + len(accepted.relationships)
        ):
            accepted = retried
        if retry_issues:
            logger.info(
                "[ycs:neo4j] transformer retained %d unsupported candidate(s) after evidence retry",
                len(retry_issues),
            )
        if (
            not retry_fallback
            and not retried.nodes
            and not retried.relationships
            and (retry_candidate.nodes or retry_candidate.relationships)
        ):
            # A provider may accept the structured schema yet omit its
            # evidence properties. Keep a conservative recovery path for
            # that case; its weaker grounding checks are isolated here.
            fallback_candidate = await _transformer(
                runtime,
                additional_instructions = (
                    "Return only transcript-supported entities and direct "
                    "relationships. Omit facts whose named endpoints do not "
                    "appear together in one sentence."
                ),
                unstructured = True,
            ).aprocess_response(document)
            fallback_graph, _ = _validated_graph_document(
                fallback_candidate,
                document,
                require_evidence = False,
            )
            if fallback_graph.nodes or fallback_graph.relationships:
                accepted = fallback_graph
                logger.warning(
                    "[ycs:neo4j] evidence validation yielded no facts; "
                    "accepted locally grounded compatibility output",
                )
    return accepted


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
                    await domains.ycs.ingestion.pipeline_task.service.mark_failed_if_unreported(
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
