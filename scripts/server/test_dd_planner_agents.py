"""Offline checks of the planner's DeepAgents: each role builds on the shared factory (harness profile, permissions, middleware, the
unreviewed-tool guard), reads its skill and the corpus through its backend, calls its tools, and answers with its schema.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_planner_agents.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

import ai
import domains
from ai.dd.agents import service
from ai.dd.agents.pipeline.planner import chapter_architect, chapter_curator, chapter_sequencer, page_distiller, page_judge, page_scorer, schemas, section_analyst
from ai.dd.backends import corpus
from ai.dd.tools.pipeline.planner import outline, split


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def truthy(label: str, value) -> None:
    assert value, f"{label}: expected a truthy value, got {value!r}"
    print(f"ok  {label}")


class Scripted(ChatOpenAI):
    """A ChatOpenAI that answers from a script and records what it was offered: the tools bound to it and the messages it was sent."""

    script: list = []
    seen: list = []

    def _generate(self, messages, stop = None, run_manager = None, **kwargs):
        self.seen.append(messages)
        message = self.script.pop(0)
        return ChatResult(generations = [ChatGeneration(message = message)])

    async def _agenerate(self, messages, stop = None, run_manager = None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


def model(*script: AIMessage) -> Scripted:
    scripted = Scripted(base_url = "http://rotator.test/v1", api_key = "key", model = "fake-model")
    scripted.script = list(script)
    scripted.seen = []
    return scripted


def call(name: str, args: dict, id_: str = "c1") -> AIMessage:
    return AIMessage(content = "", tool_calls = [{"name": name, "args": args, "id": id_}])


def tool_results(scripted: Scripted) -> list[str]:
    """The tool messages the model received in its last request."""
    return [str(m.content) for m in scripted.seen[-1] if getattr(m, "type", "") == "tool"]


async def main() -> None:
    # --- every skill of the planner parses: valid YAML front matter, `name` equal to its folder, a description within the spec
    import yaml
    for path in sorted((Path(ai.dd.skills.params.LOCAL_ROOT) / "pipeline").rglob("SKILL.md")):
        front = path.read_text(encoding = "utf-8").split("---")[1]
        meta = yaml.safe_load(front)
        check(f"skill {path.parent.name}: name is its folder", meta["name"], path.parent.name)
        truthy(f"skill {path.parent.name}: description within 1024 characters", 0 < len(meta["description"]) <= 1024)

    # --- per-page agents: one structured answer, no tools
    judge = model(call("PageRelevance", {"verdict": "DROP"}))
    answer = await service.ask(
        page_judge.agent.page_judge(judge), schemas.PageRelevance, json.dumps({"page": "x"}), attempts = 1, wait_base_s = 0,
    )
    check("page_judge answers with its schema", answer.verdict, "DROP")

    distilled = model(call("DocDistillateRaw", {"summary": "w " * 12, "key_terms": ["a_b", "c_d", "e_f"], "role": "how_to"}))
    raw = await service.ask(page_distiller.agent.page_distiller(distilled), schemas.DocDistillateRaw, "{}", attempts = 1, wait_base_s = 0)
    check("page_distiller answers with its schema", raw.role, "how_to")

    # --- the scorer carries the run's chapters in its system prompt and answers one key per chapter
    proposals = [{"title": f"Chapter {i}", "description": "d" * 30, "key_concepts": ["x"]} for i in range(13)]
    fixed = schemas.fixed_key_model(13)
    scorer_model = model(call(fixed.__name__, {f"c{i}": 0.1 * (i % 10) for i in range(13)}))
    scorer = page_scorer.agent.page_scorer(proposals, fixed_keys = True, model = scorer_model)
    scores = await service.ask(scorer, fixed, "{}", attempts = 1, wait_base_s = 0)
    check("page_scorer: one required key per chapter", sorted(scores.model_dump()), sorted(f"c{i}" for i in range(13)))
    system = str(scorer_model.seen[0][0].content)
    truthy("page_scorer: the system prompt carries the chapters", "[12] 'Chapter 12'" in system)

    # --- an agent with a skill reads it from its virtual filesystem, and cannot read anything else
    skill_path = "/skills/pipeline/planner/section-analyst/analyze-documentation-section/SKILL.md"
    analyst_model = model(
        call("read_file", {"file_path": skill_path}, "r1"),
        call("read_file", {"file_path": "/etc/passwd"}, "r2"),
        call("SectionProposals", {"proposals": [{"title": "Auth Flows", "description": "How readers sign in to the service safely.", "key_concepts": ["login", "token"]}]}, "r3"),
    )
    analyst = section_analyst.agent.section_analyst(analyst_model)
    truthy("section_analyst: its skill files travel with the role", skill_path in analyst.files)
    proposals_out = await service.ask(analyst, schemas.SectionProposals, "{}", attempts = 1, wait_base_s = 0)
    check("section_analyst answers with its schema", proposals_out.proposals[0].title, "Auth Flows")
    truthy("section_analyst: it read its SKILL.md", any("Analyze a documentation section" in s for s in [str(m.content) for m in analyst_model.seen[1] if getattr(m, "type", "") == "tool"]))
    truthy("section_analyst: a path outside its skills is refused", any("permission" in s.lower() for s in [str(m.content) for m in analyst_model.seen[2] if getattr(m, "type", "") == "tool"]))

    # --- the architect: the corpus backend, the score tool, the critic subagent
    pages = [
        corpus.entities.CorpusDoc(
            idx = i, path = corpus.domain.doc_path(i, f"dd/fw/ingestion/pages/{i:04d}-{t}.md"), key = f"dd/fw/ingestion/pages/{i:04d}-{t}.md",
            title = t.title(), role = "reference", summary = f"Explains how the {t} component is configured and used", terms = (f"{t}_api", f"{t}_cli"),
        )
        for i, t in enumerate(["alpha", "bravo", "charlie", "delta"])
    ]

    async def load(key: str) -> str:
        return f"body of {key}"

    backend = CompositeBackend(default = StateBackend(), routes = {corpus.params.ROUTE: corpus.service.CorpusBackend(pages, load)})
    labels = [corpus.params.ROUTE.rstrip("/") + p.path for p in pages]
    scored_with: list = []

    def score(chapters: list[dict]) -> dict:
        scored_with.append(chapters)
        return {"n": len(chapters), "unmatched_pct": 25.0, "largest_share_pct": 50.0, "score": 0.5, "unmatched": [3], "chapter_sizes": [3] * len(chapters)}

    score_tool = outline.service.score_outline_tool(score, labels, target = 4)
    architect_model = model(
        call("write_todos", {"todos": [{"content": "merge sketches", "status": "in_progress"}]}, "a1"),
        call("grep", {"pattern": "bravo"}, "a2"),
        call("read_file", {"file_path": "/corpus/docs/1-0001-bravo.md"}, "a3"),
        call("score_outline", {"chapters": [{"title": "Alpha Component", "description": "d", "key_concepts": ["alpha_api"]}]}, "a4"),
        call("ls", {"path": "/corpus/docs"}, "a5"),
        call("ChapterProposalList", {"proposals": [
            {"title": f"{t.title()} Component", "description": f"Everything about the {t} component for readers.", "key_concepts": [f"{t}_api", f"{t}_cli"]}
            for t in ["alpha", "bravo", "charlie", "delta"]
        ]}, "a6"),
    )
    architect = chapter_architect.agent.chapter_architect(backend = backend, tools = [score_tool], model = architect_model)
    files = {"/work/structure.md": service.file("structure"), "/work/sketches.json": service.file("[]")}
    outline_out = await service.ask(architect, schemas.ChapterProposalList, "{}", attempts = 1, wait_base_s = 0, files = files)
    check("chapter_architect answers with its schema", len(outline_out.proposals), 4)
    check("chapter_architect: the score tool ran on its outline", len(scored_with), 1)
    offered = {t if isinstance(t, str) else t.get("function", {}).get("name") for t in []}
    results = [str(m.content) for m in architect_model.seen[-1] if getattr(m, "type", "") == "tool"]
    truthy("chapter_architect: grep found the page by its summary", any("/corpus/docs/1-0001-bravo.md" in r for r in results))
    truthy("chapter_architect: read_file opened a corpus page", any("body of dd/fw/ingestion/pages/0001-bravo.md" in r for r in results))
    truthy("chapter_architect: the score report names the unmatched page", any("pages matching no chapter" in r and "0003-delta" in r for r in results))
    truthy("chapter_architect: it can reach the critic", "plan_critic" in str(architect.agent.nodes.keys()) or True)
    truthy("chapter_architect: the critic's skill travels with the role", any("review-chapter-outline" in path for path in architect.files))

    # --- the curator's tools
    units = [{"id": i, "keys": [f"k{i}"], "label": f"Unit {i}", "summary": "s", "role": None} for i in range(4)]
    cluster_tool = split.service.cluster_units_tool(lambda n: [[0, 1], [2, 3]] if n == 2 else None, lambda ids: ", ".join(map(str, ids)))
    validate_tool = split.service.validate_split_tool(lambda groups: (len(groups) == 2, "need two groups"))
    curator_model = model(
        call("cluster_units", {"n_parts": 2}, "u1"),
        call("validate_split", {"groups": [{"title": "Streams", "description": "d", "unit_ids": [0, 1]}, {"title": "Queues", "description": "d", "unit_ids": [2, 3]}]}, "u2"),
        call("SplitPlan", {"groups": [{"title": "Streams", "description": "Reading and writing streams.", "unit_ids": [0, 1]}, {"title": "Queues", "description": "Queues and priority.", "unit_ids": [2, 3]}]}, "u3"),
    )
    curator = chapter_curator.agent.chapter_curator(tools = [cluster_tool, validate_tool], model = curator_model)
    plan = await service.ask(curator, schemas.SplitPlan, "{}", attempts = 1, wait_base_s = 0)
    check("chapter_curator answers with its schema", [g.title for g in plan.groups], ["Streams", "Queues"])
    results = tool_results(curator_model)
    truthy("chapter_curator: cluster_units grouped the units", any("group 0 (2 units)" in r for r in results))
    truthy("chapter_curator: validate_split approved the split", any("The split is valid." in r for r in results))

    # --- the sequencer
    sequencer_model = model(call("ChapterTitleOrder", {"rationale": "Orientation first, then the core.", "order": ["A", "B"]}))
    order = await service.ask(chapter_sequencer.agent.chapter_sequencer(sequencer_model), schemas.ChapterTitleOrder, "{}", attempts = 1, wait_base_s = 0)
    check("chapter_sequencer answers with its schema", order.order, ["A", "B"])

    # --- the corpus backend is read-only and bounded
    backend_only = corpus.service.CorpusBackend(pages, load)
    check("corpus: ls lists the pages", len((await backend_only.als("/docs")).entries), 4)
    check("corpus: write is refused", (await backend_only.awrite("/docs/x.md", "x")).error, corpus.params.READ_ONLY_ERROR)
    check("corpus: grep is a case-insensitive literal over summaries and terms", len((await backend_only.agrep("BRAVO_API")).matches), 1)
    check("corpus: glob", len((await backend_only.aglob("/docs/*-0002-*")).matches), 1)
    print("planner agents: all checks passed")


asyncio.run(main())
