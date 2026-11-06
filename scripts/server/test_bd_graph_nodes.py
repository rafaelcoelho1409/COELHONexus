"""Offline checks of how Book Distiller graph nodes report their progress.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_graph_nodes.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains  # noqa: F401  (imported first, as the application does)
from ai.bd.graphs import service


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


events: list[dict] = []


async def progress(event: dict) -> None:
    events.append(event)


async def async_work() -> dict:
    return {"status": "audited"}


def sync_work() -> dict:
    return {"status": "planned"}


def failing_work() -> dict:
    raise RuntimeError("boom")


def statuses() -> list[str]:
    return [event["status"] for event in events]


result = asyncio.run(service.run_node(progress, "audit_batch", async_work))
check("an async node's result is returned", result, {"status": "audited"})
check("it is reported running, then done", statuses(), ["running", "done"])
check("each event names the node", {event["node"] for event in events}, {"audit_batch"})

events.clear()
result = asyncio.run(service.run_node(progress, "plan_repairs", sync_work))
check("a synchronous node is run the same way", (result, statuses()), ({"status": "planned"}, ["running", "done"]))

events.clear()
try:
    asyncio.run(service.run_node(progress, "repair_pages", failing_work))
except RuntimeError:
    check("a failing node is reported running, then failed, and still raises", statuses(), ["running", "failed"])
else:
    raise AssertionError("the node's error was swallowed")

check("a node without a progress callback still runs", asyncio.run(service.run_node(None, "publish", async_work)), {"status": "audited"})
print("graph nodes passed")
