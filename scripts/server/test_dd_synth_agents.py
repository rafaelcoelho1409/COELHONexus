"""Offline checks of the synth's DeepAgents: each role builds on the shared factory (harness profile, permissions, middleware, the unreviewed-tool guard),
reads its skill and its read-only files through its backend, calls its check tools, and answers with its schema.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_synth_agents.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

import ai
import domains
from ai.dd.agents import service
from ai.dd.agents.pipeline.synth import (
    chapter_judge, chapter_outliner, chapter_reviewer, claim_extractor, code_deriver, code_normalizer, coverage_extender, drift_detector,
    paragraph_patcher, schemas, section_writer, source_extender, source_router, term_canonicalizer,
)
from ai.dd.backends import files
from ai.dd.tools.pipeline.synth import check as check_tools, view


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def truthy(label: str, value) -> None:
    assert value, f"{label}: expected a truthy value, got {value!r}"
    print(f"ok  {label}")


class Scripted(ChatOpenAI):
    """A ChatOpenAI that answers from a script and records what it was sent."""

    script: list = []
    seen: list = []

    def _generate(self, messages, stop = None, run_manager = None, **kwargs):
        self.seen.append(messages)
        return ChatResult(generations = [ChatGeneration(message = self.script.pop(0))])

    async def _agenerate(self, messages, stop = None, run_manager = None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


def model(*script: AIMessage) -> Scripted:
    scripted = Scripted(base_url = "http://rotator.test/v1", api_key = "key", model = "fake-model")
    scripted.script = list(script)
    scripted.seen = []
    return scripted


def call(name: str, args: dict, id_: str = "c1") -> AIMessage:
    return AIMessage(content = "", tool_calls = [{"name": name, "args": args, "id": id_}])


def tool_texts(scripted: Scripted, turn: int) -> list[str]:
    """The tool messages the model was sent on its `turn`-th request."""
    return [str(m.content) for m in scripted.seen[turn] if getattr(m, "type", "") == "tool"]


async def ask(role, schema, scripted, request = "{}"):
    return await service.ask(role, schema, request, attempts = 1, wait_base_s = 0)


async def main() -> None:
    import yaml

    # --- every synth skill parses: valid YAML front matter, `name` equal to its folder, a description within the spec
    for path in sorted((Path(ai.dd.skills.params.LOCAL_ROOT) / "pipeline" / "synth").rglob("SKILL.md")):
        meta = yaml.safe_load(path.read_text(encoding = "utf-8").split("---")[1])
        check(f"skill {path.parent.name}: name is its folder", meta["name"], path.parent.name)
        truthy(f"skill {path.parent.name}: description within 1024 characters", 0 < len(meta["description"]) <= 1024)

    # --- the read-only files backend
    mounted = files.service.mounted({"/1-alpha.md": "alpha client\nretry_policy here\n", "/2-bravo.md": "bravo server\n"}, "/sources/")
    backend = files.service.FilesBackend({"/1-alpha.md": "alpha client\nretry_policy here\n", "/2-bravo.md": "bravo server\n"}, route = "/sources/")
    check("files: ls lists every file", [e["path"] for e in (await backend.als("/")).entries], ["/1-alpha.md", "/2-bravo.md"])
    check("files: grep is a case-insensitive literal over lines", [m["path"] for m in (await backend.agrep("RETRY_POLICY")).matches], ["/1-alpha.md"])
    check("files: glob", [m["path"] for m in (await backend.aglob("*bravo*")).matches], ["/2-bravo.md"])
    truthy("files: read returns the text", "retry_policy" in (await backend.aread("/sources/1-alpha.md")).file_data["content"])
    truthy("files: a missing file is an error", (await backend.aread("/nope.md")).error)
    truthy("files: write is refused", (await backend.awrite("/x.md", "x")).error)
    truthy("files: edit is refused", (await backend.aedit("/1-alpha.md", "alpha", "beta")).error)
    truthy("files: the composite backend mounts the route", mounted is not None)

    # --- single-shot roles: one structured answer, no tools
    for name, factory, schema, args in (
        ("chapter_judge", chapter_judge.agent.chapter_judge, schemas.JudgeAnswer, {k: {"passed": True} for k in schemas.JudgeAnswer.model_fields}),
        ("coverage_extender", coverage_extender.agent.coverage_extender, schemas.ExtensionAnswer, {"subtopics": [], "citations": []}),
        ("source_extender", source_extender.agent.source_extender, schemas.SourceExtensionAnswer, {"subtopics": [], "citations": []}),
        ("claim_extractor", claim_extractor.agent.claim_extractor, schemas.ClaimsAnswer, {"claims": ["a claim"], "terms": []}),
        ("term_canonicalizer", term_canonicalizer.agent.term_canonicalizer, schemas.CanonicalAnswer, {"canonical_terms": [], "rationale": "none"}),
        ("paragraph_patcher", paragraph_patcher.agent.paragraph_patcher, schemas.ParagraphAnswer, {"paragraph": "Edited."}),
    ):
        scripted = model(call(schema.__name__, args))
        out = await ask(factory(scripted), schema, scripted)
        truthy(f"{name} answers with its schema", out is not None)

    # --- the source router checks its digest with a tool, fixes what it reports and answers
    def validator(candidate: dict) -> list[str]:
        return [] if candidate["contributes_to"] and candidate["contributes_to"][0]["section_id"] == "s1" else ["section_id 'sX' is not in the outline"]

    async def validate_digest(candidate: dict) -> list[str]:
        return validator(candidate)

    contribution = {"section_id": "sX", "relevance": "primary", "summary": "x" * 30, "key_facts": ["a fact about it"], "code_refs": []}
    digest = {"source_title": "Title", "overall_summary": "y" * 40, "unassigned_code_refs": []}
    router_model = model(
        call("check_digest", {**digest, "contributes_to": [contribution]}, "d1"),
        call("check_digest", {**digest, "contributes_to": [{**contribution, "section_id": "s1"}]}, "d2"),
        call("DigestAnswer", {**digest, "contributes_to": [{**contribution, "section_id": "s1"}]}, "d3"),
    )
    router = source_router.agent.source_router(tools = [check_tools.service.check_digest_tool(validate_digest)], model = router_model)
    routed = await ask(router, schemas.DigestAnswer, router_model)
    check("source_router: answers with its schema", routed.contributes_to[0].section_id, "s1")
    truthy("source_router: the tool reported the bad section", any("not in the outline" in t and "1 issue(s)" in t for t in tool_texts(router_model, 1)))
    truthy("source_router: the tool then said it was valid", any(t.startswith("OK") for t in tool_texts(router_model, 2)))

    # --- the section writer reads a block in full from /vault/, checks its draft, and cannot read outside it
    async def check_section(candidate: dict) -> list[str]:
        return [] if candidate["heading"] == "Alpha Client" else ["`heading` must be exactly 'Alpha Client'"]

    section = {"intro": "i" * 40, "subtopics": [{"code_ref_hash": "a" * 16, "subheading": "Using AlphaClient", "explanation": "This shows how AlphaClient is called with a url and the result."}] * 1, "citations": []}
    writer_model = model(
        call("read_file", {"file_path": "/vault/" + "a" * 16 + ".md"}, "w1"),
        call("read_file", {"file_path": "/etc/passwd"}, "w2"),
        call("check_section", {**section, "heading": "Client"}, "w3"),
        call("check_section", {**section, "heading": "Alpha Client"}, "w4"),
        call("SectionAnswer", {**section, "heading": "Alpha Client"}, "w5"),
    )
    backend_w = files.service.mounted({"/" + "a" * 16 + ".md": "```python\nclient = AlphaClient(url='x')\n```"}, "/vault/")
    writer = section_writer.agent.section_writer(backend = backend_w, tools = [check_tools.service.check_section_tool(check_section)], model = writer_model)
    written = await ask(writer, schemas.SectionAnswer, writer_model)
    check("section_writer: answers with its schema", written.heading, "Alpha Client")
    truthy("section_writer: it read the block in full", any("AlphaClient(url='x')" in t for t in tool_texts(writer_model, 1)))
    truthy("section_writer: a path outside /vault/ is refused", any("permission" in t.lower() for t in tool_texts(writer_model, 2)))
    truthy("section_writer: the validator's issue reached it", any("exactly 'Alpha Client'" in t for t in tool_texts(writer_model, 3)))

    # --- the outliner: its skill, a source page, the validation tool
    skill = "/skills/pipeline/synth/chapter-outliner/design-chapter-outline/SKILL.md"

    async def validate_outline(candidate: dict) -> list[str]:
        return []

    section_a = {"section_id": "s1", "heading": "Alpha Client", "description": "d" * 30, "prerequisites": [], "needs_code": True}
    outliner_model = model(
        call("read_file", {"file_path": skill}, "o1"),
        call("read_file", {"file_path": "/sources/1-alpha.md"}, "o2"),
        call("validate_outline", {"sections": [section_a]}, "o3"),
        call("OutlineAnswer", {"sections": [section_a]}, "o4"),
    )
    outliner = chapter_outliner.agent.chapter_outliner(
        backend = files.service.mounted({"/1-alpha.md": "# Alpha\n\nAlphaClient docs\n"}, "/sources/"),
        tools = [check_tools.service.validate_outline_tool(validate_outline)], model = outliner_model,
    )
    truthy("chapter_outliner: its skill files travel with the role", skill in outliner.files)
    outlined = await ask(outliner, schemas.OutlineAnswer, outliner_model)
    check("chapter_outliner: answers with its schema", outlined.sections[0].heading, "Alpha Client")
    truthy("chapter_outliner: it read its SKILL.md", any("Design a chapter outline" in t for t in tool_texts(outliner_model, 1)))
    truthy("chapter_outliner: it read a source page", any("AlphaClient docs" in t for t in tool_texts(outliner_model, 2)))
    truthy("chapter_outliner: the validation tool said the outline was valid", any(t.startswith("OK") for t in tool_texts(outliner_model, 3)))

    # --- the reviewer reads a failing section before it decides
    reviewer_model = model(
        call("read_file", {"file_path": "/skills/pipeline/synth/chapter-reviewer/review-failed-chapter/SKILL.md"}, "r1"),
        call("inspect_section", {"section_id": "s1"}, "r2"),
        call("inspect_section", {"section_id": "s9"}, "r3"),
        call("RevisionAnswer", {"halt": False, "confidence": 0.8, "rationale": "s1 is filler", "revisions": [{"section_id": "s1", "instruction": "Open with the API."}]}, "r4"),
    )
    reviewer = chapter_reviewer.agent.chapter_reviewer(tools = [view.service.inspect_section_tool(lambda sid: "## Client\nfiller text" if sid == "s1" else None)], model = reviewer_model)
    revised = await ask(reviewer, schemas.RevisionAnswer, reviewer_model)
    check("chapter_reviewer: answers with its schema", revised.revisions[0].section_id, "s1")
    truthy("chapter_reviewer: it read the section", any("filler text" in t for t in tool_texts(reviewer_model, 2)))
    truthy("chapter_reviewer: an unknown section is reported, not invented", any("No section" in t for t in tool_texts(reviewer_model, 3)))

    # --- the drift detector greps the sibling chapters before reporting
    detector_model = model(
        call("grep", {"pattern": "ten seconds"}, "g1"),
        call("write_file", {"file_path": "/chapters/ch-01.md", "content": "x"}, "g2"),
        call("ViolationsAnswer", {"has_violations": False, "violations": [], "summary": "verified"}, "g3"),
    )
    detector = drift_detector.agent.drift_detector(
        backend = files.service.mounted({"/ch-01.md": "The timeout is ten seconds.\n", "/ch-02.md": "other\n"}, "/chapters/"), model = detector_model,
    )
    audited = await ask(detector, schemas.ViolationsAnswer, detector_model)
    check("drift_detector: answers with its schema", audited.has_violations, False)
    truthy("drift_detector: grep found the sibling chapter", any("/ch-01.md" in t for t in tool_texts(detector_model, 1)))
    truthy("drift_detector: it cannot write", any("permission" in t.lower() or "read-only" in t.lower() for t in tool_texts(detector_model, 2)))

    # --- code roles check their code with a parser tool
    from ai.dd.nodes.pipeline.synth import render, sawc_derive

    async def check_code(candidate: dict) -> list[str]:
        ok, error = render.domain.python_ast_valid(candidate["code"])
        return [] if ok else [f"does not parse: {error}"]

    deriver_model = model(
        call("check_code", {"code": "def broken(:"}, "c1"),
        call("check_code", {"code": "import alpha\nprint(alpha)\n"}, "c2"),
        call("DerivedCodeAnswer", {"code": "import alpha\nprint(alpha)\n", "explanation": "Imports alpha and prints it."}, "c3"),
    )
    deriver = code_deriver.agent.code_deriver(tools = [check_tools.service.check_code_tool(check_code)], model = deriver_model)
    derived = await ask(deriver, schemas.DerivedCodeAnswer, deriver_model)
    check("code_deriver: answers with its schema", derived.code.startswith("import alpha"), True)
    truthy("code_deriver: the parser tool rejected broken code", any("does not parse" in t for t in tool_texts(deriver_model, 1)))
    check("code_deriver: the derive gate is the same function the node uses", sawc_derive.domain.python_ast_valid("import alpha\nprint(alpha)\n"), True)

    normalizer_model = model(call("NormalizedCodeAnswer", {"code": "x = 1\n"}))
    normalizer = code_normalizer.agent.code_normalizer(tools = [check_tools.service.check_code_tool(check_code)], model = normalizer_model)
    check("code_normalizer answers with its schema", (await ask(normalizer, schemas.NormalizedCodeAnswer, normalizer_model)).code, "x = 1\n")

    # --- the check tools' own report
    check("tools: no issues is one line", check_tools.service.report([]), "OK: no issues. The draft is valid; answer now.")
    many = check_tools.service.report([f"issue {i}" for i in range(20)])
    truthy("tools: a long list is cut and counted", many.startswith("20 issue(s) to fix:") and many.endswith("... and 8 more"))
    print("\nsynth agents: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
