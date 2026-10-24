"""Behavior check of `infra.redis.service` against a throwaway `redis-server`.

    cd apps/server && .venv/bin/python ../../scripts/server/test_infra_redis.py

Covers the client lifecycle (`client`, `client_sync`), the lock helpers (`try_lock`, `release_if_owner(_sync)`), the key
scans (`iter_held`, `first_held`, `delete_matching`), the event stream (`emit_event`, `subscribe_events`) and the cancel
flags (`set_flag`, `flag_is_set`, `clear_flag`, `watch_flag`)."""
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

import infra  # noqa: E402

R = infra.redis.service


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


async def run():
    # ---- client lifecycle ----
    async with R.client() as r:
        await r.set("a", "1")
        ok("client: usable inside the block", await r.get("a") == b"1")
    pool = r.connection_pool
    conns = [*pool._available_connections, *pool._in_use_connections]
    ok("client: every connection of the pool is disconnected on exit", conns and all(not c.is_connected for c in conns), str(conns))
    try:
        async with R.client() as r:
            raise ValueError("boom")
    except ValueError:
        ok("client: an error inside the block propagates", True)
    with R.client_sync() as rs:
        rs.set("b", "2")
        ok("client_sync: usable inside the block", rs.get("b") == b"2")
    try:
        with R.client_sync() as rs:
            raise ValueError("boom")
    except ValueError:
        ok("client_sync: an error inside the block propagates", True)

    # ---- locks ----
    async with R.client() as r:
        ok("try_lock: first caller takes it", await R.try_lock(r, "lock:x", "owner-1", 60) == (True, "owner-1"))
        ok("try_lock: second caller is told the holder", await R.try_lock(r, "lock:x", "owner-2", 60) == (False, "owner-1"))
        ttl = await r.ttl("lock:x")
        ok("try_lock: TTL applied", 0 < ttl <= 60, str(ttl))
        ok("release_if_owner: a non-owner cannot release", await R.release_if_owner(r, "lock:x", "owner-2") is False)
        ok("release_if_owner: the owner releases", await R.release_if_owner(r, "lock:x", "owner-1") is True)
        ok("try_lock: free again", await R.try_lock(r, "lock:x", "owner-3", 60) == (True, "owner-3"))
    with R.client_sync() as rs:
        ok("release_if_owner_sync: the owner releases", R.release_if_owner_sync(rs, "lock:x", "owner-3") is True)

    # ---- scans ----
    async with R.client() as r:
        for slug in ("one", "two", "three"):
            await r.set(f"held:{slug}", f"thread-{slug}")
        await r.set("other:one", "x")
        got = {item async for item in R.iter_held(r, "held:")}
        ok("iter_held: every key under the prefix, suffix + value, nothing else", got == {("one", "thread-one"), ("two", "thread-two"), ("three", "thread-three")}, str(got))
        skipped = {item async for item in R.iter_held(r, "held:", skip="two")}
        ok("iter_held: skip leaves the named suffix out", skipped == {("one", "thread-one"), ("three", "thread-three")}, str(skipped))
        first = await R.first_held(r, "held:", skip="one")
        ok("first_held: an entry that is not skipped", first in {("two", "thread-two"), ("three", "thread-three")}, str(first))
        ok("first_held: None when nothing matches", await R.first_held(r, "nothing:") is None)
        ok("first_held: None when only the skipped key exists", await R.first_held(r, "other:", skip="one") is None)

        for i in range(1203):
            await r.set(f"bulk:{i}", "v")
        await r.set("bulk-keep", "v")
        ok("delete_matching: deletes every match across batches, returns the count", await R.delete_matching(r, "bulk:*") == 1203)
        ok("delete_matching: leaves non-matching keys", await r.exists("bulk-keep") == 1 and await r.exists("bulk:7") == 0)
        ok("delete_matching: nothing to delete is 0", await R.delete_matching(r, "bulk:*") == 0)

    # ---- event stream ----
    chan, snap = "ev:chan", "ev:snap"
    for n in range(3):
        await R.emit_event(chan, snap, {"n": n}, snapshot_max_events=2, snapshot_ttl_s=60, error_label="[test]")
    async with R.client() as r:
        ok("emit_event: snapshot capped to the last N events", [e["n"] for e in await R.replay_snapshot(r, snap)] == [1, 2])
        ok("emit_event: snapshot TTL applied", 0 < await r.ttl(snap) <= 60)

    async def collect(count):
        seen = []
        async for event in R.subscribe_events(chan, snap, poll_interval_s=0.05):
            seen.append(event["n"])
            if len(seen) >= count:
                break
        return seen

    task = asyncio.create_task(collect(3))
    await asyncio.sleep(0.4)
    await R.emit_event(chan, snap, {"n": 99}, snapshot_max_events=10, snapshot_ttl_s=60, error_label="[test]")
    ok("subscribe_events: replays the snapshot, then follows live events", await asyncio.wait_for(task, 5) == [1, 2, 99])

    # ---- flags ----
    async with R.client() as r:
        ok("flag: absent", await R.flag_is_set(r, "flag:x") is False)
        await R.set_flag(r, "flag:x", 60, error_label="[test]")
        ok("flag: set", await R.flag_is_set(r, "flag:x") is True)
        await R.clear_flag(r, "flag:x")
        ok("flag: cleared", await R.flag_is_set(r, "flag:x") is False)

    async def long_task():
        await asyncio.sleep(30)

    main = asyncio.create_task(long_task())
    watcher = asyncio.create_task(R.watch_flag(
        "flag:cancel", main, poll_interval_s=0.05, detected_message="[test] cancel seen", error_label="[test]",
    ))
    await asyncio.sleep(0.2)
    async with R.client() as r:
        await R.set_flag(r, "flag:cancel", 60, error_label="[test]")
    await asyncio.wait_for(watcher, 5)
    try:
        await main
        cancelled = False
    except asyncio.CancelledError:
        cancelled = True
    ok("watch_flag: cancels the watched task when the flag appears", cancelled)


asyncio.run(run())
print("ALL OK")
