"""Offline checks for the 2026-10-14 batch-10 fix (no LLM, no cluster): the chapter_assign fallback sweep.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_9.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

CA = domains.dd.planner.nodes.chapter_assign
S, P = CA.service, CA.params
OK = [{"chapter_idx": 2, "confidence": 0.9}]
FB = [{"chapter_idx": 0, "confidence": 0.5}]


def first_pass(n_fb, n=20):
    return [(f"k{i}", FB if i < n_fb else OK, 10, i < n_fb) for i in range(n)]


def make(recover, delay=0.0):
    seen = []
    async def _t(k, sem):
        async with sem:
            seen.append(k)
            await asyncio.sleep(delay)
            return (k, OK, 5, False) if k in recover else (k, FB, 5, True)
    return _t, seen


# 1) recovered docs are swapped in, still-failing docs keep their first result, healthy docs are untouched
t, seen = make({"k0", "k1", "k2"})
res, n, rec = asyncio.run(S.sweep_failed(first_pass(5), t, 20))
assert (n, rec) == (5, 3) and sorted(seen) == ["k0", "k1", "k2", "k3", "k4"], (n, rec, seen)
by = {r[0]: r for r in res}
assert [by[k][3] for k in ("k0", "k1", "k2", "k3", "k4", "k5")] == [False, False, False, True, True, False] and len(res) == 20
assert by["k0"][1] == OK and by["k3"][1] == FB
print("1 sweep swaps recovered docs ok")

# 2) nothing failed, or too many failed (Rotator down) -> no sweep, no settle
calls = []
async def settle(): calls.append(1)
t, seen = make({"k0"})
assert asyncio.run(S.sweep_failed(first_pass(0), t, 20, settle))[1:] == (0, 0)
r7 = first_pass(7)                                                                 # 7 / 20 = 35% > SWEEP_MAX_SHARE
assert asyncio.run(S.sweep_failed(r7, t, 20, settle)) == (r7, 0, 0) and not seen and not calls
print("2 sweep skipped when idle or when the Rotator is down ok")

# 3) settle runs once before the sweep; the sweep honours its concurrency
inflight = []
async def tracked(k, sem):
    async with sem:
        inflight.append(1)
        peak.append(len(inflight))
        await asyncio.sleep(0.02)
        inflight.pop()
        return (k, OK, 5, False)
peak = []
res, n, rec = asyncio.run(S.sweep_failed(first_pass(6), tracked, 20, settle))
assert calls == [1] and (n, rec) == (6, 6) and max(peak) <= P.SWEEP_CONCURRENCY, (calls, n, rec, max(peak))
print("3 settle once, concurrency", max(peak), "ok")

# 4) the budget cuts a stuck sweep; the first results stay
P.SWEEP_BUDGET_S = 0.05
t, seen = make({"k0", "k1"}, delay = 5.0)
r0 = first_pass(2)
res, n, rec = asyncio.run(S.sweep_failed(r0, t, 20))
assert (n, rec) == (2, 0) and res == r0
print("4 budget cuts a stuck sweep ok")

# 5) switch off
P.SWEEP_ENABLED = False
assert asyncio.run(S.sweep_failed(first_pass(3), t, 20))[1:] == (0, 0)
print("5 kill switch ok")
print("ALL OK")
