"""Offline checks of how the Pipeline graphs are run: the planner's and a synth chapter's run and resume, the study around the chapters, and the book_harmonize pass.
The graph is a small real LangGraph over an in-memory checkpointer; storage is in memory; the Redis-bound plumbing of `domains` (progress, cancel, live-run registry)
is replaced by recorders. Nothing here imports `infra`.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_pipeline_runs.py
"""
import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import ai
import domains

os.environ.setdefault("DD_STUDY_SEM", "1")

keys = domains.dd.pipeline.storage.keys
runner = ai.dd.graphs.pipeline.runner
planner_run = ai.dd.graphs.pipeline.planner.service
synth_run = ai.dd.graphs.pipeline.synth.service
study = ai.dd.graphs.pipeline.synth.study
harmonize = ai.dd.nodes.pipeline.synth.book_harmonize
SLUG = "fw"


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def truthy(label: str, value) -> None:
    assert value, f"{label}: expected a truthy value, got {value!r}"
    print(f"ok  {label}")


class FakeBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    async def write(self, key, content, content_type = "text/markdown"):
        self.objects[key] = content if isinstance(content, str) else content.decode()
        return len(content)

    async def read_text(self, key, encoding = "utf-8"):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def read_bytes(self, key):
        return (await self.read_text(key)).encode()

    async def exists(self, key):
        return key in self.objects


# ---- the plumbing of `domains` replaced by recorders ----------------------------------------------------------------------------------------

events: list[tuple] = []          # (thread_id, step, kind, fields)
cancelled: set[str] = set()
calls: list[tuple] = []           # (what, *args)


async def emit_progress(thread_id, step, kind, **fields):
    events.append((thread_id, step, kind, fields))


async def reset(thread_id):
    calls.append(("reset", thread_id))
    cancelled.discard(thread_id)


async def requested(thread_id):
    return thread_id in cancelled


async def watcher(thread_id, main_task, poll_interval_s = 0.01):
    while not main_task.done():
        if thread_id in cancelled:
            main_task.cancel()
            return
        await asyncio.sleep(0.01)


def recorder(name):
    async def record(*args, **kwargs):
        calls.append((name, *args))
    return record


def sync_recorder(name):
    def record(*args, **kwargs):
        calls.append((name, kwargs or args))
    return record


@contextlib.contextmanager
def workflow_span(span_name, **kwargs):
    calls.append(("span", span_name))
    yield lambda output: calls.append(("span_output", span_name, output))


def install() -> None:
    pipeline = domains.dd.pipeline
    pipeline.runtime.progress.service.emit_progress = emit_progress
    pipeline.runtime.cancel.service.reset = reset
    pipeline.runtime.cancel.service.requested = requested
    pipeline.runtime.cancel.service.watcher = watcher
    for name in ("clear_active_run", "track_chapter", "untrack_chapter", "clear_active_study"):
        setattr(pipeline.runtime.dispatch.service, name, recorder(name))
    pipeline.observability.service.workflow_span = workflow_span
    for name in ("record_planner_run", "record_chapter_outcome", "record_audit_missing", "record_study_completion"):
        setattr(pipeline.observability.service, name, sync_recorder(name))
    domains.settings.llm_counter.service.snapshot = recorder("snapshot")
    domains.dd.ingestion.backfill.service.ensure_framework_normalized = recorder("normalize")
    domains.dd.catalog.service.index_by_slug = lambda: {SLUG: {"name": "Framework"}}


def where(name) -> list[tuple]:
    return [c for c in calls if c[0] == name]


def kinds(thread_id, step = None) -> list[str]:
    return [kind for tid, st, kind, _ in events if tid == thread_id and (step is None or st == step)]


def fields_of(thread_id, kind) -> dict:
    return next(f for tid, _, k, f in events if tid == thread_id and k == kind)


def clear() -> None:
    events.clear()
    calls.clear()
    cancelled.clear()


# ---- a small real graph: one node that emits progress and then behaves as its state says -----------------------------------------------------

class State(TypedDict, total = False):
    framework_slug: str
    thread_id: str
    planner_mode: str
    chapter_id: str
    synth_mode: str
    status: str
    error: str
    run_started_at: float
    refine_iter: int
    chapter_stats: dict
    mgsr_stats: dict
    select_stats: dict


@dataclass
class Ctx:
    blobs: object = None


behaviour = {"default": "ok"}


async def work(state: State) -> dict:
    get_stream_writer()({"step": "work", "kind": "start", "chapter": state.get("chapter_id")})
    get_stream_writer()("not a progress event")
    how = behaviour.get(state.get("chapter_id") or "", behaviour["default"])
    if how == "boom":
        raise RuntimeError("the model is down")
    if how == "hang":
        await asyncio.sleep(30)
    if how == "audit_failed":
        return {"status": "audit_failed", "error": "1 section is empty"}
    return {"chapter_stats": {"wall_ms": 1500, "audit_passed": True, "n_code_refs": 4, "n_missing": 0}, "mgsr_stats": {"halt_reason": "chapter_passed"}, "refine_iter": 1}


def binding(blobs: FakeBlobs) -> runner.entities.Binding:
    builder = StateGraph(State, context_schema = Ctx)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile(checkpointer = InMemorySaver())
    return runner.entities.Binding(graph = graph, blobs = blobs, context = lambda: Ctx(blobs = blobs), config = lambda tid: {"configurable": {"thread_id": tid}})


async def values_of(b: runner.entities.Binding, thread_id: str) -> dict:
    return dict((await b.graph.aget_state(b.config(thread_id))).values or {})


# ---- the planner ----------------------------------------------------------------------------------------------------------------------------

async def test_planner() -> None:
    thread = "docs-distiller/fw/u1"
    clear()
    blobs = FakeBlobs()
    b = binding(blobs)
    result = await planner_run.run(b, thread, SLUG, "llm")
    check("planner: a run that finishes is done", (result["status"], result["error"]), ("done", None))
    check("planner: the progress events of the graph reach the run's channel", kinds(thread, "work"), ["start"])
    check("planner: a custom chunk that is not a progress event is not forwarded", [e for e in events if e[2] != "start" and e[1] != "planner"], [])
    terminal = fields_of(thread, "terminal")
    check("planner: the terminal event says done", terminal["status"], "done")
    truthy("planner: and carries the wall-clock", isinstance(terminal["total_wall_ms"], int))
    check("planner: the checkpoint holds the terminal status", (await values_of(b, thread))["status"], "done")
    check("planner: the cancel flag is cleared before the run", where("reset"), [("reset", thread)])
    check("planner: the live-run registry is cleared for the slug", where("clear_active_run"), [("clear_active_run", SLUG)])
    check("planner: the usage snapshot is taken", where("snapshot"), [("snapshot", "dd", thread)])
    timing = json.loads(blobs.objects[keys.planner_timing_key(SLUG)])
    check("planner: the timing blob is written", (timing["slug"], timing["total_wall_ms"] >= 0), (SLUG, True))
    check("planner: the run metric records the outcome", where("record_planner_run")[0][1]["outcome"], "done")
    check("planner: the trace output is the run's summary", where("span_output")[0][2]["status"], "done")

    clear()
    behaviour["default"] = "boom"
    failed = await planner_run.run(binding(FakeBlobs()), "docs-distiller/fw/u2", SLUG, "llm")
    check("planner: a crash is a failed run with its error", (failed["status"], failed["error"]), ("failed", "RuntimeError: the model is down"))
    check("planner: the failure reaches the terminal event", fields_of("docs-distiller/fw/u2", "terminal")["error"], "RuntimeError: the model is down")

    clear()
    behaviour["default"] = "hang"
    cancelled.add("docs-distiller/fw/u3")
    b3 = binding(FakeBlobs())

    async def raise_flag_again():
        await asyncio.sleep(0.05)
        cancelled.add("docs-distiller/fw/u3")

    flag = asyncio.create_task(raise_flag_again())
    stopped = await planner_run.run(b3, "docs-distiller/fw/u3", SLUG, "llm")
    await flag
    check("planner: the cancel flag stops the run", stopped["status"], "cancelled")

    # resume
    clear()
    behaviour["default"] = "boom"
    b4 = binding(FakeBlobs())
    thread4 = "docs-distiller/fw/u4"
    await planner_run.run(b4, thread4, SLUG, "llm")
    behaviour["default"] = "ok"
    clear()
    resumed = await planner_run.resume(b4, thread4)
    check("planner: a resume continues at the node that failed", resumed["status"], "done")
    check("planner: and says where it resumed", fields_of(thread4, "resumed")["next_nodes"], ["work"])
    clear()
    again = await planner_run.resume(b4, thread4)
    check("planner: resuming a finished run only re-announces it", (again["status"], kinds(thread4)), ("done", ["terminal"]))
    unknown = await planner_run.resume(b4, "docs-distiller/fw/none")
    truthy("planner: resuming a thread with no checkpoint fails", unknown["status"] == "failed" and "no checkpoints" in unknown["error"])
    behaviour["default"] = "ok"


# ---- one synth chapter ----------------------------------------------------------------------------------------------------------------------

async def test_chapter() -> None:
    thread = "docs-distiller/synth/fw/c1"
    clear()
    blobs = FakeBlobs()
    b = binding(blobs)
    result = await synth_run.run_chapter(b, thread, SLUG, "ch-03-gamma", "quality")
    check("chapter: a run that finishes is done", (result["status"], result["error"]), ("done", None))
    check("chapter: the corpus is checked first", where("normalize"), [("normalize", SLUG)])
    values = await values_of(b, thread)
    truthy("chapter: the initial state carries the wall-clock start the CoRefine gate reads", isinstance(values["run_started_at"], float))
    check("chapter: the synth terminal event is emitted", fields_of(thread, "terminal")["status"], "done")
    check("chapter: the outcome metric reads the chapter's stats", (where("record_chapter_outcome")[0][1]["outcome"], where("record_chapter_outcome")[0][1]["chapter_number"]), ("accept", 3))
    check("chapter: the audit metric is recorded", where("record_audit_missing")[0][1]["missing_ratio"], 0.0)
    timing = json.loads(blobs.objects[keys.study_timing_key(SLUG)])
    check("chapter: its wall time is merged into the study timing", list(timing["per_chapter_ms"]), ["ch-03-gamma"])

    # a second chapter keeps the first one's time and the harmonize time
    blobs.objects[keys.study_timing_key(SLUG)] = json.dumps({**timing, "harmonize_ms": 7000, "session_wall_ms": 99})
    await synth_run.run_chapter(binding(blobs), "docs-distiller/synth/fw/c2", SLUG, "ch-04-delta", "quality")
    merged = json.loads(blobs.objects[keys.study_timing_key(SLUG)])
    check("chapter: the other chapters' times and the harmonize time are kept", (sorted(merged["per_chapter_ms"]), merged["harmonize_ms"], merged["session_wall_ms"]), (["ch-03-gamma", "ch-04-delta"], 7000, 99))

    # a failed chapter is not timed and has no outcome metric
    clear()
    behaviour["ch-05-eps"] = "boom"
    blobs3 = FakeBlobs()
    failed = await synth_run.run_chapter(binding(blobs3), "docs-distiller/synth/fw/c3", SLUG, "ch-05-eps", "quality")
    check("chapter: a crash is a failed run", failed["status"], "failed")
    check("chapter: a failed chapter is not timed", keys.study_timing_key(SLUG) in blobs3.objects, False)
    check("chapter: and records no outcome", where("record_chapter_outcome"), [])

    # resume: the wall-clock start is reset for the new task
    clear()
    behaviour["ch-05-eps"] = "ok"
    b5 = binding(FakeBlobs())
    thread5 = "docs-distiller/synth/fw/c5"
    behaviour["ch-06-zeta"] = "boom"
    await synth_run.run_chapter(b5, thread5, SLUG, "ch-06-zeta", "quality")
    before = (await values_of(b5, thread5))["run_started_at"]
    behaviour["ch-06-zeta"] = "ok"
    await asyncio.sleep(0.01)
    resumed = await synth_run.resume_chapter(b5, thread5)
    check("chapter: a resume finishes the chapter", resumed["status"], "done")
    truthy("chapter: and restarts the wall-clock gate", (await values_of(b5, thread5))["run_started_at"] > before)


# ---- the study ------------------------------------------------------------------------------------------------------------------------------

CHAPTERS = ["ch-01-a", "ch-02-b", "ch-03-c"]
STUDY = "docs-distiller/study/fw/s1"


def seed_study(blobs: FakeBlobs, rendered: tuple[str, ...] = ("ch-01-a",), stale: tuple[str, ...] = ()) -> None:
    plan_chapters = [{"id": cid, "title": cid, "sources": [f"dd/{SLUG}/ingestion/pages/{cid}.md"]} for cid in CHAPTERS]
    blobs.objects[keys.plan_latest_key(SLUG)] = json.dumps({"chapters": plan_chapters})
    for cid in rendered:
        fingerprint = "old" if cid in stale else ai.dd.nodes.pipeline.synth.render.domain.plan_sources_hash(next(c for c in plan_chapters if c["id"] == cid))
        blobs.objects[keys.chapter_render_latest_key(SLUG, cid)] = json.dumps({"audit": {"audit_passed": True}, "plan_sources_hash": fingerprint})
        blobs.objects[keys.chapter_readme_key(SLUG, cid)] = f"# {cid}\n"


async def test_study() -> None:
    seen_harmonize: list[dict] = []

    async def fake_pass(context, *, slug, study_thread_id, chapter_ids):
        seen_harmonize.append({"slug": slug, "thread": study_thread_id, "chapter_ids": chapter_ids, "blobs": context.blobs})
        return {"elapsed_ms": 4000, "n_chapters": 2}

    original_pass = harmonize.node.run_pass
    harmonize.node.run_pass = fake_pass
    try:
        # one chapter already rendered, one that fails its audit, one that renders
        clear()
        behaviour.update({"default": "ok", "ch-02-b": "audit_failed"})
        blobs = FakeBlobs()
        seed_study(blobs)
        b = binding(blobs)
        result = await study.run(b, STUDY, SLUG, CHAPTERS, "quality")
        check("study: a chapter that failed its audit makes it needs_review", (result["final_status"], result["n_completed"], result["n_failed"], result["n_total"]), ("needs_review", 2, 0, 3))
        check("study: the events follow the chapters in order", kinds(STUDY, "study"), [
            "study_start", "chapter_done", "chapter_ready", "chapter_running", "chapter_done", "chapter_running", "chapter_done", "chapter_ready", "study_done",
        ])
        skipped = next(f for tid, _, k, f in events if tid == STUDY and k == "chapter_done")
        check("study: an already rendered chapter is skipped, not run again", (skipped["chapter_id"], skipped["skipped"]), ("ch-01-a", True))
        statuses = [(f["chapter_id"], f["status"]) for tid, _, k, f in events if tid == STUDY and k == "chapter_done"]
        check("study: each chapter reports its status", statuses, [("ch-01-a", "done"), ("ch-02-b", "needs_review"), ("ch-03-c", "done")])
        check("study: each run chapter is registered with the study while it runs", [c[2] for c in where("track_chapter")] == [c[2] for c in where("untrack_chapter")] and len(where("track_chapter")), 2)
        check("study: the harmonize pass runs once, over all the chapters, with the run's storage", [(h["slug"], h["thread"], h["chapter_ids"], h["blobs"] is blobs) for h in seen_harmonize], [(SLUG, STUDY, CHAPTERS, True)])
        done = fields_of(STUDY, "study_done")
        check("study: study_done carries the roll-up", (done["n_completed"], done["n_needs_review"], done["harmonize_ms"], done["book_harmonize"]["elapsed_ms"]), (2, 1, 4000, 4000))
        check("study: the synth terminal mirrors the study", fields_of(STUDY, "terminal")["status"], "needs_review")
        check("study: the live-study registry is cleared", where("clear_active_study"), [("clear_active_study", SLUG)])
        timing = json.loads(blobs.objects[keys.study_timing_key(SLUG)])
        check("study: the timing blob holds the run chapter and the harmonize time", (list(timing["per_chapter_ms"]), timing["harmonize_ms"]), (["ch-03-c"], 4000))
        check("study: the study metric is recorded", where("record_study_completion")[0][1]["outcome"], "needs_review")
        check("study: the chapter threads got their own terminal events", sum(1 for tid, _, k, _ in events if tid.startswith("docs-distiller/synth/") and k == "terminal"), 2)
        check("study: the study span reports its outcome", where("span_output")[-1][2]["harmonize_status"], "done")

        # a render built under an older plan is run again
        clear()
        seen_harmonize.clear()
        behaviour.update({"default": "ok", "ch-02-b": "ok"})
        blobs2 = FakeBlobs()
        seed_study(blobs2, rendered = ("ch-01-a", "ch-02-b"), stale = ("ch-02-b",))
        result2 = await study.run(binding(blobs2), STUDY, SLUG, CHAPTERS, "quality")
        check("study: a stale render is written again and a current one is not", [(f["chapter_id"], f.get("skipped")) for tid, _, k, f in events if tid == STUDY and k == "chapter_done"], [("ch-01-a", True), ("ch-02-b", None), ("ch-03-c", None)])
        check("study: all done", result2["final_status"], "done")

        # a cancel before the first chapter: nothing runs, no harmonize
        clear()
        seen_harmonize.clear()
        cancelled.add(STUDY)
        blobs3 = FakeBlobs()
        seed_study(blobs3, rendered = ())
        result3 = await study.run(binding(blobs3), STUDY, SLUG, CHAPTERS, "quality")
        check("study: a cancelled study stops before its first chapter", (result3["final_status"], result3["n_completed"], seen_harmonize), ("cancelled", 0, []))
        check("study: and says so", fields_of(STUDY, "terminal")["status"], "cancelled")

        # every chapter fails: failed, and no harmonize
        clear()
        behaviour.clear()
        behaviour["default"] = "boom"
        blobs4 = FakeBlobs()
        seed_study(blobs4, rendered = ())
        result4 = await study.run(binding(blobs4), STUDY, SLUG, CHAPTERS, "quality")
        check("study: when every chapter fails the study fails", (result4["final_status"], result4["n_failed"], seen_harmonize), ("failed", 3, []))

        # a harmonize pass that crashes does not fail the study
        async def crashing_pass(context, **_):
            raise RuntimeError("harmonize down")

        harmonize.node.run_pass = crashing_pass
        clear()
        behaviour.clear()
        behaviour["default"] = "ok"
        blobs5 = FakeBlobs()
        seed_study(blobs5, rendered = ())
        result5 = await study.run(binding(blobs5), STUDY, SLUG, CHAPTERS, "quality")
        check("study: a harmonize crash leaves the study done", (result5["final_status"], result5["book_harmonize"]), ("done", {"skipped": "crash: RuntimeError"}))
    finally:
        harmonize.node.run_pass = original_pass
        behaviour.clear()
        behaviour["default"] = "ok"


# ---- the book_harmonize pass ----------------------------------------------------------------------------------------------------------------

def seed_readmes(blobs: FakeBlobs, chapter_ids: list[str], audit_failed: tuple[str, ...] = ()) -> None:
    for cid in chapter_ids:
        blobs.objects[keys.chapter_readme_key(SLUG, cid)] = f"# {cid}\n\nThe timeout is ten seconds.\n"
        blobs.objects[keys.chapter_render_latest_key(SLUG, cid)] = json.dumps({"audit": {"audit_passed": cid not in audit_failed}})


async def test_harmonize_pass() -> None:
    ctx_for = lambda blobs: ai.dd.graphs.pipeline.synth.context.SynthContext(blobs = blobs)  # noqa: E731
    ran: list[list[str]] = []

    async def fake_harmonize(context, *, framework_slug, framework_name, chapters):
        ran.append([c["chapter_id"] for c in chapters])
        return {
            "n_chapters": len(chapters), "n_atomic_claims": 6, "n_canonical_terms": 1, "n_chapters_with_issues": 1, "n_chapters_patched": 1, "elapsed_ms": 900,
            "patches": [
                {"chapter_id": "ch-01-a", "patched": False, "new_prose": None},
                {"chapter_id": "ch-02-b", "patched": True, "new_prose": "```markdown\n# ch-02-b\n\nThe timeout is thirty seconds.\n```"},
            ],
        }

    original = harmonize.node.harmonize_book
    harmonize.node.harmonize_book = fake_harmonize
    try:
        # fewer than two rendered chapters
        clear()
        blobs = FakeBlobs()
        seed_readmes(blobs, ["ch-01-a", "ch-02-b"], audit_failed = ("ch-02-b",))
        out = await harmonize.node.run_pass(ctx_for(blobs), slug = SLUG, study_thread_id = STUDY, chapter_ids = ["ch-01-a", "ch-02-b", "ch-03-c"])
        check("pass: fewer than two usable chapters skips it", (out["skipped"], out["audit_failed_chapters"], out["missing_chapters"]), ("fewer_than_2_rendered_chapters", ["ch-02-b"], ["ch-03-c"]))
        check("pass: and says so on the study's channel", kinds(STUDY, "study"), ["book_harmonize_skipped"])

        # a full pass: runs, overwrites the patched README, stores its result, announces
        clear()
        blobs2 = FakeBlobs()
        seed_readmes(blobs2, ["ch-01-a", "ch-02-b"])
        out2 = await harmonize.node.run_pass(ctx_for(blobs2), slug = SLUG, study_thread_id = STUDY, chapter_ids = ["ch-01-a", "ch-02-b"])
        check("pass: it runs once over both chapters", ran[-1], ["ch-01-a", "ch-02-b"])
        check("pass: the patched README is overwritten, fences unwrapped", blobs2.objects[keys.chapter_readme_key(SLUG, "ch-02-b")], "# ch-02-b\n\nThe timeout is thirty seconds.\n")
        check("pass: the unpatched README is left alone", blobs2.objects[keys.chapter_readme_key(SLUG, "ch-01-a")], "# ch-01-a\n\nThe timeout is ten seconds.\n")
        check("pass: the result is counted", (out2["n_chapters_overwritten"], out2["cache_hit"]), (1, False))
        check("pass: the result is stored under its hash and as the latest", sorted(k for k in blobs2.objects if "harmonize" in k)[0].startswith(f"dd/{SLUG}/"), True)
        check("pass: start and done are announced", kinds(STUDY, "study"), ["book_harmonize_start", "book_harmonize_done"])
        check("pass: the llm usage and the trace are attributed to the node", [c[1] for c in where("span")], ["dd.synth.node.book_harmonize"])

        # the same chapters again: served from the cache
        clear()
        before = len(ran)
        # the first pass changed ch-02-b, so its hash is new: seed the original text back to hit the first run's cache entry
        blobs2.objects[keys.chapter_readme_key(SLUG, "ch-02-b")] = "# ch-02-b\n\nThe timeout is ten seconds.\n"
        out3 = await harmonize.node.run_pass(ctx_for(blobs2), slug = SLUG, study_thread_id = STUDY, chapter_ids = ["ch-01-a", "ch-02-b"])
        check("pass: identical inputs are a cache hit, nothing runs", (out3["cache_hit"], len(ran) - before), (True, 0))
        check("pass: the hit is announced as done", (kinds(STUDY, "study"), fields_of(STUDY, "book_harmonize_done")["cache_hit"]), (["book_harmonize_done"], True))

        # a crash inside the pass is reported, not raised
        async def crash(context, **_):
            raise RuntimeError("agent down")

        harmonize.node.harmonize_book = crash
        blobs4 = FakeBlobs()
        seed_readmes(blobs4, ["ch-01-a", "ch-02-b"])
        out4 = await harmonize.node.run_pass(ctx_for(blobs4), slug = SLUG, study_thread_id = STUDY, chapter_ids = ["ch-01-a", "ch-02-b"])
        check("pass: a crash is reported with its cause", (out4["skipped"], out4["error"]), ("crash: RuntimeError", "agent down"))
    finally:
        harmonize.node.harmonize_book = original


async def main() -> None:
    install()
    await test_planner()
    await test_chapter()
    await test_study()
    await test_harmonize_pass()
    print("\npipeline runs: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
