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

    async def _retrieve_one(query: str) -> tuple[str, list[dict] | BaseException]:
        try:
            return query, await domains.bd.ask.retriever.service.retrieve(
                runtime.context.book_id, runtime.context.run_id, query,
            )
        except Exception as error:  # noqa: BLE001 — sibling searches must remain useful.
            return query, error

    tasks = [asyncio.create_task(_retrieve_one(query)) for query in queries]
    ranked_results: dict[str, list[dict]] = {}
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
        sub_result = {
            "sub_question": query,
            "answer": "\n".join(item["text"][:300] for item in result[:2]),
        }
        sub_results_by_query[query] = sub_result
        ranked_results[query] = result
        if writer is not None and state.get("mode") == "deep":
            writer({
                "node": "run_subagent",
                "latest_sub_question": query,
                "latest_sub_answer_preview": sub_result["answer"][:200],
                "sub_results": [sub_result],
            })
    documents = domains.bd.ask.retriever.service.fuse_evidence([
        ranked_results[query] for query in queries if query in ranked_results
    ])
    sub_results = [sub_results_by_query[query] for query in queries if query in sub_results_by_query]
    return {
        "documents": documents,
        "document_count": len(documents),
        "retrieval_sources": sorted({item["source"] for item in documents}),
        "sub_results": sub_results if state.get("mode") == "deep" else [],
        "retrieval_error": "; ".join(errors)[:500],
    }
