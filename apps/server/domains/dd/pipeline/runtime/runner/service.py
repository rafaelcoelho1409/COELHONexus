"""Running a Pipeline graph, the same way for the planner and the synth: start it with its cancel watcher, forward its progress events to the run's channel,
and settle how it ended."""
from __future__ import annotations
import domains
from . import entities

import asyncio
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def drive(graph, graph_input: dict | None, config: dict, context: Any, thread_id: str) -> None:
    """Run the graph and republish its progress events on the run's channel, in the shape the web reads.

    Nodes emit `{step, kind, ...}` through the graph's `custom` stream (also from the fan-out workers of a stage's subgraph, hence
    `subgraphs=True`); `durability="sync"` writes each checkpoint before the next step starts, so a worker lost mid-run resumes
    from its last completed node."""
    async for _namespace, _mode, chunk in graph.astream(
        graph_input, config, context = context, stream_mode = ["custom"], subgraphs = True, durability = "sync",
    ):
        if not isinstance(chunk, dict) or "step" not in chunk or "kind" not in chunk:
            continue
        fields = {k: v for k, v in chunk.items() if k not in {"step", "kind"}}
        await domains.dd.pipeline.runtime.progress.service.emit_progress(thread_id, chunk["step"], chunk["kind"], **fields)


def launch(binding: entities.Binding, graph_input: dict | None, config: dict, thread_id: str) -> tuple[asyncio.Task, asyncio.Task]:
    """Start the graph (`graph_input` None resumes from the last checkpoint) and the watcher that cancels it when the thread's cancel flag is set."""
    main_task = asyncio.create_task(drive(binding.graph, graph_input, config, binding.context(), thread_id))
    watcher_task = asyncio.create_task(domains.dd.pipeline.runtime.cancel.service.watcher(thread_id, main_task))
    return main_task, watcher_task


async def stop_watcher(watcher_task: asyncio.Task) -> None:
    watcher_task.cancel()
    try:
        await watcher_task
    except (asyncio.CancelledError, Exception):
        pass


async def settle(binding: entities.Binding, config: dict, main_task: asyncio.Task, watcher_task: asyncio.Task, thread_id: str, stage: str) -> dict:
    """Await the run, stop its watcher and write the terminal status into the thread's checkpoint. Returns the terminal patch: `status` (done, cancelled or
    failed) and, on a failure, `error`."""
    patch: dict = {}
    try:
        await main_task
        patch["status"] = "done"
        logger.info(f"[{stage}] {thread_id}: done")
    except asyncio.CancelledError:
        patch["status"] = "cancelled"
        logger.info(f"[{stage}] {thread_id}: cancelled by user")
    except Exception as error:
        patch["status"] = "failed"
        patch["error"] = f"{type(error).__name__}: {error}"
        logger.exception(f"[{stage}] {thread_id}: run failed ({type(error).__name__}: {error})")
    finally:
        await stop_watcher(watcher_task)

    try:
        await binding.graph.aupdate_state(config, patch)
    except Exception as error:
        logger.warning(f"[{stage}] {thread_id}: aupdate_state failed for terminal patch {patch!r}: {type(error).__name__}: {error}")
    return patch


async def snapshot_usage(thread_id: str, stage: str) -> None:
    """Freeze the thread's LLM-usage counters into the snapshot the drawer reads (best-effort: logged, never raised)."""
    try:
        await domains.settings.llm_counter.service.snapshot("dd", thread_id)
    except Exception as error:
        logger.warning(f"[{stage}] {thread_id}: llm-counter snapshot failed ({type(error).__name__}: {error})")
