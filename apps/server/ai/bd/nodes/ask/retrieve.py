"""Book-scoped bounded parallel retrieval node."""
from __future__ import annotations

import asyncio

from .... import bd
import domains

import langgraph.config
import langgraph.runtime


async def retrieve_evidence(state: dict, runtime: langgraph.runtime.Runtime[bd.graphs.ask.adaptive.entities.AskContext]) -> dict:
    domains.settings.llm_counter.service.set_node(node = "retrieve")
    queries = list(dict.fromkeys(str(item).strip() for item in state.get("queries", []) if str(item).strip()))[:4]
    try:
        writer = langgraph.config.get_stream_writer()
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
    evidence: list[dict] = []
    sub_results = []
    errors = []
    for task in asyncio.as_completed(tasks):
        query, result = await task
        if isinstance(result, BaseException):
            errors.append(f"{type(result).__name__}: {result}")
            sub_result = {"sub_question": query, "answer": "No indexed evidence was returned."}
            sub_results.append(sub_result)
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
        sub_results.append(sub_result)
        evidence.extend(result)
        if writer is not None and state.get("mode") == "deep":
            writer({
                "node": "run_subagent",
                "latest_sub_question": query,
                "latest_sub_answer_preview": sub_result["answer"][:200],
                "sub_results": [sub_result],
            })
    unique: dict[str, dict] = {}
    for item in evidence:
        unique.setdefault(item["id"], item)
    documents = list(unique.values())[:20]
    return {
        "documents": documents,
        "document_count": len(documents),
        "retrieval_sources": sorted({item["source"] for item in documents}),
        "sub_results": sub_results if state.get("mode") == "deep" else [],
        "retrieval_error": "; ".join(errors)[:500],
    }
