"""Offline checks for what tools each DD agent is shown: no leak between roles built in one process, no tool for the ingestion auditor,
and the repairer's `check_patch` reading the page from its state.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_agent_tools.py
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI

from ai.dd import agents, nodes, tools


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class ScriptedModel(ChatOpenAI):
    """A Rotator-shaped model that answers from a script and records the tool names each call was bound to."""

    script: list = []
    seen: list = []

    def bind_tools(self, tools, **kwargs):
        self.seen.append(sorted(convert_to_openai_tool(tool)["function"]["name"] for tool in tools))
        return self

    async def _agenerate(self, messages, stop = None, run_manager = None, **kwargs):
        message = self.script.pop(0) if self.script else AIMessage(content = "{}")
        return ChatResult(generations = [ChatGeneration(message = message)])


def model(*script) -> ScriptedModel:
    return ScriptedModel(model = "fgts-va", api_key = "unused", base_url = "http://127.0.0.1:9/v1", script = list(script), seen = [])


def call(name: str, args: dict, call_id: str = "1") -> AIMessage:
    return AIMessage(content = "", tool_calls = [{"name": name, "args": args, "id": call_id}])


async def ask(role, scripted, request = None):
    return await role.ainvoke(
        request or {"messages": [{"role": "user", "content": "go"}]},
        config = {"configurable": {"thread_id": "t"}},
    )


# --- a role keeps its own tools however many stricter roles were built before it in the same process
model_config = agents.entities.ModelConfig(max_output_tokens = 512, timeout_s = 10.0)


def role(name: str, allowed: frozenset[str], scripted: ScriptedModel):
    return agents.service.build(
        name, system_prompt = "x", config = model_config, rotator_task = "t", allowed_tools = allowed,
        read_paths = ["/corpus/**"], model = scripted,
        subagents = [agents.service.subagent("critic", description = "Reviews.", system_prompt = "x")] if "task" in allowed else (),
    )


strict_model = model()
strict = role("strict", frozenset({"read_file"}), strict_model)
wide_model = model()
wide = role("wide", frozenset({"read_file", "grep", "glob", "ls", "task"}), wide_model)
for scripted, built in ((strict_model, strict), (wide_model, wide)):
    try:
        asyncio.run(ask(built.agent, scripted))
    except Exception:
        pass
check("a strict role is shown only its own stack tools", strict_model.seen[0], ["read_file"])
check("a wide role built after it still gets its tools", wide_model.seen[0], ["glob", "grep", "ls", "read_file", "task"])

from deepagents.profiles.harness import harness_profiles
check("the shared model profile excludes no tool", sorted(harness_profiles._get_harness_profile("openai:fgts-va").excluded_tools), [])

# --- another piece of code registered an exclusion under the same key: building a role clears it
from deepagents import HarnessProfile, register_harness_profile
register_harness_profile("openai:fgts-va", HarnessProfile(excluded_tools = frozenset({"grep", "task"})))
late_model = model()
late = role("late", frozenset({"read_file", "grep"}), late_model)
try:
    asyncio.run(ask(late.agent, late_model))
except Exception:
    pass
check("an exclusion registered elsewhere does not reach a role", late_model.seen[0], ["grep", "read_file"])

# --- the ingestion auditor is shown no tool but its answer
auditor_model = model(call("PageAssessment", {"page_kind": "documentation", "confidence": 0.9}))
auditor = agents.ingestion.page_auditor.agent.page_auditor(model = auditor_model)
result = asyncio.run(ask(auditor, auditor_model))
check("the auditor is shown only its answer tool", auditor_model.seen[0], ["PageAssessment"])
check("the auditor answers with its schema", agents.domain.structured_or_recovered(result, agents.schemas.PageAssessment) is not None, True)
check("the auditor prompt has the conventions rendered", "<<EXPORT_CONVENTIONS>>" in agents.ingestion.page_auditor.domain.system_prompt(), False)

# --- the repairer checks a patch against the page held in its state
PAGE = "# T\n\nA line.\n\nSee the guide.\n\nA line.\n"
sha = hashlib.sha256(PAGE.encode("utf-8")).hexdigest()
assessment = {"issues": [{"issue_id": "d", "category": "duplicate_boilerplate", "severity": "low", "description": "d",
                          "evidence": "A line.", "confidence": 0.95}]}
patch = {"issue_id": "d", "evidence": "A line.", "target_text": "A line.\n\nSee the guide.", "replacement": "",
         "confidence": 0.95, "context_before": "\n\n", "context_after": "\n\nA line.\n"}
repair_model = model(
    call("check_patch", {**patch, "target_text": "A line."}, "1"),
    call("check_patch", patch, "2"),
    call("PagePatchBatch", {"patches": [{**patch, "source_sha256": sha, "rationale": "r"}]}, "3"),
)
repairer = agents.ingestion.page_repair.agent.page_repair(check = nodes.ingestion.page_repair.domain.check_patch, model = repair_model)
request = {
    "messages": [{"role": "user", "content": "repair"}],
    "files": {
        tools.ingestion.check_patch.params.PAGE_PATH: agents.service.file(PAGE),
        tools.ingestion.check_patch.params.ASSESSMENT_PATH: agents.service.file(json.dumps(assessment)),
    },
}
result = asyncio.run(ask(repairer, repair_model, request))
check("the repairer is shown its check and its answer, nothing else", repair_model.seen[0], ["PagePatchBatch", "check_patch"])
replies = [message.content for message in result["messages"] if message.type == "tool" and message.name == "check_patch"]
check("the check reads the page from the state: a repeated target is refused, a unique one passes",
      (replies[0].startswith("REJECTED: target does not occur exactly once (it occurs 2 times)"), replies[1].startswith("OK:")), (True, True))
batch = agents.domain.structured_or_recovered(result, agents.schemas.PagePatchBatch)
body, report = nodes.ingestion.page_repair.domain.apply_patches(PAGE, sha, assessment, batch)
check("the answer the repairer ends with passes the final gate", (len(report["accepted"]), body), (1, "# T\n\n\n\nA line.\n"))

print("agent tools: all checks passed")
