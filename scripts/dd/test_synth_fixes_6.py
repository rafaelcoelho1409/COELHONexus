"""Offline check for the outline block count (2026-10-16): the section floor and the block-aware cap read the chapter's code-block count, which was
always 0 because it looked for a sentinel tag the raw bodies do not carry yet.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_6.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

OL = domains.dd.synth.nodes.outline
count = OL.domain.count_code_blocks


def page(*blocks):
    return "# Page\n\nSome prose.\n\n" + "\n\n".join(f"```python\n{b}\n```" for b in blocks) + "\n"


# 1) raw bodies: distinct fenced blocks count once, however many pages repeat them -----------------------------------------------------------
a, b, c = "print('alpha')\nx = 1", "print('beta')\ny = 2", "print('gamma')\nz = 3"
assert count([page(a, b), page(b, c)]) == 3
assert count([]) == 0 and count(["", "no code here"]) == 0

# 2) sentinelized bodies count their sentinels -----------------------------------------------------------------------------------------------
sent, _ = domains.dd.synth.nodes.vault.domain.sentinelize_doc(page(a, b))
assert count([sent]) == 2, count([sent])
assert count([sent, page(c)]) == 3

# 3) the count drives the floor and the cap: 135 blocks over 18 sources -> floor 7, cap 12 --------------------------------------------------
P = OL.params
cap = P.max_h2_for_n_sources(18, 135)
assert (cap, P.min_h2_for_n_blocks(135, cap)) == (12, 7), (cap, P.min_h2_for_n_blocks(135, cap))
assert P.min_h2_for_n_blocks(count([page(a)]), P.max_h2_for_n_sources(1, 1)) == 0        # a prose-light chapter has no floor

# 4) an unmet section floor is recognised, gets a source-title hint, and earns extra repair attempts ---------------------------------------------
D = OL.domain
schemas = OL.schemas


_HEADINGS = ["Claude Code setup", "Cursor workspace files", "Goose deeplinks", "Gemini command line", "Stdio transport flags", "Environment variable handling"]


def outline_n(n):
    return schemas.ChapterOutline(sections=[
        schemas.OutlineSection(section_id=f"s{i + 1}", heading=_HEADINGS[i], description=f"Topic number {i} of the installation chapter, with its own commands and files.", prerequisites=[])
        for i in range(n)])


o4 = outline_n(4)
_, iss = D.validate_outline_structure(o4, D.derive_dag(o4.sections), n_sources=18, n_blocks=135)
assert [D.is_floor_issue(x) for x in iss] == [True], iss
assert not D.is_floor_issue("Outline has 30 H2 sections but only 5 source documents")
bodies = ["# Claude Code integration\n\ntext", "no title here", "# Goose\n\n```py\nx\n```", "# Claude Code integration\nagain"]
assert D.source_titles(bodies) == ["Claude Code integration", "Goose"]
assert "Claude Code integration; Goose" in D.floor_repair_hint(bodies) and D.floor_repair_hint(["no title"]) == ""
assert OL.params.FLOOR_REPAIR_EXTRA >= 1

# 5) the chat client waits out a Rotator capacity answer (Retry-After, bounded) instead of failing the attempt at once -----------------------------
import asyncio  # noqa: E402

chat = domains.settings.chat.service
CD, CP = domains.settings.chat.domain, domains.settings.chat.params


class FakeResp:
    def __init__(self, headers):
        self.headers = headers


class PoolExhausted(Exception):
    def __init__(self, msg="Error code: 429 - rotator pool exhausted: no healthy deployments available, retry after cooldown", headers=None):
        super().__init__(msg)
        self.response = FakeResp(headers or {})


kw = dict(default_s=20.0, min_s=3.0, cap_s=60.0)
assert CD.capacity_wait_s(PoolExhausted(headers={"retry-after": "45"}), **kw) == 45.0
assert CD.capacity_wait_s(PoolExhausted(headers={"retry-after": "600"}), **kw) == 60.0           # capped
assert CD.capacity_wait_s(PoolExhausted(headers={"retry-after": "1"}), **kw) == 3.0              # floored
assert CD.capacity_wait_s(PoolExhausted(), **kw) == 20.0                                          # no header -> default
assert CD.capacity_wait_s(Exception("No deployments available for selected model, Try again in 600.0 seconds."), **kw) == 60.0
assert CD.capacity_wait_s(Exception("Error code: 504 - TimeoutError"), **kw) is None              # a stall is not a capacity answer
assert CD.capacity_wait_s(Exception("Error code: 400 - bad schema"), **kw) is None

slept, calls = [], []
real_sleep = asyncio.sleep


async def fake_sleep(s):
    slept.append(s)

chat.asyncio.sleep = fake_sleep


async def flaky(n_fail, exc_factory):
    state = {"n": 0}

    async def call():
        state["n"] += 1
        if state["n"] <= n_fail:
            raise exc_factory()
        return "ok"
    return state, await chat._call_with_capacity_wait(call, 5.0)


async def run():
    st, out = await flaky(2, lambda: PoolExhausted(headers={"retry-after": "10"}))
    assert (out, st["n"], slept) == ("ok", 3, [10.0, 10.0]), (out, st, slept)
    slept.clear()
    try:
        await flaky(3, lambda: PoolExhausted(headers={"retry-after": "10"}))
        raise SystemExit("expected the third capacity answer to be raised")
    except PoolExhausted:
        assert slept == [10.0, 10.0]                                                                # attempts are bounded
    slept.clear()
    try:
        await flaky(1, lambda: Exception("Error code: 504 - TimeoutError"))
        raise SystemExit("a non-capacity error must not be retried here")
    except Exception as e:
        assert "504" in str(e) and slept == []
asyncio.run(run())
chat.asyncio.sleep = real_sleep
print("ALL OK")
