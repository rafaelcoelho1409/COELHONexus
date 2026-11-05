"""Connect Docs Distiller page ingestion to its Agentic AI graph."""
from __future__ import annotations
import domains
from . import params

import hashlib
import json
from typing import Any


async def run_ingestion_agentic(store, *, progress = None, checkpointer = None) -> dict:
    """Freeze assembled page inputs, run the graph, then update the in-memory manifest."""
    import ai

    dd_ai = ai.dd
    storage_keys = domains.dd.ingestion.storage.keys
    entries = store.manifest
    minio = store.minio
    page_refs: list[dict[str, Any]] = []

    # The graph only receives immutable object references and compact metadata.
    for entry in entries:
        source = await minio.read_text(entry.key)
        input_key = storage_keys.agentic_input_key(
            store.framework_slug,
            store.run_id,
            entry.idx,
            entry.slug,
        )
        await domains.dd.ingestion.storage.service.write_immutable(
            minio,
            input_key,
            source,
            params.MARKDOWN_TYPE,
        )
        source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        page_refs.append({
            "idx": entry.idx,
            "slug": entry.slug,
            "url": entry.url,
            "tier": entry.tier,
            "title": entry.title,
            "section": entry.section,
            "notes": entry.notes,
            "lastmod": entry.lastmod,
            "anchors": list(entry.anchors),
            "bytes": len(source.encode("utf-8")),
            "source_key": input_key,
            "raw_key": entry.raw_key,
            "source_sha256": source_sha256,
        })

    candidate_digest = hashlib.sha256(json.dumps(
        [(page["idx"], page["source_sha256"]) for page in page_refs],
        separators = (",", ":"),
    ).encode("utf-8")).hexdigest()[:24]
    if progress is not None:
        await progress.start(tier = "agentic_normalization", total = len(page_refs))

    auditor = dd_ai.agents.ingestion.page_auditor.agent.page_auditor()
    repairer = dd_ai.agents.ingestion.page_repair.agent.page_repair()
    auditor_files = dd_ai.skills.service.files_for("page_auditor")
    repair_files = dd_ai.skills.service.files_for("page_repair")

    async def read_page(key: str) -> str:
        return await minio.read_text(key)

    async def write_artifact(key: str, body: str, content_type: str) -> None:
        await domains.dd.ingestion.storage.service.write_immutable(
            minio,
            key,
            body,
            content_type,
        )

    graph = dd_ai.graphs.ingestion.graph.build_ingestion_graph(
        read_page = read_page,
        artifact_exists = minio.exists,
        read_artifact = minio.read_text,
        write_artifact = write_artifact,
        auditor = auditor,
        repairer = repairer,
        auditor_files = auditor_files,
        repair_files = repair_files,
        progress = progress,
        checkpointer = checkpointer,
    )
    graph_config = {"configurable": {
        "thread_id": f"dd-ingestion:{store.framework_slug}:{store.run_id}:{candidate_digest}",
    }}
    graph_input = {
        "framework_slug": store.framework_slug,
        "run_id": store.run_id,
        "pages": page_refs,
    }
    if checkpointer is not None:
        snapshot = await graph.aget_state(graph_config)
        if snapshot.next:
            graph_input = None
    result = await graph.ainvoke(graph_input, config = graph_config)

    outputs = result.get("output_pages", [])
    by_idx = {page["idx"]: page for page in outputs}
    new_entries = []
    for entry in entries:
        page = by_idx.get(entry.idx)
        if page is None:
            raise RuntimeError(f"agentic graph omitted page index {entry.idx}")
        entry.key = page["key"]
        entry.bytes = page["bytes"]
        entry.title = page["title"]
        entry.raw_key = page["raw_key"]
        new_entries.append(entry)

    await store.replace_manifest(new_entries)
    if progress is not None:
        await progress.complete_stage(
            "agentic",
            current = len(page_refs),
            total = len(page_refs),
        )
    return {
        **result.get("summary", {}),
        "pages": len(outputs),
        "normalizer": params.NORMALIZER_VERSION,
    }
