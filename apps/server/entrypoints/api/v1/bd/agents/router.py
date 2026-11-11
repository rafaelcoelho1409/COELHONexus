"""Book-scoped Ask API with persisted turn snapshots and LangGraph SSE."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Annotated

import domains
import infra

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse

from . import schemas


router = APIRouter()
logger = logging.getLogger(__name__)
_ACTIVE_TASKS: dict[int, asyncio.Task] = {}
SafeThreadId = Annotated[str, Path(pattern = r"^[A-Za-z0-9_-]{1,128}$")]


@router.get("/threads")
async def get_threads(
    book_id: str = Query(min_length = 1, max_length = 128, pattern = r"^[A-Za-z0-9_-]+$"),
) -> dict:
    await domains.bd.ask.conversation.service.ensure_table()
    items = await domains.bd.ask.conversation.service.list_threads(book_id)
    return {"items": items, "total": len(items)}


@router.get("/history/{thread_id}")
async def get_history(thread_id: SafeThreadId) -> dict:
    await domains.bd.ask.conversation.service.ensure_table()
    items = await domains.bd.ask.conversation.service.list_turns(thread_id)
    return {"thread_id": thread_id, "items": items, "total": len(items)}


@router.get("/usage/{thread_id}")
async def get_usage(thread_id: SafeThreadId) -> dict:
    return await domains.settings.llm_counter.service.read_counters("bd", thread_id)


@router.post("/threads/{thread_id}/branch")
async def branch_thread(thread_id: SafeThreadId, payload: schemas.BranchRequest) -> dict:
    await domains.bd.ask.conversation.service.ensure_table()
    book_id = await domains.bd.ask.conversation.service.thread_book_id(thread_id)
    if not book_id:
        raise HTTPException(status_code = 404, detail = "Ask thread not found")
    new_thread_id = uuid.uuid4().hex[:12]
    copied = await domains.bd.ask.conversation.service.branch_thread(
        thread_id, new_thread_id, book_id, payload.up_to_created_at,
    )
    return {"new_thread_id": new_thread_id, "copied": copied}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: SafeThreadId) -> dict:
    await domains.bd.ask.conversation.service.ensure_table()
    return {"deleted": await domains.bd.ask.conversation.service.delete_thread(thread_id)}


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: int) -> dict:
    task = _ACTIVE_TASKS.get(turn_id)
    if task is not None and not task.done():
        task.cancel()
    deleted = await domains.bd.ask.conversation.service.delete_turn(turn_id)
    return {"cancelled": True, "deleted": deleted}


def _citation_event(state: dict) -> list[dict]:
    citations = state.get("citations") or []
    return citations if isinstance(citations, list) else []


def _thinking_update(thinking: dict, node: str, update: dict) -> None:
    graph = thinking.setdefault("graph", {
        "route": "",
        "active_node": None,
        "visited_nodes": [],
        "retrieval_sources": [],
        "status": "running",
    })
    graph["active_node"] = node
    graph["status"] = "running"
    if node not in graph["visited_nodes"]:
        graph["visited_nodes"].append(node)
    if update.get("mode"):
        graph["route"] = "deep" if update["mode"] == "deep" else "standard"
    if update.get("retrieval_sources") is not None:
        graph["retrieval_sources"] = list(update.get("retrieval_sources") or [])
    node_stage = {
        "plan_research": ("retrieve", "Planning book research"),
        "run_subagent": ("retrieve", "Searching book passages and evidence graph"),
        "rewrite_query": ("retrieve", "Refining the book search"),
        "grade_documents": ("grade", "Checking evidence relevance"),
        "generate": ("generate", "Writing from book evidence"),
        "synthesize": ("generate", "Synthesizing chapter findings"),
        "critic": ("verify", "Verifying answer support"),
        "format_citations": ("verify", "Linking answer citations"),
    }
    order = ["retrieve", "grade", "generate", "verify"]
    if node in node_stage:
        stage, action = node_stage[node]
        index = order.index(stage)
        for position, name in enumerate(order):
            value = thinking["stages"].setdefault(name, {"status": "queued", "action": ""})
            if position < index:
                value.update(status = "done", action = "")
            elif position == index:
                value.update(status = "active", action = action)
    if update.get("mode"):
        thinking["mode"] = update["mode"]
    if update.get("sub_questions"):
        thinking["deep"] = {
            "research_plan": update.get("research_plan", ""),
            "sub_questions": [
                {"question": question, "status": "queued", "answer": "", "answer_preview": ""}
                for question in update["sub_questions"]
            ],
            "confidence_score": None,
        }
    if update.get("research_plan") and isinstance(thinking.get("deep"), dict):
        thinking["deep"]["research_plan"] = update["research_plan"]
    if update.get("sub_results") and isinstance(thinking.get("deep"), dict):
        answers = {item.get("sub_question"): item.get("answer", "") for item in update["sub_results"] if isinstance(item, dict)}
        for item in thinking["deep"].get("sub_questions", []):
            if item["question"] in answers:
                item.update(status = "done", answer = answers[item["question"]], answer_preview = answers[item["question"]][:200])
    if update.get("confidence_score") is not None and isinstance(thinking.get("deep"), dict):
        thinking["deep"]["confidence_score"] = update["confidence_score"]
    if update.get("citations"):
        thinking["citations"] = update["citations"]
    thinking["_seq"] = int(thinking.get("_seq", 0)) + 1


def _event(
    node: str,
    update: dict,
    turn_id: int,
    elapsed_ms: int | None = None,
    graph: dict | None = None,
) -> dict:
    event = {"node": node, "turn_id": turn_id}
    if graph is not None:
        event["graph"] = graph
    for key in (
        "mode", "generation", "confidence_score", "citations", "sub_questions",
        "research_plan", "sub_results", "latest_sub_question", "latest_sub_answer",
        "latest_sub_answer_preview", "retrieval_sources", "verification_status",
    ):
        if key in update:
            event[key] = update[key]
    if "documents" in update or "document_count" in update:
        event["documents"] = True
        event["document_count"] = update.get("document_count", 0)
    if elapsed_ms is not None:
        event["duration_ms"] = elapsed_ms
    return event


@router.post("/search/stream")
async def ask_stream(payload: schemas.AskRequest, request: Request) -> StreamingResponse:
    await domains.bd.ask.conversation.service.ensure_table()
    prior_book = await domains.bd.ask.conversation.service.thread_book_id(payload.thread_id)
    if prior_book is not None and prior_book != payload.book_id:
        raise HTTPException(status_code = 409, detail = "An Ask thread is locked to its original book")
    prior_run = await domains.bd.ask.conversation.service.thread_run_id(payload.thread_id)
    run = (
        await domains.bd.distill.service.read_run(payload.book_id, prior_run)
        if prior_run is not None
        else await domains.bd.distill.service.read_current_run(payload.book_id)
    )
    if run is None or run.get("status") != "done":
        raise HTTPException(status_code = 409, detail = "This book has no completed Distill run to ask about")
    index = await domains.bd.rag.service.read_index(payload.book_id, run["run_id"])
    if index is None or index.get("status") != "done":
        raise HTTPException(status_code = 409, detail = "The current book version is not fully indexed for Ask")
    model = getattr(request.app.state, "llm", None)
    if model is None:
        raise HTTPException(status_code = 503, detail = "The configured chat model is unavailable")
    history = await domains.bd.ask.conversation.service.history(payload.thread_id)
    import ai

    planner = ai.bd.agents.ask.planner.agent.planner(model)
    grader = ai.bd.agents.ask.grader.agent.grader(model)
    answerer = ai.bd.agents.ask.answer.agent.answerer(model)
    critic = ai.bd.agents.ask.critic.agent.critic(model)
    graph = ai.bd.graphs.ask.adaptive.graphs.build_adaptive_ask_graph()
    context = ai.bd.graphs.ask.adaptive.entities.AskContext(
        book_id = payload.book_id,
        run_id = run["run_id"],
        planner = planner,
        grader = grader,
        answerer = answerer,
        critic = critic,
    )
    try:
        turn_id = await domains.bd.ask.conversation.service.insert_turn(
            payload.book_id, run["run_id"], payload.thread_id, payload.question,
        )
    except Exception as error:
        logger.exception("[bd:ask] could not create conversation turn")
        raise HTTPException(status_code = 503, detail = "Ask conversation storage is unavailable") from error
    initial_state = {
        "question": payload.question,
        "thread_id": payload.thread_id,
        "conversation_history": history,
        "force_mode": payload.force_mode or "",
        "supplied_sub_questions": payload.sub_questions,
        "mode": "",
        "queries": [],
        "documents": [],
        "sub_results": [],
        "citations": [],
        "retry_count": 0,
    }
    run_id = payload.thread_id
    domains.settings.llm_counter.service.set_context(ns = "bd", run_id = run_id)
    started = time.monotonic()
    thinking = {
        "stages": {
            "retrieve": {"status": "active", "action": "Planning book research"},
            "grade": {"status": "queued", "action": ""},
            "generate": {"status": "queued", "action": ""},
            "verify": {"status": "queued", "action": ""},
        },
        "book_id": payload.book_id,
        "citations": [],
        "deep": None,
        "graph": {
            "route": "",
            "active_node": "plan_research",
            "visited_nodes": [],
            "retrieval_sources": [],
            "status": "running",
        },
        "_seq": 0,
    }
    queue: asyncio.Queue = asyncio.Queue()
    snapshot_lock = asyncio.Lock()
    config = {
        "configurable": {"thread_id": payload.thread_id},
        "recursion_limit": ai.bd.graphs.ask.adaptive.params.RECURSION_LIMIT,
        "callbacks": [callback for callback in (infra.langfuse.service.build_langchain_callback(),) if callback is not None],
    }

    async def _produce() -> None:
        final_state: dict = {}

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(ai.bd.graphs.ask.adaptive.params.HEARTBEAT_INTERVAL_S)
                thinking["_seq"] = int(thinking.get("_seq", 0)) + 1
                thinking["duration_ms"] = int((time.monotonic() - started) * 1000)
                async with snapshot_lock:
                    await domains.bd.ask.conversation.service.update_snapshot(
                        turn_id, thinking.get("mode", ""), thinking,
                    )
                await queue.put(("heartbeat", None))

        heartbeat_task = asyncio.create_task(_heartbeat(), name = f"bd-ask-heartbeat-{turn_id}")
        try:
            async for streamed in graph.astream(
                initial_state, context = context, config = config, stream_mode = ["updates", "custom"],
            ):
                if isinstance(streamed, tuple) and len(streamed) == 2:
                    stream_kind, values = streamed
                    if stream_kind == "custom":
                        if isinstance(values, dict):
                            custom_node = str(values.get("node") or "run_subagent")
                            _thinking_update(thinking, custom_node, values)
                            thinking["duration_ms"] = int((time.monotonic() - started) * 1000)
                            async with snapshot_lock:
                                await domains.bd.ask.conversation.service.update_snapshot(
                                    turn_id, thinking.get("mode", ""), thinking,
                                )
                            await queue.put(("event", {**values, "turn_id": turn_id, "graph": thinking.get("graph")}))
                        continue
                else:
                    values = streamed
                if not isinstance(values, dict):
                    continue
                for node, update in values.items():
                    if not isinstance(update, dict):
                        continue
                    final_state.update(update)
                    _thinking_update(thinking, node, update)
                    thinking["duration_ms"] = int((time.monotonic() - started) * 1000)
                    async with snapshot_lock:
                        await domains.bd.ask.conversation.service.update_snapshot(
                            turn_id, final_state.get("mode", ""), thinking,
                        )
                    await queue.put(("event", _event(node, update, turn_id, graph=thinking.get("graph"))))
            for stage in thinking["stages"].values():
                stage.update(status = "done", action = "")
            thinking["graph"].update(active_node = None, status = "done")
            duration_ms = int((time.monotonic() - started) * 1000)
            thinking["duration_ms"] = duration_ms
            try:
                thinking["usage"] = await domains.settings.llm_counter.service.read_counters("bd", run_id)
            except Exception:
                thinking["usage"] = None
            answer = str(final_state.get("generation", ""))
            await domains.bd.ask.conversation.service.update_turn(
                turn_id, answer, final_state.get("mode", "standard"), thinking,
            )
            await queue.put(("event", {"node": "end", "turn_id": turn_id, "duration_ms": duration_ms,
                                        "usage": thinking.get("usage"), "citations": _citation_event(final_state),
                                        "graph": thinking.get("graph")}))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — report an actionable stream error and finish the persisted snapshot.
            logger.exception("[bd:ask] graph execution failed")
            failure = f"Error generating answer: {type(error).__name__}: {str(error)[:240]}"
            for stage in thinking["stages"].values():
                stage.update(status = "done", action = "")
            thinking["graph"].update(active_node = None, status = "failed")
            thinking["duration_ms"] = int((time.monotonic() - started) * 1000)
            try:
                await domains.bd.ask.conversation.service.update_turn(
                    turn_id, failure, final_state.get("mode", "standard"), thinking,
                )
            except Exception:
                logger.exception("[bd:ask] failed to persist graph error")
            await queue.put(("event", {"node": "error", "turn_id": turn_id, "error": failure, "graph": thinking.get("graph")}))
            await queue.put(("event", {"node": "end", "turn_id": turn_id, "duration_ms": thinking["duration_ms"], "graph": thinking.get("graph")}))
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions = True)
            _ACTIVE_TASKS.pop(turn_id, None)
            await queue.put(("done", None))

    task = asyncio.create_task(_produce(), name = f"bd-ask-{turn_id}")
    _ACTIVE_TASKS[turn_id] = task

    async def _stream():
        yield "data: " + json.dumps({"node": "_meta", "turn_id": turn_id}) + "\n\n"
        while True:
            kind, value = await queue.get()
            if kind == "done":
                break
            if kind == "heartbeat":
                yield ": keepalive\n\n"
                continue
            yield "data: " + json.dumps(value, ensure_ascii = False, default = str) + "\n\n"

    return StreamingResponse(_stream(), media_type = "text/event-stream", headers = {"Cache-Control": "no-cache"})
