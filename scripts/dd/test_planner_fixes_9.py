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
# 6) select: pages filed under a migrate / upgrade / changelog URL directory go to the migration chapter ------------------
SEL = domains.dd.planner.nodes.chapter_select.domain
def doc(i): return f"ingestion/fw/pages/{i:04d}-page-{i}.md"
sdk = [doc(i) for i in range(4)]                                                   # `docs/sdk/upgrade-path/<version pair>` pages
rest = [doc(i) for i in range(4, 40)]
kd = {k: ("docs", "sdk", "upgrade-path") for k in sdk} | {k: ("docs", "tracing") for k in rest}
chs = [{"title": "SDK Basics", "member_doc_keys": sdk[:2] + rest[:12], "origin": "proposal"},
       {"title": "Tracing", "member_doc_keys": sdk[2:] + rest[12:], "origin": "proposal"}]
titles = {k: f"Python v{i} to v{i + 1}" for i, k in enumerate(sdk)}               # no migration word in the titles
out, st = SEL.pull_migration_pages(chs, titles, kd)
mig = [c for c in out if c.get("origin") == "migration"]
assert st["n_migration_pulled"] == 4 and len(mig) == 1 and set(mig[0]["member_doc_keys"]) == set(sdk), st
assert SEL.pull_migration_pages(chs, titles)[1]["n_migration_pulled"] == 0          # without directories nothing says so
big = {k: ("docs", "upgrade-guide") for k in rest[:20]} | {k: ("docs", "tracing") for k in rest[20:]} | {k: ("docs", "x") for k in sdk}
assert SEL.pull_migration_pages(chs, titles, big)[1]["n_migration_pulled"] == 0     # a directory with > 15% of the corpus stays put
print("6 migration by URL directory ok:", st)
# 7) assign: an all-zero vector is asked again with the "closest chapter" line ------------------------------------------------
P.SWEEP_ENABLED = True
props28 = [{"title": f"C{i}", "description": "d", "key_concepts": []} for i in range(28)]
props8 = props28[:8]
dist = {"summary": "s", "key_terms": ["t"]}
seen7 = []
def make_score(answers):
    it = iter(answers)
    async def _s(prompt, schema=None):
        seen7.append((("fixed" if schema is not None else "list"), prompt))
        best = next(it)
        if schema is not None:
            return schema(**{f"c{i}": (best if i == 4 else 0.0) for i in range(28)})
        return CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=i, confidence=(best if i == 3 else 0.0)) for i in range(8)])
    return _s
async def run7(answers, props):
    seen7.clear()
    S._score_call = make_score(answers)
    return await S.assign_one(asyncio.Semaphore(1), "fw", "k", dist, props)
res = asyncio.run(run7([0.0, 0.8], props28))                                          # zero, then a real answer
assert len(seen7) == 2 and {x["chapter_idx"]: x["confidence"] for x in res[1]}[4] == 0.8, len(seen7)
assert "give the closest chapter at least 0.5" not in seen7[0][1] and "give the closest chapter at least 0.5" in seen7[1][1] and seen7[1][0] == "fixed"
res = asyncio.run(run7([0.0] * (1 + P.ZERO_REASKS), props28))                         # a genuine no-fit page: 1 + ZERO_REASKS asks, stays zero
assert len(seen7) == 1 + P.ZERO_REASKS and max(x["confidence"] for x in res[1]) == 0.0 and not res[3]
res = asyncio.run(run7([0.9], props28))                                               # a confident answer: no extra call
assert len(seen7) == 1
res = asyncio.run(run7([0.2, 0.9], props28))                                          # 0.2 < RESCUE_FLOOR: asked again, the better vector wins
assert len(seen7) == 2 and max(x["confidence"] for x in res[1]) == 0.9
res = asyncio.run(run7([0.0, 0.7], props8))                                           # short plan: the score list gets the line appended / swapped
assert len(seen7) == 2 and seen7[1][0] == "list" and "give the closest chapter at least 0.5" in seen7[1][1], seen7[1][1][-200:]
P.ZERO_REASKS = 0
asyncio.run(run7([0.0], props28)); assert len(seen7) == 1
P.ZERO_REASKS = 4
assert CA.prompts.closest_chapter("a template without the line").endswith(CA.prompts.CLOSEST_CHAPTER)
print("7 all-zero vector re-ask ok")
# 8) select: absolute size cap + directory-seeded split clustering ----------------------------------------------------------
def mk_units(spec):
    units, texts, i = [], [], 0
    for d, n in spec:
        for j in range(n):
            units.append({"id": i, "keys": [f"ingestion/fw/pages/{i:04d}-{d[-1]}-{j}-name{i}.md"], "slug": f"{d[-1]}-{j}-name{i}", "dir": d, "role": None})
            texts.append(f"integration {d[-1]} page {j} tracing langfuse")
            i += 1
    return units, texts
units, texts = mk_units([(("integrations", "frameworks"), 39), (("integrations", "no-code"), 9), (("integrations", "other"), 8), (("integrations", "analytics"), 3), (("guides", "cookbook"), 3)])
groups = SEL.cluster_units(units, texts, 3, SEL.split_floor(len(units), 3))
fw = {u["id"] for u in units if u["dir"] == ("integrations", "frameworks")}
assert groups and any(fw <= set(g) for g in groups), [len(g) for g in groups]      # the 39 framework pages stay together
assert all(len(g) >= SEL.split_floor(len(units), 3) for g in groups)
parts = [{"title": f"Part {i}", "description": "d", "unit_ids": g} for i, g in enumerate(groups)]
assert SEL.validate_split(parts, units, set(), max_parts = len(groups), floor = SEL.split_floor(len(units), 3))[0]
chs = [{"title": f"C{i}", "member_doc_keys": [f"k{i}-{j}" for j in range(n)], "origin": "proposal"} for i, n in enumerate([64, 30, 28, 26, 24, 22, 20, 18, 16, 14])]
assert SEL.plan_splits(chs) and SEL.plan_splits(chs)[0][0] == 0 and SEL.plan_splits(chs)[0][1] >= 2    # 64 pages: over SPLIT_MAX_DOCS, though only 23% of the corpus
chs[0]["member_doc_keys"] = chs[0]["member_doc_keys"][:38]
assert not [p for p in SEL.plan_splits(chs) if p[0] == 0]                           # 38 pages, 14%: untouched
print("8 absolute chapter cap + directory-seeded split ok:", [len(g) for g in groups])
# 9) order: a catalog family (the integrations block) leaves no gap -------------------------------------------------------
OC = domains.dd.planner.nodes.order_chapters.domain
cat = lambda t, n: {"title": t, "member_doc_keys": [f"ingestion/fw/pages/{i:04d}-{t[:4]}-{i}.md" for i in range(n)]}
chs9 = [cat("Foundations", 8), cat("Frameworks", 6), cat("Providers", 6), cat("Dashboards", 12), cat("Gateways", 6), cat("Prompts", 12)]
kd9 = {k: ("integrations", "x") for c in chs9[1:3] + chs9[4:5] for k in c["member_doc_keys"]} | {k: ("docs", "obs") for c in (chs9[0], chs9[3], chs9[5]) for k in c["member_doc_keys"]}
homes9 = OC.chapter_home_dirs(chs9, kd9)
assert homes9[1] == homes9[2] == homes9[4] == ("integrations",), homes9
new9, _ = OC.group_by_home_dir([0, 1, 2, 3, 4, 5], chs9, homes9)
assert new9 == [0, 1, 2, 4, 3, 5], new9                                            # Gateways pulled next to Providers (one chapter between used to be left alone)
print("9 catalog family adjacent ok:", new9)
# 10) order: an ambiguous install/setup chapter the vote put late is not yanked to the front ---------------------------------
chs10 = [{"title": t} for t in ["Server Core", "Tools", "Client", "Auth", "Middleware", "CLI Installation", "Deploy", "Quickstart", "x", "y"]]
assert OC.apply_foundational_prefix_rule([7, 0, 1, 2, 3, 4, 5, 6, 8, 9], chs10) == ([7, 0, 1, 2, 3, 4, 5, 6, 8, 9], [7])
assert OC.apply_foundational_prefix_rule([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], chs10) == ([7, 0, 1, 2, 3, 4, 5, 6, 8, 9], [7])   # "Quickstart" always pins
chs10[7]["title"] = "Server Setup"
assert OC.apply_foundational_prefix_rule([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], chs10) == ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [])  # ... "setup" at 8th of 10 stays
assert OC.apply_foundational_prefix_rule([0, 1, 7, 2, 3, 4, 5, 6, 8, 9], chs10)[1] == [7]                                # ... but at 3rd it pins
print("10 foundational pin follows the vote ok")

# 11) select: a 17-page catch-all (5% of a 344-page corpus) is split ----------------------------------------------------------
chs11 = [{"title": f"C{i}", "member_doc_keys": [f"k{i}-{j}" for j in range(n)], "origin": "proposal"} for i, n in enumerate([30, 26, 24, 22, 20, 18, 18, 18] + [15] * 11)]
chs11.append({"title": "Additional Topics", "member_doc_keys": [f"a{j}" for j in range(17)], "origin": "gap", "role": "appendix"})
sp = SEL.plan_splits(chs11)
assert sp and sp[-1][0] == len(chs11) - 1 and sp[-1][1] == 2, sp
chs11[-1]["member_doc_keys"] = chs11[-1]["member_doc_keys"][:14]
assert not [x for x in SEL.plan_splits(chs11) if x[0] == len(chs11) - 1]                                                   # 14 pages: below the 16-page floor
print("11 mid-size catch-all split ok:", sp[-1])
print("ALL OK")
