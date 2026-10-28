"""Behavior check of the ingestion lock as a LEASE, against a throwaway `redis-server`.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_lock.py

A run that is killed (a redeploy, an OOM kill, the Celery hard limit) used to leave the framework's lock for 3900 s, and since one
ingestion runs at a time that blocked every other framework for 65 minutes. The lock is now a short lease the run's cancel watcher
keeps renewing: it lives as long as the run, and lapses within one lease when the run dies. Short leases are patched in here so the
check takes seconds."""
import asyncio
import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
_proc = subprocess.Popen(
    ["redis-server", "--port", str(PORT), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no", "--dir", tempfile.mkdtemp()],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
atexit.register(_proc.terminate)
os.environ.update(REDIS_HOST="127.0.0.1", REDIS_PORT=str(PORT), REDIS_PASSWORD="", ENVIRONMENT="local")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import infra

R = infra.redis.service
progress = domains.dd.ingestion.runtime.progress
dispatch = domains.dd.ingestion.runtime.dispatch


def ok(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print(f"ok  {label}")


for _ in range(50):
    try:
        with R.client_sync() as probe:
            probe.ping()
        break
    except Exception:
        time.sleep(0.1)

KEY = progress.keys.lock_key("demo")


async def holder(r) -> str | None:
    return await progress.service.read_lock(r, "demo")


async def main() -> None:
    async with R.client() as r:
        # ---- the lease ----
        ok("a queued run takes the lock for the long, queued lease", await progress.service.acquire_lock(r, "demo", "run-1"))
        ttl = await r.ttl(KEY)
        ok("…which is LOCK_QUEUED_S", progress.params.LOCK_LEASE_S < ttl <= progress.params.LOCK_QUEUED_S, str(ttl))
        ok("renewing as the owner shortens it to the run's lease", await progress.service.renew_lock(r, "demo", "run-1") is True and 0 < await r.ttl(KEY) <= progress.params.LOCK_LEASE_S)
        ok("another run cannot renew a lock it does not hold", await progress.service.renew_lock(r, "demo", "run-2") is False and await holder(r) == "run-1")
        await r.delete(KEY)
        ok("a lease that lapsed is taken again by its run when nobody else has it", await progress.service.renew_lock(r, "demo", "run-1") is True and await holder(r) == "run-1")
        await r.delete(KEY)

        # ---- the heartbeat: the watcher keeps the lock alive exactly as long as the run ----
        progress.params.LOCK_LEASE_S = 2
        await r.set(KEY, "run-1", ex = 2)
        main_task = asyncio.create_task(asyncio.sleep(60))
        watcher = asyncio.create_task(dispatch.service._cancel_watcher(r, "run-1", main_task, 0.05, slug = "demo", renew_every_s = 0.3))
        await asyncio.sleep(3.5)  # longer than the 2 s lease: without renewals the lock would be long gone
        ok("a live run's lock outlives its lease", await holder(r) == "run-1", str(await r.ttl(KEY)))
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions = True)
        main_task.cancel()
        await asyncio.sleep(2.6)
        ok("a run that died (no more renewals) frees the framework within one lease", await holder(r) is None)

        # ---- the watcher still does its first job, and without a slug leaves the lock alone ----
        await r.set(KEY, "run-1", ex = 2)
        sleeper = asyncio.create_task(asyncio.sleep(60))
        plain = asyncio.create_task(dispatch.service._cancel_watcher(r, "run-1", sleeper, 0.05))
        await asyncio.sleep(0.5)
        ok("without a slug the watcher renews nothing", 0 < await r.ttl(KEY) <= 2)
        await domains.dd.ingestion.runtime.cancel.service.request_cancel(r, "run-1")
        await asyncio.wait_for(asyncio.gather(plain, return_exceptions = True), timeout = 3)
        await asyncio.sleep(0)
        ok("…and the cancel flag still cancels the run's task", sleeper.cancelled() or sleeper.done())
        await r.delete(KEY)


asyncio.run(main())
print("\nall checks passed")
