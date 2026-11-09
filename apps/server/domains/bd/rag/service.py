"""Book-scoped hybrid retrieval indexing and knowledge graph materialization."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import domains
import infra
from langchain_text_splitters import RecursiveCharacterTextSplitter

from . import domain, entities, errors, keys, params, schemas, versions


logger = logging.getLogger(__name__)
_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size = params.CHUNK_SIZE,
    chunk_overlap = params.CHUNK_OVERLAP,
    add_start_index = True,
    separators = ["\n\n", "\n", " ", ""],
)
_GRAPH_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size = params.GRAPH_CHAPTER_CONTEXT_MAX_CHARS // 2,
    chunk_overlap = 300,
    add_start_index = False,
    separators = ["\n\n", "\n", " ", ""],
)


def _safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_") for character in value
    )


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(_json_body(value).encode("utf-8")).hexdigest()


def _collection_name(model: str) -> str:
    fingerprint = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"{params.COLLECTION_PREFIX}-{fingerprint}"


def _point_id(
    book_id: str,
    run_id: str,
    source_type: str,
    source_key: str,
    source_record_id: str | None,
    index: int,
    text: str,
) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bd:{book_id}:{run_id}:{source_type}:{source_key}:{source_record_id or ''}:{index}:{digest}",
    ))


async def _write_manifest(storage, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["updated_at"] = max(time.time(), manifest["created_at"])
    validated = schemas.RagIndexManifest.model_validate(manifest)
    await storage.write(
        keys.index_manifest_key(manifest["book_id"], manifest["distill_run_id"]),
        _json_body(validated.model_dump(mode = "json")),
        content_type = "application/json",
    )
    return validated.model_dump(mode = "json")


async def read_index(
    book_id: str,
    run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    storage = storage or infra.minio.service.get_storage()
    key = keys.index_manifest_key(book_id, run_id)
    if not await storage.exists(key):
        return None
    manifest = schemas.RagIndexManifest.model_validate_json(await storage.read_text(key))
    if manifest.book_id != book_id or manifest.distill_run_id != run_id:
        raise ValueError("the retrieval-index manifest does not match the requested run")
    return manifest.model_dump(mode = "json")


async def queue_index(book_id: str, run_id: str) -> dict[str, Any]:
    """Queue book retrieval materialization after report publication."""
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    if not params.RAG_EXECUTION_ENABLED:
        raise ValueError("Agentic RAG execution is disabled")
    storage = infra.minio.service.get_storage()
    run = await domains.bd.distill.service.read_run(book_id, run_id, storage = storage)
    if run is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    if run["status"] != "done" or not run.get("report_key"):
        raise ValueError("retrieval indexing requires a published Learning Guide")

    async with infra.redis.service.client() as redis:
        async with redis.lock(keys.enqueue_lock_key(book_id, run_id), timeout = 120, blocking_timeout = 30):
            run = await domains.bd.distill.service.read_run(book_id, run_id, storage = storage)
            if run is None:
                raise FileNotFoundError(f"Distill run {run_id} was not found")
            if run["status"] != "done" or not run.get("report_key"):
                raise ValueError("retrieval indexing requires a published Learning Guide")
            existing = await read_index(book_id, run_id, storage = storage)
            now = time.time()
            if (
                existing is not None
                and existing["status"] == "done"
                and existing.get("vector_status") == "done"
                and existing.get("graph_status") == "done"
            ):
                return existing
            if existing is not None and existing["status"] in {"queued", "running"}:
                if now - existing["updated_at"] < params.STALE_JOB_AFTER_S:
                    return existing
            try:
                async with redis.lock(
                    keys.index_lock_key(book_id, run_id),
                    timeout = params.INDEX_LOCK_TIMEOUT_S,
                    blocking_timeout = 30,
                ):
                    return await _dispatch_index_unlocked(book_id, run_id, run, existing, storage)
            except infra.redis.service.LockError:
                current = await read_index(book_id, run_id, storage = storage)
                return current or existing


async def _dispatch_index_unlocked(book_id: str, run_id: str, run: dict[str, Any], existing, storage):
    now = time.time()
    vector_done = existing is not None and existing.get("vector_status") == "done"
    manifest = schemas.RagIndexManifest(
        book_id = book_id,
        distill_run_id = run_id,
        extraction_run_id = run["extraction_run_id"],
        status = "queued",
        workflow_version = versions.WORKFLOW_VERSION,
        graph_extraction_prompt_version = versions.GRAPH_EXTRACTION_PROMPT_VERSION,
        attempt = (existing["attempt"] + 1) if existing is not None else 1,
        collection_name = existing.get("collection_name") if existing else None,
        embedding_model = existing.get("embedding_model") if existing else None,
        embedding_dimensions = existing.get("embedding_dimensions") if existing else None,
        source_page_count = existing.get("source_page_count", 0) if existing else 0,
        learning_unit_count = existing.get("learning_unit_count", 0) if existing else 0,
        guide_record_count = existing.get("guide_record_count", 0) if existing else 0,
        point_count = existing.get("point_count", 0) if vector_done else 0,
        graph_node_count = 0,
        graph_relationship_count = 0,
        vector_status = "done" if vector_done else "pending",
        graph_status = "pending",
        current_stage = "graph_materialization" if vector_done else "preparing",
        cancel_requested = False,
        created_at = existing["created_at"] if existing else now,
        updated_at = now,
        finished_at = None,
        error = None,
    ).model_dump(mode = "json")
    manifest = await _write_manifest(storage, manifest)
    try:
        from . import task

        await redis.delete(keys.cancel_key(book_id, run_id))
        task.index_book.apply_async(args = (book_id, run_id))
    except Exception as error:  # noqa: BLE001 — preserve a published guide if Celery is unavailable.
        manifest.update(
            status = "failed",
            current_stage = "graph_materialization" if vector_done else "vector_index",
            vector_status = "done" if vector_done else "failed",
            graph_status = "failed" if vector_done else "pending",
            finished_at = max(time.time(), manifest["created_at"]),
            error = f"Index queue failed ({type(error).__name__}).",
        )
        return await _write_manifest(storage, manifest)
    return manifest


async def _ensure_collection(qdrant, collection_name: str, dimensions: int) -> None:
    if await qdrant.collection_exists(collection_name):
        info = await qdrant.get_collection(collection_name)
        vectors = info.config.params.vectors
        sparse = info.config.params.sparse_vectors
        if (
            not isinstance(vectors, dict)
            or "dense" not in vectors
            or getattr(vectors["dense"], "size", None) != dimensions
            or not isinstance(sparse, dict)
            or "sparse" not in sparse
        ):
            raise RuntimeError("the existing book retrieval collection has an incompatible vector schema")
    else:
        await qdrant.create_collection(
            collection_name = collection_name,
            vectors_config = {
                "dense": infra.qdrant.service.models.VectorParams(
                    size = dimensions,
                    distance = infra.qdrant.service.models.Distance.COSINE,
                    hnsw_config = infra.qdrant.service.models.HnswConfigDiff(m = 16, ef_construct = 200),
                ),
            },
            sparse_vectors_config = {
                "sparse": infra.qdrant.service.models.SparseVectorParams(
                    index = infra.qdrant.service.models.SparseIndexParams(on_disk = False),
                ),
            },
        )
    for field, schema in (("book_id", "keyword"), ("distill_run_id", "keyword"), ("source_type", "keyword"), ("page_number", "integer")):
        try:
            await qdrant.create_payload_index(
                collection_name = collection_name,
                field_name = field,
                field_schema = schema,
            )
        except Exception as error:  # noqa: BLE001 — tolerate an existing payload index only.
            if "exist" not in str(error).casefold():
                raise


def _chunks(text: str) -> Iterator[tuple[int, int, str]]:
    for document in _SPLITTER.create_documents([text]):
        start = document.metadata.get("start_index", 0)
        start = start if type(start) is int and start >= 0 else 0
        yield start, start + len(document.page_content), document.page_content


def _source_record(
    *,
    book_id: str,
    run_id: str,
    extraction_run_id: str,
    ingestion_ai_run_id: str,
    source_type: str,
    source_key: str,
    source_sha256: str,
    text: str,
    section_path: list[str],
    page_number: int | None,
    page_numbers: list[int],
    content_role: str = "unknown",
    content_disposition: str = "include",
    unit_id: str | None = None,
    source_record_id: str | None = None,
) -> dict[str, Any]:
    return {
        "book_id": book_id,
        "distill_run_id": run_id,
        "extraction_run_id": extraction_run_id,
        "ingestion_ai_run_id": ingestion_ai_run_id,
        "source_type": source_type,
        "source_key": source_key,
        "source_sha256": source_sha256,
        "text": text,
        "section_path": section_path,
        "page_number": page_number,
        "page_numbers": page_numbers,
        "content_role": content_role,
        "content_disposition": content_disposition,
        "learning_unit_id": unit_id,
        "source_record_id": source_record_id,
    }


def _guide_records(report: domains.bd.distill.schemas.StudyGuideDocument, report_key: str,
                   report_sha256: str) -> Iterator[dict[str, Any]]:
    record_index = 0

    def add(text: str, section_path: list[str], refs: list[Any]) -> Iterator[dict[str, Any]]:
        nonlocal record_index
        pages = sorted({ref.page_number for ref in refs})
        if text.strip():
            source_record_id = f"guide-record-{record_index:05d}"
            record_index += 1
            yield {
                "source_key": report_key,
                "source_record_id": source_record_id,
                "source_sha256": report_sha256,
                "text": text,
                "section_path": section_path,
                "page_number": pages[0] if pages else None,
                "page_numbers": pages,
            }

    yield from add(
        f"Central thesis: {report.central_thesis}",
        ["Learning Guide", "Central thesis"],
        report.central_thesis_source_refs,
    )
    for label, lessons in (("Key takeaways", report.key_takeaways),
                           ("Cross-chapter connections", report.cross_chapter_connections),
                           ("Limitations", report.limitation_notes)):
        for lesson in lessons:
            text = f"{lesson.title}\n{lesson.explanation}\n" + "\n".join(ref.quote for ref in lesson.source_refs)
            yield from add(text, ["Learning Guide", label], lesson.source_refs)
    for chapter in report.chapters:
        section_path = ["Learning Guide", chapter.title]
        yield from add(chapter.overview, section_path, chapter.overview_source_refs)
        for lesson in [*chapter.lessons, *chapter.open_questions]:
            text = f"{lesson.title}\n{lesson.explanation}\n" + "\n".join(ref.quote for ref in lesson.source_refs)
            yield from add(text, section_path, lesson.source_refs)
        for equation in chapter.equations:
            text = f"{equation.title}\n{equation.explanation}\n{equation.latex}\n" + "\n".join(equation.assumptions)
            yield from add(text, section_path, equation.evidence)
        for diagram in chapter.diagrams:
            evidence = [item for node in diagram.nodes for item in node.evidence]
            text = f"{diagram.title}\n{diagram.accessible_description}\n" + "\n".join(
                f"{edge.source_id} {edge.relation} {edge.target_id}" for edge in diagram.edges
            )
            yield from add(text, section_path, evidence)
        for chart in chapter.charts:
            evidence = [item for row in chart.dataset.rows for item in row.evidence]
            rows = [json.dumps(row.values, ensure_ascii = False, sort_keys = True) for row in chart.dataset.rows]
            text = f"{chart.title}\n{chart.caption}\n{chart.accessible_description}\n" + "\n".join(rows)
            yield from add(text, section_path, evidence)
    for equation in report.equations:
        text = f"{equation.title}\n{equation.explanation}\n{equation.latex}\n" + "\n".join(equation.assumptions)
        yield from add(text, ["Learning Guide", "Figures and models"], equation.evidence)
    for diagram in report.diagrams:
        evidence = [item for node in diagram.nodes for item in node.evidence]
        text = f"{diagram.title}\n{diagram.accessible_description}\n" + "\n".join(
            f"{edge.source_id} {edge.relation} {edge.target_id}" for edge in diagram.edges
        )
        yield from add(text, ["Learning Guide", "Figures and models"], evidence)
    for chart in report.charts:
        evidence = [item for row in chart.dataset.rows for item in row.evidence]
        rows = [json.dumps(row.values, ensure_ascii = False, sort_keys = True) for row in chart.dataset.rows]
        text = f"{chart.title}\n{chart.caption}\n{chart.accessible_description}\n" + "\n".join(rows)
        yield from add(text, ["Learning Guide", "Figures and models"], evidence)
    for entry in report.glossary:
        text = f"{entry.term}\n{entry.definition}"
        yield from add(text, ["Learning Guide", "Glossary"], entry.source_refs)


async def _build_records(storage, run: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    current_source = await domains.bd.ingestion.storage.service.read_manifest(storage, run["book_id"])
    section_by_page: dict[int, list[str]] = {}
    if (
        current_source is not None
        and current_source.get("extraction_id") == run["extraction_run_id"]
        and _canonical_sha256(current_source) == run["source_manifest_sha256"]
    ):
        section_by_page = {
            item["page"]: item.get("section_path", [])
            for item in current_source.get("pages", [])
            if isinstance(item, dict)
            and type(item.get("page")) is int
            and isinstance(item.get("section_path", []), list)
        }

    qa_content = await storage.read_text(run["ingestion_ai_manifest_key"])
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != run["ingestion_ai_manifest_sha256"]:
        raise ValueError("the pinned Ingestion manifest failed its integrity check")
    qa_manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate_json(qa_content)
    if (
        qa_manifest.book_id != run["book_id"]
        or qa_manifest.extraction_run_id != run["extraction_run_id"]
        or qa_manifest.source_manifest_sha256 != run["source_manifest_sha256"]
        or len(qa_manifest.pages) != run["expected_page_count"]
    ):
        raise ValueError("the pinned Ingestion run does not cover every original book page")

    selection_by_page: dict[int, Any] = {}
    if run.get("content_selection_key"):
        selection_content = await storage.read_text(run["content_selection_key"])
        if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != run["content_selection_sha256"]:
            raise ValueError("the pinned content selection failed its integrity check")
        selection = domains.bd.ingestion.ai.schemas.ContentSelectionManifest.model_validate_json(selection_content)
        if (
            selection.book_id != run["book_id"]
            or selection.extraction_run_id != run["extraction_run_id"]
            or selection.agentic_run_id != run["ingestion_ai_run_id"]
            or selection.revision != run["content_selection_revision"]
            or selection.expected_page_count != run["expected_page_count"]
        ):
            raise ValueError("the pinned content selection does not match the Learning Guide run")
        selection_by_page = {page.page_number: page for page in selection.pages}

    report_content = await storage.read_text(run["report_key"])
    report_sha256 = hashlib.sha256(report_content.encode("utf-8")).hexdigest()
    if report_sha256 != run["report_sha256"]:
        raise ValueError("the published Learning Guide failed its integrity check")
    report = domains.bd.distill.schemas.StudyGuideDocument.model_validate_json(report_content)
    if report.distill_run_id != run["run_id"] or report.extraction_run_id != run["extraction_run_id"]:
        raise ValueError("the Learning Guide does not match the selected source run")

    for page in qa_manifest.pages:
        if page.status not in {
            domains.bd.ingestion.ai.schemas.AgenticPageStatus.DONE,
            domains.bd.ingestion.ai.schemas.AgenticPageStatus.REVIEW_REQUIRED,
        }:
            raise ValueError(f"Ingestion page {page.page_number} has no completed normalized content")
        text = await storage.read_text(page.normalized_key)
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != page.normalized_sha256:
            raise ValueError(f"Ingestion page {page.page_number} failed its integrity check")
        section_path = section_by_page.get(page.page_number, [])
        if not all(isinstance(item, str) for item in section_path):
            section_path = []
        selected = selection_by_page.get(page.page_number)
        disposition = selected.disposition if selected is not None else "include"
        yield _source_record(
            book_id = run["book_id"],
            run_id = run["run_id"],
            extraction_run_id = run["extraction_run_id"],
            ingestion_ai_run_id = run["ingestion_ai_run_id"],
            source_type = "source_page",
            source_key = page.normalized_key,
            source_sha256 = page.normalized_sha256,
            text = text,
            section_path = section_path,
            page_number = page.page_number,
            page_numbers = [page.page_number],
            content_role = selected.role if selected is not None else "unknown",
            content_disposition = disposition,
        )

    unit_keys = await storage.list(f"{domains.bd.distill.keys.run_prefix(run['book_id'], run['run_id'])}learning-units/")
    for artifact_key in sorted(unit_keys):
        artifact = json.loads(await storage.read_text(artifact_key))
        page_number = artifact.get("page_number")
        if type(page_number) is not int:
            continue
        for lesson_index, lesson in enumerate(artifact.get("lessons", [])):
            if not isinstance(lesson, dict):
                continue
            evidence = lesson.get("evidence", [])
            text = "\n".join(filter(None, [
                lesson.get("title"), lesson.get("claim"), lesson.get("explanation"),
                *[item.get("quote", "") for item in evidence if isinstance(item, dict)],
            ]))
            if text.strip():
                yield _source_record(
                    book_id = run["book_id"],
                    run_id = run["run_id"],
                    extraction_run_id = run["extraction_run_id"],
                    ingestion_ai_run_id = run["ingestion_ai_run_id"],
                    source_type = "learning_unit",
                    source_key = artifact_key,
                    source_sha256 = artifact.get("source_sha256", "0" * 64),
                    text = text,
                    section_path = artifact.get("section_path", []),
                    page_number = page_number,
                    page_numbers = [page_number],
                    content_role = "core",
                    content_disposition = "include",
                    unit_id = lesson.get("learning_unit_id"),
                    source_record_id = f"lesson-{lesson_index:04d}",
                )

    for record in _guide_records(report, run["report_key"], report_sha256):
        yield _source_record(
            book_id = run["book_id"],
            run_id = run["run_id"],
            extraction_run_id = run["extraction_run_id"],
            ingestion_ai_run_id = run["ingestion_ai_run_id"],
            source_type = "learning_guide",
            source_key = record["source_key"],
            source_sha256 = record["source_sha256"],
            text = record["text"],
            section_path = record["section_path"],
            page_number = record["page_number"],
            page_numbers = record["page_numbers"],
            source_record_id = record["source_record_id"],
        )


async def _graph_source_batches(
    storage: infra.minio.service.MinIOStorage,
    run: dict[str, Any],
) -> AsyncIterator[tuple[str, list[entities.GraphSourceRef]]]:
    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, run["book_id"])
    if (
        source_manifest is None
        or source_manifest.get("extraction_id") != run["extraction_run_id"]
        or _canonical_sha256(source_manifest) != run["source_manifest_sha256"]
    ):
        raise ValueError("the pinned book outline changed before graph extraction")
    section_by_page = {
        item["page"]: item.get("section_path", [])
        for item in source_manifest.get("pages", [])
        if isinstance(item, dict)
        and type(item.get("page")) is int
        and isinstance(item.get("section_path", []), list)
    }
    qa_content = await storage.read_text(run["ingestion_ai_manifest_key"])
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != run["ingestion_ai_manifest_sha256"]:
        raise ValueError("the pinned Ingestion manifest failed its integrity check")
    qa_manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate_json(qa_content)
    if (
        qa_manifest.book_id != run["book_id"]
        or qa_manifest.extraction_run_id != run["extraction_run_id"]
        or qa_manifest.source_manifest_sha256 != run["source_manifest_sha256"]
        or len(qa_manifest.pages) != run["expected_page_count"]
    ):
        raise ValueError("the pinned Ingestion run does not cover every original book page")

    selection_by_page: dict[int, Any] = {}
    if run.get("content_selection_key"):
        selection_content = await storage.read_text(run["content_selection_key"])
        if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != run["content_selection_sha256"]:
            raise ValueError("the pinned content selection failed its integrity check")
        selection = domains.bd.ingestion.ai.schemas.ContentSelectionManifest.model_validate_json(selection_content)
        if (
            selection.book_id != run["book_id"]
            or selection.extraction_run_id != run["extraction_run_id"]
            or selection.agentic_run_id != run["ingestion_ai_run_id"]
            or selection.revision != run["content_selection_revision"]
            or selection.expected_page_count != run["expected_page_count"]
        ):
            raise ValueError("the pinned content selection does not match the retrieval run")
        selection_by_page = {page.page_number: page for page in selection.pages}

    pending: list[entities.GraphSourceRef] = []
    pending_chars = 0
    pending_title = ""
    for page in qa_manifest.pages:
        if page.status not in {
            domains.bd.ingestion.ai.schemas.AgenticPageStatus.DONE,
            domains.bd.ingestion.ai.schemas.AgenticPageStatus.REVIEW_REQUIRED,
        }:
            raise ValueError(f"Ingestion page {page.page_number} has no completed normalized content")
        text = await storage.read_text(page.normalized_key)
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != page.normalized_sha256:
            raise ValueError(f"Ingestion page {page.page_number} failed its integrity check")
        selection = selection_by_page.get(page.page_number)
        role = selection.role if selection is not None else "unknown"
        disposition = selection.disposition if selection is not None else "include"
        section_path = section_by_page.get(page.page_number, [])
        if not all(isinstance(item, str) for item in section_path):
            section_path = []
        title = " › ".join(section_path) or "Unassigned source material"
        for document in _GRAPH_SPLITTER.create_documents([text]):
            source_ref = entities.GraphSourceRef(
                page_number = page.page_number,
                quote = document.page_content,
                content_role = role,
                content_disposition = disposition,
            )
            should_flush = pending and (
                title != pending_title
                or pending_chars + len(source_ref.quote) > params.GRAPH_CHAPTER_CONTEXT_MAX_CHARS
            )
            if should_flush:
                yield pending_title, pending
                pending = []
                pending_chars = 0
            pending_title = title
            pending.append(source_ref)
            pending_chars += len(source_ref.quote)
    if pending:
        yield pending_title, pending


async def _materialize_graph(
    book_id: str,
    run_id: str,
    run: dict[str, Any],
    report: domains.bd.distill.schemas.StudyGuideDocument,
    redis: Any,
    storage: infra.minio.service.MinIOStorage,
) -> tuple[int, int]:
    nodes, edges = domain.learning_guide_graph(report, book_id = book_id, run_id = run_id)
    import ai

    graph_agent = ai.bd.agents.rag.graph_extractor.agent.graph_extractor()
    extraction_graph = ai.bd.graphs.rag.graph_extraction.graphs.graph_extraction_graph()
    batch_index = 0
    async for section_title, source_refs in _graph_source_batches(storage, run):
        if await redis.get(keys.cancel_key(book_id, run_id)):
            raise errors.IndexCancelled
        evidence = list(dict.fromkeys(ref.quote for ref in source_refs))[:params.GRAPH_EVIDENCE_MAX_ITEMS]
        if not evidence:
            continue
        material = {
            "section": section_title,
            "source_pages": [
                {
                    "page_number": ref.page_number,
                    "content_role": ref.content_role,
                    "content_disposition": ref.content_disposition,
                    "text": ref.quote,
                }
                for ref in source_refs
            ],
        }
        source_material = _json_body(material)[:params.GRAPH_CHAPTER_CONTEXT_MAX_CHARS]
        source_fingerprint = _canonical_sha256({
            "prompt_version": versions.GRAPH_EXTRACTION_PROMPT_VERSION,
            "source_material": source_material,
            "source_evidence": evidence,
        })
        artifact_key = keys.graph_extraction_key(book_id, run_id, batch_index)
        artifact = None
        if await storage.exists(artifact_key):
            try:
                artifact = json.loads(await storage.read_text(artifact_key))
            except json.JSONDecodeError:
                artifact = None
        if isinstance(artifact, dict) and artifact.get("source_fingerprint") == source_fingerprint:
            extraction = ai.bd.agents.rag.graph_extractor.schemas.GraphExtraction.model_validate(
                artifact.get("extraction", {}),
            )
        else:
            graph_state = await extraction_graph.ainvoke(
                {
                    "section_title": section_title,
                    "source_material": source_material,
                    "source_evidence": evidence,
                },
                context = ai.bd.graphs.rag.graph_extraction.entities.GraphExtractionContext(
                    agent = graph_agent,
                    run_id = run_id,
                    prompt_version = versions.GRAPH_EXTRACTION_PROMPT_VERSION,
                ),
            )
            extraction = ai.bd.agents.rag.graph_extractor.schemas.GraphExtraction.model_validate(
                graph_state.get("accepted_extraction", {}),
            )
            await storage.write(
                artifact_key,
                _json_body({
                    "source_fingerprint": source_fingerprint,
                    "prompt_version": versions.GRAPH_EXTRACTION_PROMPT_VERSION,
                    "extraction": extraction.model_dump(mode = "json"),
                }),
                content_type = "application/json",
            )
        nodes, edges = domain.add_extracted_graph(
            nodes,
            edges,
            extraction,
            book_id = book_id,
            run_id = run_id,
            section_title = section_title,
            source_refs = source_refs,
        )
        batch_index += 1
    await delete_graph_run(book_id, run_id)
    node_rows = [
        {
            "id": item.id,
            "kind": item.kind,
            "name": item.name,
            "properties": item.properties,
        }
        for item in nodes
    ]
    edge_rows = [
        {
            "id": item.id,
            "source_id": item.source_id,
            "target_id": item.target_id,
            "kind": item.kind,
            "evidence": item.evidence,
        }
        for item in edges
    ]
    async with infra.neo4j.service.session() as session:
        for offset in range(0, len(node_rows), params.GRAPH_WRITE_BATCH_SIZE):
            if await redis.get(keys.cancel_key(book_id, run_id)):
                raise errors.IndexCancelled
            async def write_nodes(transaction):
                result = await transaction.run(
                    """
                    UNWIND $rows AS row
                    MERGE (node:COELHONexus:BD:BDNode {id: row.id})
                    SET node.kind = row.kind,
                        node.name = row.name,
                        node.book_id = $book_id,
                        node.distill_run_id = $run_id,
                        node += row.properties
                    """,
                    rows = node_rows[offset : offset + params.GRAPH_WRITE_BATCH_SIZE],
                    book_id = book_id,
                    run_id = run_id,
                )
                await result.consume()
            await session.execute_write(write_nodes)
        for offset in range(0, len(edge_rows), params.GRAPH_WRITE_BATCH_SIZE):
            if await redis.get(keys.cancel_key(book_id, run_id)):
                raise errors.IndexCancelled
            async def write_edges(transaction):
                result = await transaction.run(
                    """
                    UNWIND $rows AS row
                    MATCH (source:COELHONexus:BD:BDNode {
                        id: row.source_id, book_id: $book_id, distill_run_id: $run_id
                    })
                    MATCH (target:COELHONexus:BD:BDNode {
                        id: row.target_id, book_id: $book_id, distill_run_id: $run_id
                    })
                    MERGE (source)-[relationship:BD_RELATES {id: row.id}]->(target)
                    SET relationship.kind = row.kind,
                        relationship.evidence = row.evidence,
                        relationship.book_id = $book_id,
                        relationship.distill_run_id = $run_id
                    """,
                    rows = edge_rows[offset : offset + params.GRAPH_WRITE_BATCH_SIZE],
                    book_id = book_id,
                    run_id = run_id,
                )
                await result.consume()
            await session.execute_write(write_edges)
    return len(node_rows), len(edge_rows)


async def delete_graph_run(book_id: str, run_id: str) -> int:
    """Delete only nodes and edges owned by one book and Distill run."""
    async with infra.neo4j.service.session() as session:
        async def delete_nodes(transaction):
            result = await transaction.run(
                """
                MATCH (node:COELHONexus:BD:BDNode {
                    book_id: $book_id, distill_run_id: $run_id
                })
                WITH collect(node) AS nodes, count(node) AS deleted
                FOREACH (node IN nodes | DETACH DELETE node)
                RETURN deleted
                """,
                book_id = book_id,
                run_id = run_id,
            )
            record = await result.single()
            return record["deleted"] if record else 0
        return await session.execute_write(delete_nodes)


async def request_cancel(book_id: str, run_id: str) -> dict[str, Any]:
    """Request a cooperative stop at the next bounded indexing boundary."""
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    current = await read_index(book_id, run_id, storage = storage)
    if current is None:
        raise FileNotFoundError(f"retrieval index for run {run_id} was not found")
    if current["status"] not in {"queued", "running"}:
        return current
    async with infra.redis.service.client() as redis:
        await redis.set(keys.cancel_key(book_id, run_id), "1", ex = params.INDEX_LOCK_TIMEOUT_S)
        try:
            async with redis.lock(
                keys.index_lock_key(book_id, run_id),
                timeout = params.INDEX_LOCK_TIMEOUT_S,
                blocking_timeout = 1,
            ):
                current.update(
                    status = "cancelled",
                    cancel_requested = True,
                    finished_at = max(time.time(), current["created_at"]),
                    error = None,
                )
                return await _write_manifest(storage, current)
        except infra.redis.service.LockError:
            current["cancel_requested"] = True
            return current


async def wipe_index(book_id: str, run_id: str) -> dict[str, Any]:
    """Remove one terminal retrieval index while preserving Distill outputs."""
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with infra.redis.service.client() as redis:
        try:
            async with (
                redis.lock(keys.enqueue_lock_key(book_id, run_id), timeout = 120, blocking_timeout = 30),
                redis.lock(
                    keys.index_lock_key(book_id, run_id),
                    timeout = params.INDEX_LOCK_TIMEOUT_S,
                    blocking_timeout = 1,
                ),
            ):
                current = await read_index(book_id, run_id, storage = storage)
                if current is None:
                    return {"book_id": book_id, "distill_run_id": run_id, "wiped": False, "objects_deleted": 0}
                if current["status"] in {"queued", "running"}:
                    raise ValueError("stop the retrieval run and wait for it to finish before wiping it")
                await delete_run_points(book_id, run_id)
                await delete_graph_run(book_id, run_id)
                graph_artifacts = await storage.list(keys.graph_extractions_prefix(book_id, run_id))
                await storage.delete(keys.index_manifest_key(book_id, run_id))
                await storage.delete_prefix(keys.graph_extractions_prefix(book_id, run_id))
                await redis.delete(keys.cancel_key(book_id, run_id))
                return {
                    "book_id": book_id,
                    "distill_run_id": run_id,
                    "wiped": True,
                    "objects_deleted": len(graph_artifacts) + 1,
                }
        except infra.redis.service.LockError as error:
            raise ValueError("the retrieval index is still processing; wait for it to stop before wiping it") from error


async def _run_graph_stage(
    book_id: str,
    run_id: str,
    run: dict[str, Any],
    job: dict[str, Any],
    storage: infra.minio.service.MinIOStorage,
    redis: Any,
) -> dict[str, Any]:
    job.update(
        status = "running",
        current_stage = "graph_materialization",
        graph_status = "running",
        finished_at = None,
        error = None,
    )
    await _write_manifest(storage, job)
    try:
        if await redis.get(keys.cancel_key(book_id, run_id)):
            raise errors.IndexCancelled
        report_content = await storage.read_text(run["report_key"])
        if hashlib.sha256(report_content.encode("utf-8")).hexdigest() != run["report_sha256"]:
            raise ValueError("the published Learning Guide failed its integrity check")
        report = domains.bd.distill.schemas.StudyGuideDocument.model_validate_json(report_content)
        graph_nodes, graph_relationships = await _materialize_graph(
            book_id,
            run_id,
            run,
            report,
            redis,
            storage,
        )
        job.update(
            status = "done",
            current_stage = "complete",
            graph_status = "done",
            graph_node_count = graph_nodes,
            graph_relationship_count = graph_relationships,
            finished_at = max(time.time(), job["created_at"]),
            error = None,
        )
        return await _write_manifest(storage, job)
    except errors.IndexCancelled:
        job.update(
            status = "cancelled",
            graph_status = "cancelled",
            cancel_requested = True,
            finished_at = max(time.time(), job["created_at"]),
            error = None,
        )
        return await _write_manifest(storage, job)
    except Exception as error:
        logger.exception("[bd-rag] graph materialization failed book=%s run=%s", book_id, run_id)
        job.update(
            status = "failed",
            graph_status = "failed",
            finished_at = max(time.time(), job["created_at"]),
            error = f"Knowledge graph materialization failed ({type(error).__name__}).",
        )
        return await _write_manifest(storage, job)


async def run_index(book_id: str, run_id: str) -> dict[str, Any] | None:
    if not _safe_segment(book_id) or not _safe_segment(run_id):
        raise ValueError("book_id and run_id must be safe path segments")
    storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            keys.index_lock_key(book_id, run_id),
            timeout = params.INDEX_LOCK_TIMEOUT_S,
            blocking_timeout = 60,
        ),
    ):
        job = await read_index(book_id, run_id, storage = storage)
        if job is None or job["status"] in {"done", "cancelled"}:
            return job
        run = await domains.bd.distill.service.read_run(book_id, run_id, storage = storage)
        if run is None or run["status"] != "done" or not run.get("report_key"):
            raise ValueError("retrieval indexing requires a published Learning Guide")
        if (
            job.get("vector_status") == "done"
            and job.get("point_count", 0) > 0
            and job.get("collection_name")
        ):
            if await redis.get(keys.cancel_key(book_id, run_id)):
                job.update(
                    status = "cancelled",
                    graph_status = "cancelled",
                    cancel_requested = True,
                    finished_at = max(time.time(), job["created_at"]),
                    error = None,
                )
                return await _write_manifest(storage, job)
            return await _run_graph_stage(book_id, run_id, run, job, storage, redis)
        if await redis.get(keys.cancel_key(book_id, run_id)):
            job.update(
                status = "cancelled",
                cancel_requested = True,
                finished_at = max(time.time(), job["created_at"]),
                error = None,
            )
            return await _write_manifest(storage, job)
        job.update(
            status = "running",
            current_stage = "vector_index",
            vector_status = "running",
            graph_status = "pending",
            cancel_requested = False,
            finished_at = None,
            error = None,
        )
        job = await _write_manifest(storage, job)
        qdrant = infra.qdrant.service.new_qdrant()
        qdrant_closed = False
        try:
            records = _build_records(storage, run)
            counts = {"source_page_count": 0}
            probe, probe_meta = await domains.settings.embeddings.service.embed_probe_async()
            dimensions = len(probe)
            model = str(probe_meta.get("model") or "")
            if dimensions < 1 or not model:
                raise ValueError("the configured embedding endpoint returned no model or vector dimensions")
            collection_name = _collection_name(model)
            await _ensure_collection(qdrant, collection_name, dimensions)
            # A failed attempt may have used a different configured embedding
            # model. Keep this run's points only in the current model-specific
            # collection so a later retry cannot leave stale duplicate vectors.
            await delete_run_points(book_id, run_id, keep_collection = collection_name)
            sparse = infra.qdrant.service.FastEmbedSparse(model_name = params.SPARSE_MODEL_NAME)
            pending: list[dict[str, Any]] = []
            point_count = 0
            learning_unit_count = 0
            guide_record_count = 0

            async def flush() -> None:
                nonlocal point_count
                if not pending:
                    return
                if await redis.get(keys.cancel_key(book_id, run_id)):
                    raise errors.IndexCancelled
                texts = [item["content"] for item in pending]
                dense_vectors, used_model = await domains.settings.embeddings.service.embed_texts_async(texts)
                if used_model != model or any(len(vector) != dimensions for vector in dense_vectors):
                    raise ValueError("the configured embedding model changed during retrieval indexing")
                sparse_vectors = list(sparse.embed_documents(texts))
                points = []
                for index, item in enumerate(pending):
                    points.append(infra.qdrant.service.models.PointStruct(
                        id = item["id"],
                        vector = {
                            "dense": dense_vectors[index],
                            "sparse": infra.qdrant.service.models.SparseVector(
                                indices = sparse_vectors[index].indices,
                                values = sparse_vectors[index].values,
                            ),
                        },
                        payload = {**item["payload"], "embedding_model": model},
                    ))
                await qdrant.upsert(collection_name = collection_name, points = points)
                point_count += len(points)
                pending.clear()
                job.update(
                    collection_name = collection_name,
                    embedding_model = model,
                    embedding_dimensions = dimensions,
                    point_count = point_count,
                    source_page_count = counts["source_page_count"],
                    learning_unit_count = learning_unit_count,
                    guide_record_count = guide_record_count,
                    updated_at = time.time(),
                )
                await _write_manifest(storage, job)

            async for record in records:
                if await redis.get(keys.cancel_key(book_id, run_id)):
                    raise errors.IndexCancelled
                if not isinstance(record.get("text"), str) or not record["text"].strip():
                    continue
                if record["source_type"] == "source_page":
                    counts["source_page_count"] += 1
                if record["source_type"] == "learning_unit":
                    learning_unit_count += 1
                elif record["source_type"] == "learning_guide":
                    guide_record_count += 1
                for chunk_index, (start, end, text) in enumerate(_chunks(record["text"])):
                    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    payload = {
                        key: value for key, value in record.items()
                        if key not in {"text", "book_id", "run_id", "ingestion_ai_run_id", "extraction_run_id"}
                    }
                    payload.update({
                        "book_id": book_id,
                        "distill_run_id": run_id,
                        "extraction_run_id": run["extraction_run_id"],
                        "ingestion_ai_run_id": run["ingestion_ai_run_id"],
                        "chunk_index": chunk_index,
                        "source_char_start": start,
                        "source_char_end": end,
                        "content_sha256": content_hash,
                        "content": text,
                    })
                    pending.append({
                        "id": _point_id(
                            book_id,
                            run_id,
                            record["source_type"],
                            record["source_key"],
                            record.get("source_record_id"),
                            chunk_index,
                            text,
                        ),
                        "content": text,
                        "payload": payload,
                    })
                    if len(pending) >= params.EMBED_BATCH_SIZE:
                        await flush()
            await flush()
            if point_count == 0:
                raise ValueError("no source or Learning Guide text was available to index")
            job.update(
                vector_status = "done",
                collection_name = collection_name,
                embedding_model = model,
                embedding_dimensions = dimensions,
                source_page_count = counts["source_page_count"],
                learning_unit_count = learning_unit_count,
                guide_record_count = guide_record_count,
                point_count = point_count,
                updated_at = time.time(),
            )
            await _write_manifest(storage, job)
            await qdrant.close()
            qdrant_closed = True
            return await _run_graph_stage(book_id, run_id, run, job, storage, redis)
        except errors.IndexCancelled:
            if job.get("current_stage") == "vector_index":
                job["vector_status"] = "cancelled"
            elif job.get("current_stage") == "graph_materialization":
                job["graph_status"] = "cancelled"
            job.update(
                status = "cancelled",
                cancel_requested = True,
                finished_at = max(time.time(), job["created_at"]),
                error = None,
            )
            return await _write_manifest(storage, job)
        except Exception as error:
            logger.exception("[bd-rag] index failed book=%s run=%s", book_id, run_id)
            if job.get("current_stage") == "vector_index":
                job["vector_status"] = "failed"
            elif job.get("current_stage") == "graph_materialization":
                job["graph_status"] = "failed"
            job.update(
                status = "failed",
                finished_at = max(time.time(), job["created_at"]),
                error = f"Retrieval indexing failed ({type(error).__name__}).",
            )
            await _write_manifest(storage, job)
            raise
        finally:
            if not qdrant_closed:
                await qdrant.close()


async def delete_run_points(
    book_id: str,
    run_id: str,
    *,
    keep_collection: str | None = None,
) -> int:
    """Remove this Distill run from model collections, optionally keeping one."""
    qdrant = infra.qdrant.service.new_qdrant()
    removed_from = 0
    try:
        collections = await qdrant.get_collections()
        for collection in collections.collections:
            name = collection.name
            if not name.startswith(("bd-rag-", "bd-distill-")):
                continue
            if name == keep_collection:
                continue
            await qdrant.delete(
                collection_name = name,
                points_selector = infra.qdrant.service.models.FilterSelector(
                    filter = infra.qdrant.service.models.Filter(must = [
                        infra.qdrant.service.models.FieldCondition(
                            key = "book_id", match = infra.qdrant.service.models.MatchValue(value = book_id),
                        ),
                        infra.qdrant.service.models.FieldCondition(
                            key = "distill_run_id", match = infra.qdrant.service.models.MatchValue(value = run_id),
                        ),
                    ]),
                ),
                wait = True,
            )
            removed_from += 1
    finally:
        await qdrant.close()
    return removed_from
