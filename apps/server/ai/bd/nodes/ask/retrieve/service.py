"""Book-scoped bounded parallel evidence retrieval for BD Ask."""
from __future__ import annotations
import domains
from .... import graphs

import asyncio

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime


async def retrieve_evidence(
    state: dict,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    domains.settings.llm_counter.service.set_node(node = "retrieve")
    queries = list(dict.fromkeys(str(item).strip() for item in state.get("queries", []) if str(item).strip()))[:4]
    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = None

    async def _retrieve_one(query: str) -> tuple[str, dict | BaseException]:
        try:
            if graphs.rag.retrieval.params.SPECIALIST_AGENTS_ENABLED:
                result = await graphs.rag.retrieval.service.retrieve(
                    runtime.context.book_id,
                    runtime.context.run_id,
                    query,
                    model = runtime.context.model,
                )
                return query, {
                    "evidence": result.get("evidence") or [],
                    "source_status": result.get("source_status") or {},
                }
            results = await domains.bd.rag.ai.retrieval.service.retrieve_sources(
                runtime.context.book_id, runtime.context.run_id, query,
            )
            evidence = domains.bd.rag.ai.retrieval.service.fuse_evidence([
                result["evidence"] for result in results.values()
            ])
            return query, {
                "evidence": evidence,
                "source_status": {source: result["status"] for source, result in results.items()},
            }
        except Exception as error:  # noqa: BLE001 — sibling searches must remain useful.
            return query, error

    tasks = [asyncio.create_task(_retrieve_one(query)) for query in queries]
    ranked_results: dict[str, list[dict]] = {}
    source_status_by_query: dict[str, dict[str, str]] = {}
    sub_results_by_query: dict[str, dict] = {}
    errors = []
    for task in asyncio.as_completed(tasks):
        query, result = await task
        if isinstance(result, BaseException):
            errors.append(f"{type(result).__name__}: {result}")
            sub_result = {"sub_question": query, "answer": "No indexed evidence was returned."}
            sub_results_by_query[query] = sub_result
            if writer is not None and state.get("mode") == "deep":
                writer({
                    "node": "run_subagent",
                    "latest_sub_question": query,
                    "latest_sub_answer_preview": sub_result["answer"],
                    "sub_results": [sub_result],
                })
            continue
        evidence = result.get("evidence") or []
        source_status_by_query[query] = result.get("source_status") or {}
        sub_result = {
            "sub_question": query,
            "answer": "\n".join(item["text"][:300] for item in evidence[:2]),
        }
        sub_results_by_query[query] = sub_result
        ranked_results[query] = evidence
        if writer is not None and state.get("mode") == "deep":
            writer({
                "node": "run_subagent",
                "latest_sub_question": query,
                "latest_sub_answer_preview": sub_result["answer"][:200],
                "sub_results": [sub_result],
            })
    documents = domains.bd.rag.ai.retrieval.service.fuse_evidence([
        ranked_results[query] for query in queries if query in ranked_results
    ])
    sub_results = [sub_results_by_query[query] for query in queries if query in sub_results_by_query]
    return {
        "documents": documents,
        "document_count": len(documents),
        "retrieval_sources": sorted({item["source"] for item in documents}),
        "retrieval_source_status": source_status_by_query,
        "sub_results": sub_results if state.get("mode") == "deep" else [],
        "retrieval_error": "; ".join(errors)[:500],
    }
