"""Offline checks of the planner graph: every stage runs against in-memory storage and scripted agents, and the run's progress events,
state, artifacts, per-page resume and fallbacks are what the web and synth rely on.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_planner_graph.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from langgraph.checkpoint.memory import InMemorySaver

import domains
import ai
from ai.dd.agents.pipeline.planner import schemas
from ai.dd.graphs.pipeline.planner import entities as planner_entities
from ai.dd.graphs.pipeline.planner.graphs import planner_graph
from ai.dd.graphs.pipeline.planner.keys import NODE_TO_FIELD
from ai.dd.graphs.pipeline.planner.params import NODE_ORDER

keys = domains.dd.pipeline.storage.keys
SLUG = "fw"


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def truthy(label: str, value) -> None:
    assert value, f"{label}: expected a truthy value, got {value!r}"
    print(f"ok  {label}")


class FakeBlobs:
    """The slice of MinIO the planner uses, in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, str] = {}
        self.reads = 0

    async def write(self, key, content, content_type = "text/markdown"):
        self.objects[key] = content if isinstance(content, str) else content.decode()
        return len(content)

    async def read_text(self, key, encoding = "utf-8"):
        self.reads += 1
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def read_many(self, keys_, **_):
        return [await self.read_text(k) for k in keys_]

    async def exists(self, key):
        return key in self.objects

    async def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]


TOPICS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]


def corpus(blobs: FakeBlobs, pages_per_topic: int = 5, dirs: tuple[str, ...] = ("guides",)) -> list[str]:
    """pages_per_topic pages per topic; each topic lives in one URL directory (round robin over `dirs`)."""
    entries, page_keys = [], []
    idx = 0
    for t, topic in enumerate(TOPICS):
        for p in range(pages_per_topic):
            key = f"dd/{SLUG}/ingestion/pages/{idx:04d}-{topic}-{p}.md"
            body = f"# {topic.title()} page {p}\n\nAbout {topic} {topic}_api_{p} and {topic}_cli.\n" * 3
            if topic == "foxtrot" and p == 0:
                body = "# Code of conduct\n\nCODE OF CONDUCT for contributors.\n"
            blobs.objects[key] = body
            entries.append({
                "idx": idx, "key": key, "slug": f"{topic}-{p}", "title": f"{topic.title()} page {p}", "bytes": len(body),
                "url": f"https://docs.test/{dirs[t % len(dirs)]}/{topic}-{p}", "tier": "tier1", "anchors": [],
            })
            page_keys.append(key)
            idx += 1
    blobs.objects[domains.dd.ingestion.storage.keys.manifest_key(SLUG)] = json.dumps({"entries": entries, "tier_kind": "tier1"})
    return page_keys


class Fake:
    """An agent that answers each request with `answer(payload)`; counts its calls."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    async def ainvoke(self, request, config = None):
        self.calls += 1
        payload = json.loads(request["messages"][0]["content"])
        return {"structured_response": self.answer(payload)}


def topic_of(text: str) -> str:
    return next((t for t in TOPICS if t in text.lower()), "alpha")


def judge(payload):
    page = payload["page"]
    return schemas.PageRelevance(verdict = "DROP" if "CODE OF CONDUCT" in page else "KEEP")


def distiller(payload):
    topic = topic_of(payload["source_key"])
    return schemas.DocDistillateRaw(
        summary = f"Explains how the {topic} component works and how to configure it with the {topic} command line tool today",
        key_terms = [f"{topic}_api", f"{topic}_cli", f"{topic.title()}Client"],
        role = "reference",
    )


def chapters_for(topics):
    return [
        schemas.ChapterProposal(
            title = f"{t.title()} Component",
            description = f"Everything about the {t} component and its configuration for readers.",
            key_concepts = [f"{t}_api", f"{t}_cli", f"{t.title()}Client", t],
        )
        for t in topics
    ]


def analyst(payload):
    topics = sorted({topic_of(p["key"]) for p in payload["pages"]})
    return schemas.SectionProposals(proposals = chapters_for(topics))


def architect(payload):
    return schemas.ChapterProposalList(proposals = chapters_for(TOPICS))


def scorer(payload):
    topic = topic_of(payload.get("summary") or payload.get("source_key") or "")
    return schemas.DocAssignment(scores = [
        schemas.ChapterScore(chapter_idx = i, confidence = 0.9 if t == topic else 0.0) for i, t in enumerate(TOPICS)
    ])


def sequencer(payload):
    titles = [c["title"] for c in payload["chapters"]]
    return schemas.ChapterTitleOrder(rationale = "Fundamentals first, then the rest of the components in order.", order = sorted(titles))


class Run:
    def __init__(self, blobs: FakeBlobs, **fakes):
        self.blobs = blobs
        self.fakes = {
            "page_judge": Fake(judge), "page_distiller": Fake(distiller), "section_analyst": Fake(analyst),
            "chapter_architect": Fake(architect), "page_scorer": Fake(scorer), "chapter_sequencer": Fake(sequencer),
            **fakes,
        }
        self.events: list[dict] = []
        self.saver = InMemorySaver()

    async def go(self, thread_id: str = "docs-distiller/fw/t1", resume: bool = False) -> dict:
        graph = planner_graph(self.saver)
        context = planner_entities.PlannerContext(
            blobs = self.blobs,
            agents = planner_entities.PlannerAgents(**self.fakes),
        )
        config = {"configurable": {"thread_id": thread_id}}
        initial = None if resume else {"framework_slug": SLUG, "thread_id": thread_id, "planner_mode": "llm", "status": "running"}
        async for _ns, _mode, chunk in graph.astream(initial, config, context = context, stream_mode = ["custom"], subgraphs = True):
            self.events.append(chunk)
        return dict((await graph.aget_state(config)).values)

    def kinds(self, step: str) -> list[str]:
        return [e["kind"] for e in self.events if e["step"] == step]


async def settled(cap_s):
    return {}


domains.settings.chat.service.wait_for_rotator_settle = settled
domains.settings.chat.service.refresh_endpoint = lambda force = False: False


async def main() -> None:
    check("the node order is the contract the web checks", NODE_ORDER, (
        "corpus_load", "off_topic", "doc_distill", "chapter_propose", "chapter_assign", "chapter_select", "order_chapters", "plan_write",
    ))
    check("every node has an output field", sorted(NODE_TO_FIELD), sorted(NODE_ORDER))

    # --- a small corpus: the architect reads it directly, no section analysts
    blobs = FakeBlobs()
    pages = corpus(blobs)
    run = Run(blobs)
    state = await run.go()
    for node in NODE_ORDER:
        truthy(f"{node}: its output field is set", state.get(NODE_TO_FIELD[node]))
    check("corpus_load: every page loaded", len(state["raw_files"]), len(pages))
    check("off_topic: the code of conduct is dropped", len(state["relevant_files"]), len(pages) - 1)
    truthy("off_topic: the judge was asked about the pages", run.fakes["page_judge"].calls >= len(pages) - 1)
    check("doc_distill: every kept page distilled", state["doc_distill_stats"]["n_distilled"], len(pages) - 1)
    check("chapter_propose: no section analysts for a small corpus", run.fakes["section_analyst"].calls, 0)
    check("chapter_propose: the architect proposed the chapters", state["propose_stats"]["n_proposals"], len(TOPICS))
    check("chapter_propose: not a fallback", state["propose_stats"]["fallback_used"], False)
    check("chapter_assign: every kept page assigned", state["assign_stats"]["n_assigned"], len(pages) - 1)
    check("chapter_select: one chapter per topic", state["select_stats"]["n_chapters_out"], len(TOPICS))
    check("order_chapters: all samples valid", state["order_chapters_stats"]["n_samples"], 5)
    plan = json.loads(blobs.objects[keys.plan_latest_key(SLUG)])
    check("plan_write: the plan has every chapter", len(plan["chapters"]), len(TOPICS))
    check("plan_write: every kept page is a source", sum(len(c["sources"]) for c in plan["chapters"]), len(pages) - 1)
    check("plan_write: not degraded", plan["stats"]["pipeline_health"]["degraded"], False)
    for node in NODE_ORDER:
        truthy(f"{node}: progress events reach the stream", run.kinds(node))
    check("off_topic: the web's events", [k for k in run.kinds("off_topic") if k in {"start", "done"}], ["start", "done"])
    truthy("per-page results are stored", any(k.startswith(keys.item_prefix(SLUG, "off_topic")) for k in blobs.objects))

    # --- the same corpus again: every node answers from its blob or its stored per-page results, no model call
    before = {n: f.calls for n, f in run.fakes.items()}
    again = Run(blobs)
    await again.go(thread_id = "docs-distiller/fw/t2")
    check("a re-plan of an unchanged corpus calls no model but the judge and the sequencer's cached order", {
        n: f.calls for n, f in again.fakes.items() if n not in {"page_judge"}
    }, {n: 0 for n in again.fakes if n not in {"page_judge"}})
    check("the re-plan judges nothing (stored verdicts)", again.fakes["page_judge"].calls, 0)

    # --- a large corpus in two URL directories: one section analyst per directory
    big = FakeBlobs()
    corpus(big, pages_per_topic = 25, dirs = ("guides", "api"))
    big_run = Run(big)
    big_state = await big_run.go()
    truthy("a large corpus is read by section analysts", big_run.fakes["section_analyst"].calls >= 2)
    check("the architect still proposes the chapters", big_state["propose_stats"]["n_proposals"], len(TOPICS))
    check("the sketches are reported", big_state["propose_stats"]["n_sections"] >= 2, True)

    # --- a stage that stops mid-run resumes with the pages it has not done
    flaky_blobs = FakeBlobs()
    corpus(flaky_blobs)

    def flaky_distiller(payload):
        if "charlie-2" in payload["source_key"]:
            raise RuntimeError("provider down for this page")
        return distiller(payload)

    flaky = Run(flaky_blobs, page_distiller = Fake(flaky_distiller))
    await flaky.go(thread_id = "docs-distiller/fw/t3")
    # A page the model cannot distill keeps a deterministic distillate (it is kept, not dropped); the run completes and counts it.
    final = dict((await planner_graph(flaky.saver).aget_state({"configurable": {"thread_id": "docs-distiller/fw/t3"}})).values)
    check("a page whose distill fails keeps a fallback distillate", final["doc_distill_stats"]["n_fallback"], 1)
    check("... and the run still produced a plan", bool(final.get("plan_path")), True)

    # --- no architect answer: the deterministic fallback ships, never zero chapters
    dead_blobs = FakeBlobs()
    corpus(dead_blobs)

    def broken(payload):
        raise RuntimeError("provider down")

    dead = Run(dead_blobs, chapter_architect = Fake(broken))
    dead_state = await dead.go(thread_id = "docs-distiller/fw/t4")
    check("a dead architect falls back to seed chapters", dead_state["propose_stats"]["fallback_used"], True)
    truthy("... which still reach a plan", dead_state.get("plan_path"))
    check("... flagged degraded in the plan's health", json.loads(dead_blobs.objects[keys.plan_latest_key(SLUG)])["stats"]["pipeline_health"]["degraded"], True)
    # --- chapter_assign: the ladder of one page (all-zero re-ask, partial vector completed) and the sweep over a page that fell back
    ladder_blobs = FakeBlobs()
    corpus(ladder_blobs)
    seen: dict[str, int] = {}

    def ladder_scorer(payload):
        key = payload["source_key"]
        seen[key] = seen.get(key, 0) + 1
        n = seen[key]
        if "bravo-2" in key and n == 1:
            raise RuntimeError("provider down")                     # round 1: the page falls back; the sweep (call 2) recovers it
        topic = topic_of(payload.get("summary") or key)
        if "alpha-1" in key and "note" not in payload:
            return schemas.DocAssignment(scores = [schemas.ChapterScore(chapter_idx = i, confidence = 0.0) for i in range(len(TOPICS))])
        if "charlie-1" in key and "note" not in payload:
            return schemas.DocAssignment(scores = [schemas.ChapterScore(chapter_idx = 2, confidence = 0.9)])   # a partial vector
        return scorer(payload) if "note" not in payload else schemas.DocAssignment(scores = [
            schemas.ChapterScore(chapter_idx = i, confidence = 0.9 if t == topic else 0.0) for i, t in enumerate(TOPICS)
        ])

    ladder = Run(ladder_blobs, page_scorer = Fake(ladder_scorer))
    ladder_state = await ladder.go(thread_id = "docs-distiller/fw/t5")
    stats = ladder_state["assign_stats"]
    check("assign: the all-zero page was asked again with the closest-chapter line", sum(v for k, v in seen.items() if "alpha-1" in k) >= 2, True)
    check("assign: the fallback page was swept and recovered", (stats["n_swept"], stats["n_sweep_recovered"], stats["n_fallback"]), (1, 1, 0))
    assignments = json.loads(ladder_blobs.objects[keys.assignments_latest_key(SLUG)])["assignments"]
    alpha_1 = next(v for k, v in assignments.items() if "alpha-1" in k)
    truthy("assign: the re-ask found alpha-1's chapter", max(s["confidence"] for s in alpha_1) >= 0.5)

    # --- chapter_propose: an outline below the bar sends the architect back with what it left uncovered
    gate_blobs = FakeBlobs()
    corpus(gate_blobs)
    asked: list[dict] = []

    def gate_architect(payload):
        asked.append(payload)
        if len(asked) == 1:                                           # first attempt: four chapters about nothing in the corpus
            return schemas.ChapterProposalList(proposals = [
                schemas.ChapterProposal(title = f"Unrelated Topic {n}", description = "Something else entirely, not in the corpus.", key_concepts = ["zzz", f"qqq{n}"])
                for n in "ABCD"
            ])
        return architect(payload)

    gate = Run(gate_blobs, chapter_architect = Fake(gate_architect))
    gate_state = await gate.go(thread_id = "docs-distiller/fw/t6")
    check("propose: the architect was asked twice", len(asked), 2)
    truthy("propose: the retry carries the pages left uncovered", "match no chapter" in asked[1]["feedback"] and asked[0]["feedback"] == "")
    check("propose: the better outline won", gate_state["propose_stats"]["n_proposals"], len(TOPICS))
    check("propose: both attempts are reported", gate_state["propose_stats"]["architect_rounds"], 2)

    # --- chapter_select: a curator splits the oversize chapter (all pages scored into one topic)
    split_blobs = FakeBlobs()
    corpus(split_blobs)
    curated: list[dict] = []

    def curator(payload):
        curated.append(payload)
        units = payload["units"]
        k = payload["n_parts"]
        size = -(-len(units) // k)
        return schemas.SplitPlan(groups = [
            schemas.SplitGroup(
                title = f"{payload['chapter']['title']} Part {i + 1}", description = "One part of the oversize chapter for readers.",
                unit_ids = [u["id"] for u in units[i * size:(i + 1) * size]],
            )
            for i in range(k) if units[i * size:(i + 1) * size]
        ])

    def lopsided(payload):
        """alpha, bravo and charlie pages are scored into chapter 0 (15 of 29 pages); the others keep their own chapters."""
        topic = topic_of(payload.get("summary") or payload["source_key"])
        home = 0 if topic in {"alpha", "bravo", "charlie"} else TOPICS.index(topic)
        return schemas.DocAssignment(scores = [schemas.ChapterScore(chapter_idx = home, confidence = 0.9)])

    splitting = Run(split_blobs, page_scorer = Fake(lopsided), chapter_curator = Fake(curator))
    split_state = await splitting.go(thread_id = "docs-distiller/fw/t7")
    truthy("select: the curator was asked about the oversize chapter", len(curated) >= 1)
    truthy("select: the chapter was split into parts", split_state["select_stats"]["n_chapters_out"] >= 2)
    check("select: every page still has a chapter", split_state["select_stats"]["n_assigned_docs"], split_state["select_stats"]["n_total_docs"])

    # --- order_chapters: a first round with too few valid samples is topped up once
    order_blobs = FakeBlobs()
    corpus(order_blobs)
    sequenced = {"n": 0}

    def thin_sequencer(payload):
        sequenced["n"] += 1
        if sequenced["n"] <= 3:
            raise RuntimeError("provider down")
        return sequencer(payload)

    thin = Run(order_blobs, chapter_sequencer = Fake(thin_sequencer))
    thin_state = await thin.go(thread_id = "docs-distiller/fw/t8")
    check("order: the missing samples were asked again", sequenced["n"], 8)
    check("order: five valid samples in the end", thin_state["order_chapters_stats"]["n_samples"], 5)
    print("planner graph: all checks passed")


asyncio.run(main())
