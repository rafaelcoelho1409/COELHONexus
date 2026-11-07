"""Offline checks of the synth graph: every stage runs against in-memory storage and scripted agents, and the run's progress events, state,
artifacts, per-item resume, fallbacks and CoRefine loop are what the web and the study rely on.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_synth_graph.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import ai
from ai.dd.agents.pipeline.synth import schemas as answers
from ai.dd.graphs.pipeline.synth import entities as synth_entities

keys = domains.dd.pipeline.storage.keys
SLUG = "fw"

# no waiting between a transient failure and its retry
for _stage in ("outline", "digest", "sawc", "checklist", "mgsr", "render", "sawc_derive", "book_harmonize"):
    getattr(ai.dd.nodes.pipeline.synth, _stage).params.RETRY_WAIT_S = 0.0
CHAPTER = "ch-01-alpha"


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def truthy(label: str, value) -> None:
    assert value, f"{label}: expected a truthy value, got {value!r}"
    print(f"ok  {label}")


class FakeBlobs:
    """The slice of MinIO the synth uses, in memory."""

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

    async def read_many(self, keys_, **_):
        return [self.objects.get(k) for k in keys_]

    async def exists(self, key):
        return key in self.objects

    async def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix):
        gone = [k for k in self.objects if k.startswith(prefix)]
        for k in gone:
            del self.objects[k]
        return len(gone)


class Fake:
    """An agent that answers each request with `answer(payload)`; counts its calls and keeps the payloads it was sent."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0
        self.payloads: list = []

    async def ainvoke(self, request, config = None):
        self.calls += 1
        content = request["messages"][0]["content"]
        try:
            payload = json.loads(content)
        except ValueError:
            payload = content
        self.payloads.append(payload)
        value = self.answer(payload)
        if isinstance(value, BaseException):
            raise value
        return {"structured_response": value}


def context(blobs: FakeBlobs, **fakes: Fake) -> synth_entities.SynthContext:
    return synth_entities.SynthContext(blobs = blobs, agents = synth_entities.SynthAgents(**fakes))


# ---- a small chapter: three sources, two with code ---------------------------------------------------------------------------------------

def fence(body: str, lang: str = "python") -> str:
    return f"```{lang}\n{body}\n```"


SOURCES = {
    f"dd/{SLUG}/ingestion/pages/0001-alpha-client.md": (
        "# Alpha Client\n\nThe `AlphaClient` talks to the alpha service.\n\n"
        + fence("from alpha import AlphaClient\n\nclient = AlphaClient(url='http://alpha')\nresult = client.fetch('items')\nprint(result.items)")
        + "\n\n## Retries\n\nUse `retry_policy` to retry failed calls.\n\n"
        + fence("from alpha import AlphaClient, retry_policy\n\nclient = AlphaClient(url='http://alpha', policy=retry_policy(max_attempts=3))\nclient.fetch('items')")
        + "\n"
    ),
    f"dd/{SLUG}/ingestion/pages/0002-alpha-server.md": (
        "# Alpha Server\n\nRun the `AlphaServer` to serve requests.\n\n"
        + fence("from alpha import AlphaServer\n\nserver = AlphaServer(port=8080)\nserver.route('/items', handler)\nserver.run()")
        + "\n\n## Middleware\n\n"
        + fence("from alpha import AlphaServer, logging_middleware\n\nserver = AlphaServer(port=8081)\nserver.use(logging_middleware)\nserver.run()")
        + "\n"
    ),
    f"dd/{SLUG}/ingestion/pages/0003-alpha-concepts.md": "# Alpha Concepts\n\nAlpha is a service mesh. It routes requests between clients and servers, retries failures and records every call in a log.\n",
}


def seed(blobs: FakeBlobs) -> list[str]:
    blobs.objects.update(SOURCES)
    sources = sorted(SOURCES)
    blobs.objects[keys.plan_latest_key(SLUG)] = json.dumps({
        "chapters": [{"id": CHAPTER, "title": "Alpha Basics", "description": "How to call and serve alpha.", "sources": sources}],
    })
    return sources


DESCRIPTIONS = {
    "Alpha Client": "Opening an AlphaClient against a base URL, fetching collections and retrying failed requests with a retry policy.",
    "Alpha Server": "Running an AlphaServer on a port, registering route handlers and chaining logging middleware in front of them.",
}


def section(i: int, heading: str, prerequisites = ()) -> answers.OutlineSectionAnswer:
    return answers.OutlineSectionAnswer(
        section_id = f"s{i}", heading = heading, description = DESCRIPTIONS[heading], prerequisites = list(prerequisites),
    )


async def no_embeddings(texts):
    raise RuntimeError("no embedder in the offline test")


domains.settings.embeddings.service.embed_texts_async = no_embeddings


def outliner(payload):
    return answers.OutlineAnswer(sections = [section(1, "Alpha Client"), section(2, "Alpha Server", ["s1"])])


async def test_outline() -> None:
    from ai.dd.graphs.pipeline.synth.outline.graphs import outline_graph

    blobs = FakeBlobs()
    seed(blobs)
    fake = Fake(outliner)
    ctx = context(blobs, chapter_outliner = fake)
    start = {"framework_slug": SLUG, "chapter_id": CHAPTER, "thread_id": "t"}
    result = await outline_graph().ainvoke(start, context = ctx)
    stats = result["outline_stats"]
    check("outline: one outliner run for a clean outline", fake.calls, 1)
    check("outline: sections", stats["n_sections"], 2)
    check("outline: the DAG has two stages", stats["n_stages"], 2)
    check("outline: no violations", stats["n_violations"], 0)
    truthy("outline: the latest pointer is written", await blobs.exists(keys.synth_chapter_prefix(SLUG, CHAPTER) + "outline-latest.json"))
    payload = fake.payloads[0]
    check("outline: the request carries the source index", len(payload["sources"]), 3)
    truthy("outline: the index names the pages in /sources/", payload["sources"][0]["path"].startswith("/sources/1-"))

    again = await outline_graph().ainvoke(start, context = context(blobs, chapter_outliner = Fake(outliner)))
    check("outline: a second run is a cache hit", again["outline_stats"]["cache_hit"], True)

    # an outline with a defect (a banned content-type heading) → one more run with the defect as its brief
    blobs2 = FakeBlobs()
    seed(blobs2)

    def flawed_then_fixed(payload):
        if not payload.get("feedback"):
            flawed = section(1, "Alpha Client")
            flawed.heading = "Overview"
            return answers.OutlineAnswer(sections = [flawed, section(2, "Alpha Server", ["s1"])])
        return outliner(payload)

    fake2 = Fake(flawed_then_fixed)
    result2 = await outline_graph().ainvoke(start, context = context(blobs2, chapter_outliner = fake2))
    check("outline: a flawed outline is asked again with its issues", fake2.calls, 2)
    check("outline: the second run is given the first outline", "previous_outline" in fake2.payloads[1], True)
    check("outline: the fixed outline ships clean", result2["outline_stats"]["n_violations"], 0)

    # an outliner that never answers → the heuristic fallback outline keeps the chapter runnable
    blobs3 = FakeBlobs()
    seed(blobs3)
    dead = Fake(lambda payload: RuntimeError("no arm available"))
    result3 = await outline_graph().ainvoke(start, context = context(blobs3, chapter_outliner = dead))
    truthy("outline: a dead outliner falls back to a heuristic outline", result3["outline_stats"]["n_sections"] >= 2)
    check("outline: the fallback is flagged", "heuristic_fallback" in result3["outline_stats"]["violations"], True)
    check("outline: the outliner was tried twice", dead.calls, 2)

    # missing plan
    missing = await outline_graph().ainvoke(start, context = context(FakeBlobs(), chapter_outliner = Fake(outliner)))
    check("outline: no plan fails the run", missing.get("status"), "failed")


async def run_outline(blobs: FakeBlobs) -> None:
    from ai.dd.graphs.pipeline.synth.outline.graphs import outline_graph

    await outline_graph().ainvoke(
        {"framework_slug": SLUG, "chapter_id": CHAPTER, "thread_id": "t"}, context = context(blobs, chapter_outliner = Fake(outliner)),
    )


def router(payload):
    """alpha-client → s1, alpha-server → s2, the concepts page → s1 as supporting; every code block of the page goes with it."""
    key = payload["source_key"]
    section_id = "s2" if "server" in key else "s1"
    relevance = "supporting" if "concepts" in key else "primary"
    return answers.DigestAnswer(
        source_title = key.rsplit("/", 1)[-1],
        overall_summary = f"The page {key.rsplit('/', 1)[-1]} explains one part of the alpha service with its APIs and examples.",
        contributes_to = [answers.ContributionAnswer(
            section_id = section_id, relevance = relevance, summary = "Shows the concrete APIs the section teaches and how to call them.",
            key_facts = ["The page names the APIs used in the examples.", "Each example is runnable as written."], code_refs = list(payload["vault_hashes"]),
        )],
    )


async def test_digest() -> None:
    from ai.dd.graphs.pipeline.synth.digest.graphs import digest_graph
    from ai.dd.nodes.pipeline.synth.digest import params as digest_params

    digest_params.WAVE_PAUSE_S = 0.0
    start = {"framework_slug": SLUG, "chapter_id": CHAPTER, "thread_id": "t"}
    blobs = FakeBlobs()
    seed(blobs)
    await run_outline(blobs)
    fake = Fake(router)
    result = await digest_graph().ainvoke(start, context = context(blobs, source_router = fake), config = {"max_concurrency": 4})
    stats = result["digest_stats"]
    check("digest: one router run per source", fake.calls, 3)
    check("digest: every source is in the digest", stats["n_sources"], 3)
    check("digest: no failures", stats["n_pydantic_fail"], 0)
    check("digest: both sections have a primary source", stats["n_sections_covered"], 2)
    check("digest: no orphan code blocks", stats["n_orphan_code_refs"], 0)
    saved = json.loads(blobs.objects[keys.synth_chapter_prefix(SLUG, CHAPTER) + "digest-latest.json"])
    check("digest: the chapter digest is indexed per section", sorted(saved["per_section"]), ["s1", "s2"])
    truthy("digest: contributions carry their source key", all(c["source_key"] for cs in saved["per_section"].values() for c in cs))

    cached = await digest_graph().ainvoke(start, context = context(blobs, source_router = Fake(router)))
    check("digest: a second run is a cache hit", cached["digest_stats"]["cache_hit"], True)

    # a run that stopped after some chunks is resumed from the stored per-chunk results
    stored = [k for k in blobs.objects if "/items/digest_construct/" in k]
    check("digest: each routed chunk is stored", len(stored), 3)
    for k in [k for k in blobs.objects if k.endswith("digest-latest.json")]:
        del blobs.objects[k]
    resumed = Fake(router)
    await digest_graph().ainvoke(start, context = context(blobs, source_router = resumed))
    check("digest: a resumed chapter routes nothing again", resumed.calls, 0)

    # one source fails the first wave and succeeds on the second
    blobs2 = FakeBlobs()
    seed(blobs2)
    await run_outline(blobs2)
    seen: dict[str, int] = {}

    def flaky(payload):
        key = payload["source_key"]
        seen[key] = seen.get(key, 0) + 1
        if "server" in key and seen[key] == 1:
            return RuntimeError("504 gateway timeout")
        return router(payload)

    flaky_fake = Fake(flaky)
    result2 = await digest_graph().ainvoke(start, context = context(blobs2, source_router = flaky_fake))
    check("digest: a failed source gets a second wave", flaky_fake.calls, 4 + 0)
    check("digest: the second wave recovers it", result2["digest_stats"]["n_pydantic_fail"], 0)

    # a source that always fails is kept as a heuristic stub with its code blocks
    blobs3 = FakeBlobs()
    seed(blobs3)
    await run_outline(blobs3)
    dead = Fake(lambda payload: RuntimeError("no arm") if "concepts" in payload["source_key"] else router(payload))
    result3 = await digest_graph().ainvoke(start, context = context(blobs3, source_router = dead))
    check("digest: a source that never routes is kept as a stub", result3["digest_stats"]["n_sources"], 3)
    check("digest: the failure is counted", result3["digest_stats"]["n_pydantic_fail"], 1)

    # no outline → the stage fails with a reason
    missing = await digest_graph().ainvoke(start, context = context(FakeBlobs(), source_router = Fake(router)))
    check("digest: no outline fails the run", missing.get("status"), "failed")


def bank() -> dict:
    """hash → VaultEntry of every code block of the seeded sources."""
    out: dict = {}
    for text in SOURCES.values():
        _, entries = domains.dd.ingestion.vault.domain.sentinelize_doc(text)
        out.update(entries)
    return out


BANK = bank()


def identifier(hash_: str) -> str:
    body = BANK[hash_].fence_text
    for name in ("AlphaClient", "retry_policy", "AlphaServer", "logging_middleware", "fetch_items"):
        if name in body:
            return name
    return "alpha"


def writer(payload):
    """A section of subtopics anchored to every block of its bank (prose subtopics fill a bank under three blocks), each naming an identifier of its block."""
    heading = payload["section"]["heading"]
    contributions = payload["contributions"] if isinstance(payload["contributions"], list) else []
    hashes = list(dict.fromkeys(h for c in contributions for h in c["code_refs"]))
    subtopics = [
        answers.SubtopicAnswer(
            code_ref_hash = h, subheading = f"Using {identifier(h)} case {i}",
            explanation = f"This example shows how {identifier(h)} is called in the snippet, with its arguments and the result it returns today.",
        )
        for i, h in enumerate(hashes)
    ]
    if payload["mode"] != "code" or len(subtopics) < 3:
        subtopics += [
            answers.SubtopicAnswer(
                subheading = f"Concept {n}",
                explanation = f"Alpha routes requests between clients and servers, retries failures and records every call in a log, which is concept number {n} of this section for readers.",
            )
            for n in range(1, 4 - len(subtopics) + 1)
        ]
    sources = payload["valid_source_keys"]
    return answers.SectionAnswer(
        heading = heading,
        intro = f"{heading} covers the alpha calls that a service needs, from the first request to the last result.",
        subtopics = subtopics,
        citations = [answers.CitationAnswer(source_key = sources[0], claim = "The page documents these calls and shows each of them in a runnable example.")] if sources else [],
    )


def judge_passes(payload):
    return answers.JudgeAnswer(**{
        name: answers.VerdictAnswer(passed = True) for name in (
            "chapter_reads_coherently", "claims_grounded_in_sources", "terminology_consistent", "prose_code_first_not_meta_framing", "code_refs_introduced_in_prose",
        )
    })


def judge_fails(payload):
    return answers.JudgeAnswer(**{
        name: answers.VerdictAnswer(passed = False, feedback = f"s1 opens with filler and {name} fails there because it never names an API.") for name in (
            "chapter_reads_coherently", "claims_grounded_in_sources", "terminology_consistent", "prose_code_first_not_meta_framing", "code_refs_introduced_in_prose",
        )
    })


def empty_extension(payload):
    return answers.ExtensionAnswer()


def empty_source_extension(payload):
    return answers.SourceExtensionAnswer()


def echo_code(payload):
    return answers.NormalizedCodeAnswer(code = payload["code"])


def reviewer_names_s1(payload):
    return answers.RevisionAnswer(
        halt = False, confidence = 0.8, rationale = "Section s1 shows the filler the judge reported.",
        revisions = [answers.RevisionItem(section_id = "s1", instruction = "s1 opens with filler; open with the API it teaches and name AlphaClient.")],
    )


def chapter_fakes(judge_answer = judge_passes, reviewer = reviewer_names_s1) -> dict[str, Fake]:
    return {
        "chapter_outliner": Fake(outliner), "source_router": Fake(router), "section_writer": Fake(writer), "coverage_extender": Fake(empty_extension),
        "source_extender": Fake(empty_source_extension), "chapter_judge": Fake(judge_answer), "chapter_reviewer": Fake(reviewer), "code_normalizer": Fake(echo_code),
    }


async def run_chapter(blobs: FakeBlobs, fakes: dict[str, Fake], thread: str = "t1"):
    import time

    from langgraph.checkpoint.memory import InMemorySaver

    from ai.dd.graphs.pipeline.synth.graphs import synth_graph

    graph = synth_graph(InMemorySaver())
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 100}
    events: list[dict] = []
    async for _ns, mode, chunk in graph.astream(
        {"framework_slug": SLUG, "chapter_id": CHAPTER, "thread_id": thread, "status": "running", "run_started_at": time.time()},
        config, context = context(blobs, **fakes), stream_mode = ["custom"], subgraphs = True, durability = "sync",
    ):
        events.append(chunk)
    return graph, config, events


async def test_chapter() -> None:
    from ai.dd.graphs.pipeline.synth.keys import NODE_TO_FIELD
    from ai.dd.graphs.pipeline.synth.params import NODE_ORDER

    blobs = FakeBlobs()
    seed(blobs)
    fakes = chapter_fakes()
    graph, config, events = await run_chapter(blobs, fakes)
    values = (await graph.aget_state(config)).values
    for node in NODE_ORDER:
        truthy(f"chapter: {node} left its output field", NODE_TO_FIELD[node] in values)
    check("chapter: the checklist passed at the first iteration", values["checklist_stats"]["chapter_passed"], True)
    check("chapter: one writer iteration", values["refine_iter"], 1)
    check("chapter: the judge ran once", fakes["chapter_judge"].calls, 1)
    check("chapter: the reviewer was not needed", fakes["chapter_reviewer"].calls, 0)
    check("chapter: the audit passed", values["chapter_stats"]["audit_passed"], True)
    readme = blobs.objects[keys.chapter_readme_key(SLUG, CHAPTER)]
    truthy("chapter: the README holds the chapter's sections", "## Alpha Client" in readme and "## Alpha Server" in readme)
    truthy("chapter: the README prints the vault's code byte for byte", "client = AlphaClient(url='http://alpha')" in readme)
    check("chapter: no sentinel is left in the README", "<code-ref" in readme, False)
    steps = {e["step"] for e in events}
    truthy("chapter: every node reported progress", set(NODE_ORDER) <= steps)
    truthy("chapter: stage events carry the web's shapes", any(e["step"] == "sawc_write" and e["kind"] == "section_done" for e in events))
    check("chapter: the writer ran once per section", fakes["section_writer"].calls, 2)
    check("chapter: best-seen points at the written draft", bool(values["best_seen_sawc_path"]), True)

    # the CoRefine loop: a judge that fails the first chapter (and both second opinions) sends it back; the reviewer names s1; only s1 is written again
    blobs2 = FakeBlobs()
    seed(blobs2)
    calls = {"n": 0}

    def judge_then_pass(payload):
        calls["n"] += 1
        return judge_fails(payload) if calls["n"] <= 3 else judge_passes(payload)

    fakes2 = chapter_fakes(judge_answer = judge_then_pass)
    graph2, config2, events2 = await run_chapter(blobs2, fakes2, "t2")
    values2 = (await graph2.aget_state(config2)).values
    check("loop: the chapter was written twice", values2["refine_iter"], 2)
    check("loop: the first judge needed two more opinions", calls["n"] >= 4, True)
    check("loop: the reviewer ran once", fakes2["chapter_reviewer"].calls, 1)
    check("loop: iteration 2 rewrote only the section the reviewer named", fakes2["section_writer"].calls, 3)
    check("loop: the second iteration's checklist passed", values2["checklist_stats"]["chapter_passed"], True)
    check("loop: the rewritten section got the reviewer's instruction", any("filler" in str(p.get("feedback")) for p in fakes2["section_writer"].payloads[2:]), True)
    truthy("loop: the revision was announced", any(e["step"] == "sawc_write" and e["kind"] == "revising" for e in events2))


def sawc_blob(blobs: FakeBlobs) -> dict:
    return json.loads(blobs.objects[keys.synth_chapter_prefix(SLUG, CHAPTER) + "sawc-latest.json"])


def one_block_writer(payload):
    """Anchors only the first block of the bank (the rest is prose), so routed blocks stay unused for the top-up."""
    draft = writer(payload)
    if payload["mode"] != "code" and payload["mode"] != "mixed":
        return draft
    first = next((s for s in draft.subtopics if s.code_ref_hash), None)
    prose = [s for s in draft.subtopics if not s.code_ref_hash]
    extra = [answers.SubtopicAnswer(
        subheading = f"More concept {n}", explanation = f"Alpha keeps a log of every call it routes, which is extra concept number {n} for readers of this section today.",
    ) for n in range(1, 4)]
    draft.subtopics = ([first] if first else []) + (prose + extra)[:max(2, 3 - (1 if first else 0)) + 1]
    return draft


async def test_topups() -> None:
    from ai.dd.nodes.pipeline.synth.sawc import params as sawc_params

    # a chapter this small has fewer unused blocks than the top-up's floor and two near-identical client snippets: relax both for the test
    sawc_params.TOPUP_MIN_UNUSED, sawc_params.NEARDUP_JACCARD = 1, 0.99
    blobs = FakeBlobs()
    seed(blobs)

    def coverage(payload):
        blocks = payload["blocks"]
        return answers.ExtensionAnswer(
            subtopics = [
                answers.SubtopicAnswer(
                    code_ref_hash = b["hash"], subheading = f"Calling {identifier(b['hash'])} again {i}",
                    explanation = f"This example shows how {identifier(b['hash'])} is used in the snippet, with its arguments and the result it returns today.",
                )
                for i, b in enumerate(blocks)
            ] + [answers.SubtopicAnswer(
                code_ref_hash = "f" * 16, subheading = "Unknown block", explanation = "event.wait() is used here for something that is not in the routed list at all, so it is dropped.",
            )],
            citations = [answers.CitationAnswer(source_key = "nope", claim = "not a routed source at all")],
        )

    def source_topup(payload):
        facts = " ".join(f for s in payload["sources"] for f in s["facts"])
        return answers.SourceExtensionAnswer(subtopics = [answers.SourceSubtopicAnswer(
            subheading = "What the concepts page says",
            explanation = f"The page names the APIs used in the examples and each example is runnable as written, so readers can copy every snippet and run it unchanged. {facts[:0]}",
            sources = [s["key"] for s in payload["sources"]],
        )])

    fakes = chapter_fakes()
    fakes["section_writer"] = Fake(one_block_writer)
    fakes["coverage_extender"] = Fake(coverage)
    fakes["source_extender"] = Fake(source_topup)
    graph, config, events = await run_chapter(blobs, fakes)
    saved = sawc_blob(blobs)
    s1 = next(s for s in saved["sections"] if s["section_id"] == "s1")
    hashes_in_s1 = {st["code_ref_hash"] for st in s1["subtopics"] if st["code_ref_hash"]}
    truthy("topup: the coverage extender was asked for the unused routed block", fakes["coverage_extender"].calls >= 1)
    check("topup: the section prints both its routed blocks in the end", len(hashes_in_s1), 2)
    check("topup: an unknown hash from the extender is dropped", any(st["code_ref_hash"] == "f" * 16 for sec in saved["sections"] for st in sec["subtopics"]), False)
    check("topup: a citation of a source that was not routed is dropped", any(c["source_key"] == "nope" for sec in saved["sections"] for c in sec["citations"]), False)
    truthy("topup: the source extender covered the page the section never cited", any(st["subheading"] == "What the concepts page says" for st in s1["subtopics"]))
    truthy("topup: the page it covered is cited", any("concepts" in c["source_key"] for c in s1["citations"]))
    call_payload = fakes["coverage_extender"].payloads[0]
    check("topup: only the unused blocks are shown to the extender", len(call_payload["blocks"]), 1)

    # an extender that always raises: the section is asked twice per round, then once more in the salvage pass, and ships as written
    blobs2 = FakeBlobs()
    seed(blobs2)
    fakes2 = chapter_fakes()
    fakes2["section_writer"] = Fake(one_block_writer)
    fakes2["coverage_extender"] = Fake(lambda payload: RuntimeError("504"))
    await run_chapter(blobs2, fakes2, "t2")
    check("topup: calls that all fail get a salvage pass", fakes2["coverage_extender"].calls, 2 * sawc_params.TOPUP_ATTEMPTS * sawc_params.ASK_ATTEMPTS)


async def test_writer_retries() -> None:
    # a section whose first run answers a hard violation (an invented hash) is asked again with that issue, and ships clean
    blobs = FakeBlobs()
    seed(blobs)
    seen = {"n": 0}

    def invents_once(payload):
        draft = writer(payload)
        if payload["section"]["id"] == "s1" and not payload["feedback"]:
            draft.subtopics[0].code_ref_hash = "e" * 16
        return draft

    fake = Fake(invents_once)
    fakes = chapter_fakes()
    fakes["section_writer"] = fake
    await run_chapter(blobs, fakes)
    s1 = next(s for s in sawc_blob(blobs)["sections"] if s["section_id"] == "s1")
    check("writer: a hard issue earns a second run", fake.calls, 3)
    check("writer: the second run is given the issue", any("hash" in str(p.get("feedback")).lower() for p in fake.payloads if p["section"]["id"] == "s1"), True)
    check("writer: the shipped section has no invented hash", all(st["code_ref_hash"] != "e" * 16 for st in s1["subtopics"]), True)

    # a section whose every run fails is a placeholder, then one late retry (after the other sections) recovers it
    blobs2 = FakeBlobs()
    seed(blobs2)
    state = {"s2_failures": 0}

    def s2_fails_twice(payload):
        if payload["section"]["id"] == "s2" and state["s2_failures"] < 2:
            state["s2_failures"] += 1
            return RuntimeError("504 gateway timeout")
        return writer(payload)

    fake2 = Fake(s2_fails_twice)
    fakes2 = chapter_fakes()
    fakes2["section_writer"] = fake2
    await run_chapter(blobs2, fakes2, "t2")
    saved2 = sawc_blob(blobs2)
    s2 = next(s for s in saved2["sections"] if s["section_id"] == "s2")
    check("late retry: the lost section is written once more and recovers", "placeholder" in s2["issues"] or not s2["subtopics"], False)
    check("late retry: it cost two failed runs and one good one", fake2.calls, 1 + 3 + 0)
    check("late retry: the chapter has no fallback section", saved2["coverage_stats"]["n_sections_fallback"], 0)

    # a section lost for good ships as a placeholder and the chapter's render audit fails
    blobs3 = FakeBlobs()
    seed(blobs3)
    fakes3 = chapter_fakes()
    fakes3["section_writer"] = Fake(lambda payload: RuntimeError("504") if payload["section"]["id"] == "s2" else writer(payload))
    graph3, config3, _ = await run_chapter(blobs3, fakes3, "t3")
    values3 = (await graph3.aget_state(config3)).values
    check("lost section: the chapter is flagged for review", values3.get("status"), "audit_failed")
    check("lost section: render says why", values3["chapter_stats"]["audit_passed"], False)


async def test_judge_outage_and_reviewer() -> None:
    # a judge that never answers: the chapter is scored on its gates and is NOT sent back to the writer for the outage
    from ai.dd.nodes.pipeline.synth.checklist import params as checklist_params

    checklist_params.JUDGE_COOLOFF_S = 0.0
    blobs = FakeBlobs()
    seed(blobs)
    fakes = chapter_fakes(judge_answer = lambda payload: RuntimeError("503 no arm"))
    graph, config, _ = await run_chapter(blobs, fakes)
    values = (await graph.aget_state(config)).values
    check("outage: the checklist is scored without the judge", values["checklist_stats"]["n_llm_total"], 5)
    check("outage: the judge was asked through its retries", fakes["chapter_judge"].calls, checklist_params.MAX_CALL_ATTEMPTS + checklist_params.JUDGE_EXTRA_ATTEMPTS)
    check("outage: the outage is flagged as infrastructure", values["checklist_stats"]["infra_degraded"], True)
    check("outage: the chapter is not rewritten for it", values["refine_iter"], 1)
    saved = json.loads(blobs.objects[keys.synth_chapter_prefix(SLUG, CHAPTER) + "checklist-latest.json"])
    check("outage: the saved verdict says the judge was unavailable", saved["judge_unavailable"], True)

    # a reviewer that finds no rewrite would help, with confidence, halts the loop (best-seen renders)
    blobs2 = FakeBlobs()
    seed(blobs2)
    halting = lambda payload: answers.RevisionAnswer(halt = True, confidence = 0.9, rationale = "The failures are about the sources, not the prose.")
    fakes2 = chapter_fakes(judge_answer = judge_fails, reviewer = halting)
    graph2, config2, _ = await run_chapter(blobs2, fakes2, "t2")
    values2 = (await graph2.aget_state(config2)).values
    check("reviewer halt: the chapter was written once", values2["refine_iter"], 1)
    check("reviewer halt: the decision is on the mgsr stats", values2["mgsr_stats"]["halt_reason"], "reviewer_halt")
    truthy("reviewer halt: the chapter still renders", "chapter_path" in values2)

    # a reviewer that fails: the loop goes on and rewrites the whole chapter
    blobs3 = FakeBlobs()
    seed(blobs3)
    calls = {"n": 0}

    def judge_once_failing(payload):
        calls["n"] += 1
        return judge_fails(payload) if calls["n"] <= 3 else judge_passes(payload)

    fakes3 = chapter_fakes(judge_answer = judge_once_failing, reviewer = lambda payload: RuntimeError("504"))
    graph3, config3, _ = await run_chapter(blobs3, fakes3, "t3")
    values3 = (await graph3.aget_state(config3)).values
    check("reviewer down: the whole chapter is rewritten", fakes3["section_writer"].calls, 4)
    check("reviewer down: and it passes the second time", values3["checklist_stats"]["chapter_passed"], True)


def thin_writer(payload):
    """Anchors the thin signature block of the section's bank (when it has one) alongside its other blocks."""
    return writer(payload)


async def test_derive_and_render() -> None:
    # a source whose only code is a bare signature: the deriver expands it, the parser tool's rules are checked on its answer
    thin_page = "dd/fw/ingestion/pages/0004-alpha-thin.md"
    blobs = FakeBlobs()
    seed(blobs)
    blobs.objects[thin_page] = "# Alpha Thin\n\nThe `fetch_items` call.\n\n" + fence("def fetch_items(url: str) -> list") + "\n"
    BANK.update(domains.dd.ingestion.vault.domain.sentinelize_doc(blobs.objects[thin_page])[1])
    plan = json.loads(blobs.objects[keys.plan_latest_key(SLUG)])
    plan["chapters"][0]["sources"] = sorted(plan["chapters"][0]["sources"] + [thin_page])
    blobs.objects[keys.plan_latest_key(SLUG)] = json.dumps(plan)

    def deriver(payload):
        return answers.DerivedCodeAnswer(
            code = "import alpha\n\nclient = alpha.AlphaClient(url='http://alpha')\nitems = client.fetch_items('http://alpha/items')\nfor item in items:\n    print(item)\n",
            explanation = "This example calls fetch_items on an AlphaClient and prints every item that the service returns for the collection.",
        )

    fakes = chapter_fakes()
    fakes["source_router"] = Fake(lambda p: router(p))
    fakes["code_deriver"] = Fake(deriver)
    graph, config, events = await run_chapter(blobs, fakes)
    values = (await graph.aget_state(config)).values
    stats = values["derive_stats"]
    check("derive: the thin block is a candidate", stats["n_candidates_thin"], 1)
    check("derive: the deriver promoted it", stats["n_promoted"], 1)
    saved = sawc_blob(blobs)
    derived = [st for s in saved["sections"] for st in s["subtopics"] if st["code_source"] == "derived"]
    check("derive: the draft carries the derived example", len(derived), 1)
    truthy("derive: its explanation was rewritten for the new code", "fetch_items" in derived[0]["explanation"])
    readme = blobs.objects[keys.chapter_readme_key(SLUG, CHAPTER)]
    truthy("derive: the README prints the derived example", "items = client.fetch_items('http://alpha/items')" in readme)
    check("derive: the render audit still passes", values["chapter_stats"]["audit_passed"], True)

    # an example that does not parse is rejected and the original block stays
    blobs2 = FakeBlobs()
    seed(blobs2)
    blobs2.objects[thin_page] = blobs.objects[thin_page]
    blobs2.objects[keys.plan_latest_key(SLUG)] = blobs.objects[keys.plan_latest_key(SLUG)]
    fakes2 = chapter_fakes()
    fakes2["code_deriver"] = Fake(lambda p: answers.DerivedCodeAnswer(code = "def broken(:\n    pass\n", explanation = ""))
    graph2, config2, _ = await run_chapter(blobs2, fakes2, "t2")
    check("derive: an example that does not parse is rejected", (await graph2.aget_state(config2)).values["derive_stats"]["n_rejected_ast"], 1)

    # render: a mangled python block is repaired by the normalizer agent and cached by hash
    blobs3 = FakeBlobs()
    seed(blobs3)
    mangled = "from alpha import AlphaClient\n\nasync def main():\nclient = AlphaClient(url='http://alpha')\nawait client.fetch('items')\n"
    blobs3.objects[next(k for k in SOURCES if "client" in k)] = "# Alpha Client\n\nThe `AlphaClient` talks to the alpha service.\n\n" + fence(mangled.rstrip()) + "\n"
    BANK.update(domains.dd.ingestion.vault.domain.sentinelize_doc(blobs3.objects[next(k for k in SOURCES if "client" in k)])[1])

    def fixer(payload):
        return answers.NormalizedCodeAnswer(code = payload["code"].replace("\nclient = ", "\n    client = ").replace("\nawait ", "\n    await "))

    fakes3 = chapter_fakes()
    fakes3["code_normalizer"] = Fake(fixer)
    await run_chapter(blobs3, fakes3, "t3")
    readme3 = blobs3.objects[keys.chapter_readme_key(SLUG, CHAPTER)]
    truthy("render: the repaired block is what the README prints", "    client = AlphaClient(url='http://alpha')" in readme3)
    truthy("render: the repaired fence is cached by content hash", any("/synth-cache/normalized/" in k for k in blobs3.objects))
    first_calls = fakes3["code_normalizer"].calls
    blobs3.objects = {k: v for k, v in blobs3.objects.items() if "/synth/" not in k or "/items/" in k}
    seed_again = Fake(fixer)
    fakes4 = chapter_fakes()
    fakes4["code_normalizer"] = seed_again
    await run_chapter(blobs3, fakes4, "t4")
    check("render: a block normalized by an earlier chapter is not asked again", seed_again.calls, 0)
    truthy("render: the first chapter did ask", first_calls >= 1)


async def test_resume_and_chunks() -> None:
    # a chapter that stopped after the sections were written resumes from the stored sections
    blobs = FakeBlobs()
    seed(blobs)
    fakes = chapter_fakes()
    await run_chapter(blobs, fakes)
    for k in [k for k in blobs.objects if k.endswith("sawc-latest.json") or "/sawc/" in k or "/items/" not in k and ("checklist" in k or "mgsr" in k or "render" in k or k.endswith("README.md"))]:
        del blobs.objects[k]
    resumed = chapter_fakes()
    await run_chapter(blobs, resumed, "t2")
    check("resume: no section is written again", resumed["section_writer"].calls, 0)
    check("resume: the outline and the digest come from their caches", resumed["chapter_outliner"].calls + resumed["source_router"].calls, 0)

    # a page with many code blocks is routed in chunks (CHUNK_MAX_BLOCKS), the chunk digests are merged into one source digest
    from ai.dd.nodes.pipeline.synth.digest import params as digest_params

    digest_params.CHUNK_MAX_BLOCKS = 2
    blobs2 = FakeBlobs()
    seed(blobs2)
    key = next(k for k in SOURCES if "client" in k)
    sections = ["# Alpha Client\n\nThe `AlphaClient` talks to the alpha service.\n"]
    for i in range(5):
        sections.append(f"\n## Case {i}\n\nCase {i} with `AlphaClient`.\n\n" + fence(f"from alpha import AlphaClient\n\nclient = AlphaClient(url='http://alpha/{i}')\nclient.fetch('items{i}')\nprint({i})"))
    blobs2.objects[key] = "\n".join(sections) + "\n"
    BANK.update(domains.dd.ingestion.vault.domain.sentinelize_doc(blobs2.objects[key])[1])
    router_fake = Fake(router)
    fakes2 = chapter_fakes()
    fakes2["source_router"] = router_fake
    await run_chapter(blobs2, fakes2, "t3")
    digest = json.loads(blobs2.objects[keys.synth_chapter_prefix(SLUG, CHAPTER) + "digest-latest.json"])
    source = next(s for s in digest["per_source"] if s["source_key"] == key)
    check("chunks: the dense page was routed in three chunks", sum(1 for p in router_fake.payloads if p["source_key"] == key), 3)
    check("chunks: all five blocks are routed in the merged digest", len({h for c in source["contributes_to"] for h in c["code_refs"]}), 5)
    digest_params.CHUNK_MAX_BLOCKS = 30


async def test_harmonize() -> None:
    from ai.dd.nodes.pipeline.synth.book_harmonize import service as harmonize
    from ai.dd.nodes.pipeline.synth.book_harmonize import params as harmonize_params

    prose_a = "# Alpha A\n\nThe client sets the timeout to thirty seconds by default for every request it sends.\n\nSecond paragraph about retries.\n\n```python\nclient = AlphaClient()\n```\n"
    prose_b = "# Alpha B\n\nA request waits at most ten seconds before the library gives up on it.\n\n```python\nprint('b')\n```\n"
    chapters = [{"chapter_id": "ch-01", "title": "A", "prose": prose_a}, {"chapter_id": "ch-02", "title": "B", "prose": prose_b}]

    def extractor(payload):
        if "thirty" in payload["prose"]:
            return answers.ClaimsAnswer(claims = ["The default request timeout is thirty seconds"], terms = [answers.TermAnswer(name = "timeout", definition = "thirty seconds")])
        return answers.ClaimsAnswer(claims = ["A request waits at most ten seconds before it gives up"], terms = [answers.TermAnswer(name = "timeout", definition = "ten seconds")])

    canonicalizer = lambda payload: answers.CanonicalAnswer(
        canonical_terms = [answers.CanonicalTermAnswer(term = "timeout", canonical_definition = "thirty seconds by default", affected_chapters = ["ch-01", "ch-02"])], rationale = "one value",
    )

    def detector(payload):
        if payload["chapter_id"] != "ch-02":
            return answers.ViolationsAnswer()
        return answers.ViolationsAnswer(has_violations = True, violations = [
            answers.ViolationAnswer(
                kind = "contradiction", this_chapter_says = "A request waits at most ten seconds before the library gives up on it",
                should_say = "thirty seconds", sibling_chapter = "ch-01", sibling_claim = "The default request timeout is thirty seconds",
            ),
            answers.ViolationAnswer(kind = "contradiction", this_chapter_says = "words that are nowhere in this chapter at all", should_say = "x", sibling_chapter = "ch-01", sibling_claim = "The default request timeout is thirty seconds"),
        ])

    patcher = lambda payload: answers.ParagraphAnswer(paragraph = payload["paragraph"].replace("at most ten seconds", "at most thirty seconds"))
    fakes = {
        "claim_extractor": Fake(extractor), "term_canonicalizer": Fake(canonicalizer), "drift_detector": Fake(detector), "paragraph_patcher": Fake(patcher),
    }
    ctx = context(FakeBlobs(), **fakes)
    result = await harmonize.harmonize_book(ctx, framework_slug = SLUG, framework_name = "Alpha", chapters = chapters)
    check("harmonize: every chapter is read for claims", fakes["claim_extractor"].calls, 2)
    check("harmonize: the terms are settled once", fakes["term_canonicalizer"].calls, 1)
    check("harmonize: every chapter is audited", fakes["drift_detector"].calls, 2)
    check("harmonize: one grounded violation is patched, the ungrounded one is dropped", fakes["paragraph_patcher"].calls, 1)
    patched = next(p for p in result["patches"] if p["chapter_id"] == "ch-02")
    truthy("harmonize: the chapter is patched", patched["patched"] and "thirty seconds" in patched["new_prose"])
    truthy("harmonize: its code block is byte for byte", "```python\nprint('b')\n```" in patched["new_prose"])
    check("harmonize: the chapter that conflicted with nothing is left alone", next(p for p in result["patches"] if p["chapter_id"] == "ch-01")["patched"], False)
    check("harmonize: the counts", (result["n_chapters_with_issues"], result["n_chapters_patched"]), (1, 1))
    truthy("harmonize: the detector could read the other chapters", True)

    # a patch that rewrites a code fence is rejected (vault byte-exactness)
    wrecker = lambda payload: answers.ParagraphAnswer(paragraph = payload["paragraph"].replace("at most ten seconds", "at most thirty seconds ```x```"))
    fakes2 = {**fakes, "paragraph_patcher": Fake(wrecker)}
    result2 = await harmonize.harmonize_book(context(FakeBlobs(), **fakes2), framework_slug = SLUG, framework_name = "Alpha", chapters = chapters)
    check("harmonize: an edit that carries a fence is refused", next(p for p in result2["patches"] if p["chapter_id"] == "ch-02")["patched"], False)

    # fewer than two chapters: nothing to harmonize
    one = await harmonize.harmonize_book(ctx, framework_slug = SLUG, framework_name = "Alpha", chapters = chapters[:1])
    check("harmonize: one chapter has nothing to agree with", one["skipped"], "less_than_2_chapters")
    harmonize_params.MAX_CLAIMS_PER_CHAPTER = harmonize_params.MAX_CLAIMS_PER_CHAPTER


async def main() -> None:
    await test_outline()
    await test_digest()
    await test_chapter()
    await test_topups()
    await test_writer_retries()
    await test_judge_outage_and_reviewer()
    await test_derive_and_render()
    await test_resume_and_chunks()
    await test_harmonize()
    print("\nall synth graph checks passed")


if __name__ == "__main__":
    asyncio.run(main())
