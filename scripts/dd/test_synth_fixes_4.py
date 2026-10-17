"""Offline checks for the 2026-10-16 synth batch after asyncio ch-04 (no LLM, no cluster): copies of a printed block that
restate it are dropped (not kept as prose echoes), prose subtopics repeating another are dropped, the source top-up covers the
key facts a CITED source leaves unsaid, and a single-chapter run persists its wall time.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_4.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

SW = domains.dd.synth.nodes.sawc.domain
SS = domains.dd.synth.nodes.sawc.schemas
P = domains.dd.synth.nodes.sawc.params
DSP = domains.dd.synth.runtime.dispatch.service

H1, H2, H3, H4 = "1" * 16, "2" * 16, "3" * 16, "4" * 16


def st(sub, expl, h=""):
    return SS.Subtopic(code_ref_hash=h, subheading=sub, explanation=expl)


def sec(sid, subs, cites=()):
    return SS.Section(section_id=sid, heading=f"Heading {sid}", intro="An intro sentence that is long enough to pass.",
                      subtopics=list(subs), citations=[SS.Citation(source_key=k, claim="a claim that is long enough") for k in cites])


SHELL = ("This snippet uses asyncio.create_subprocess_shell to launch the command 'ls /zzz', routing stdout and stderr to "
         "asyncio.subprocess.PIPE. It then awaits communicate() and prints the decoded output and the return code.")
SHELL_COPY = ("The snippet uses asyncio.create_subprocess_shell to launch 'ls /zzz', routing stdout and stderr to "
              "asyncio.subprocess.PIPE. It then awaits communicate() and decodes both streams.")
QUEUE = "A bounded asyncio.Queue blocks put() when full, which gives producers back-pressure and keeps memory flat."
bodies = {H1: "```python\nproc = await asyncio.create_subprocess_shell('ls /zzz', stdout=asyncio.subprocess.PIPE)\n```"}

# 1) a copy that restates the block's winner is dropped even at the floor; a copy about something else stays as prose ---------------------
s1 = sec("s1", [st("Shell Command with PIPE Streams", SHELL, H1), st("Exec capture", "Another snippet runs asyncio.create_subprocess_exec and then reads its output from the pipe.", H2),
                st("Parallel", "asyncio.gather runs two coroutines that each spawn subprocesses at once and waits for both.", H3)])
s2 = sec("s2", [st("Shell subprocess with communicate", SHELL_COPY, H1), st("Reading a line", "The exec snippet reads one single line from the pipe via readline() and prints it.", H4),
                st("Two more", "Yet another distinct sentence about Process.wait and the return codes it reports.", "")])
out, n = SW.drop_repeated_code_blocks([s1, s2], bodies)
assert n == 1 and [x.subheading for x in out[1].subtopics] == ["Reading a line", "Two more"], [x.subheading for x in out[1].subtopics]
assert [x.code_ref_hash for x in out[0].subtopics] == [H1, H2, H3], "the winner keeps its block"
s2b = sec("s2", [st("Bounded Queues for Back-Pressure", QUEUE, H1), st("Reading a line", "The exec snippet reads one single line from the pipe via readline() and prints it.", H4),
                 st("Two more", "Yet another distinct sentence about Process.wait and the return codes it reports.", "")])
out, n = SW.drop_repeated_code_blocks([s1, s2b], bodies)                       # asyncio ch-03: a different topic keeps its prose (hash stripped)
assert n == 1 and [(x.subheading, x.code_ref_hash) for x in out[1].subtopics][0] == ("Bounded Queues for Back-Pressure", ""), out[1].subtopics
only = sec("s2", [st("Shell subprocess with communicate", SHELL_COPY, H1)])
out, n = SW.drop_repeated_code_blocks([s1, only], bodies)                      # a section never loses its last subtopic
assert len(out[1].subtopics) == 1 and out[1].subtopics[0].code_ref_hash == "", out[1].subtopics
print("1 restating copies dropped, different-topic copies kept as prose ok")

# 2) a prose subtopic repeating another is dropped; distinct neighbours and code subtopics stay ------------------------------------------------
SHELL_NEAR = SHELL.replace("This snippet", "The snippet").replace("the command ", "")                    # near-verbatim restatement (0.95)
a = sec("s1", [st("Shell Command", SHELL, H1), st("Distinct", "Process.kill and terminate stop the child process with different signals on Windows hosts."),
               st("Shell restated", SHELL_NEAR)])
b = sec("s2", [st("Bounded Queues", QUEUE), st("Queues again", QUEUE + " Consumers drain it.")])
out, n = SW.drop_prose_echoes([a, b])
assert n == 2 and [x.subheading for x in out[0].subtopics] == ["Shell Command", "Distinct"] and [x.subheading for x in out[1].subtopics] == ["Bounded Queues"], [[x.subheading for x in s.subtopics] for s in out]
assert SW.drop_prose_echoes([sec("s1", [st("Only", SHELL), st("Only copy", SHELL)])])[1] == 1 and SW.drop_prose_echoes([sec("s1", [st("One", SHELL)])])[1] == 0
distinct = sec("s1", [st("Windows Selector Event Loop Policy", "WindowsSelectorEventLoopPolicy is the policy using the select-based event loop on Windows platforms."),
                      st("Windows Proactor Event Loop Policy", "WindowsProactorEventLoopPolicy is the policy using the IOCP-based event loop on Windows platforms.")])
assert SW.drop_prose_echoes([distinct])[1] == 0                                                  # one-word-apart reference entries (0.64) both stay
assert SW.drop_prose_echoes([sec("s1", [st("Shell Command", SHELL), st("Paraphrase", SHELL_COPY)])])[1] == 0     # a paraphrase (0.64) is not a copy
print("2 prose echoes dropped, distinct subtopics untouched ok")

# 3) cited sources with unstated facts go to the source top-up with just those facts -----------------------------------------------------------------
exec_facts = ["It accepts program, *args, stdin, stdout, stderr, limit (default 65536), and **kwds parameters.",
              "If the Process object is garbage collected while the process is still running, the child process will be killed.",
              "The loop parameter was removed in Python 3.10.", "asyncio.create_subprocess_exec is an async function that creates a subprocess."]
idx = {"s1": [{"source_key": "k_exec", "relevance": "primary", "summary": "exec reference", "key_facts": exec_facts, "code_refs": []},
              {"source_key": "k_one", "relevance": "primary", "summary": "one gap", "key_facts": ["The send_signal method delivers a signal to the child process.", "Processes are created by create_subprocess_exec calls."], "code_refs": []},
              {"source_key": "k_new", "relevance": "supporting", "summary": "never cited", "key_facts": ["Process.wait returns the exit code once the child ends."], "code_refs": []}]}
chapter = [sec("s1", [st("Spawning", "create_subprocess_exec is an async function that creates a subprocess and returns a Process; processes are created by create_subprocess_exec calls."),
                      st("Signals", "Nothing about this topic is said in these particular words today.")], cites=["k_exec", "k_one"])]
tok = SW.chapter_prose_tokens(chapter)
gaps = SW.source_gaps(idx, chapter, tok)
got = {k: facts for _, l in gaps.items() for k, _, facts in l}
assert set(got) == {"k_exec", "k_new"}, got                                     # k_one has a single unstated fact (< TOPUP_FACT_MIN_GAP)
assert got["k_exec"] == exec_facts[:3] and got["k_new"] == ["Process.wait returns the exit code once the child ends."], got
assert {k for _, l in SW.uncited_routed_sources(idx, chapter).items() for k, _, _ in l} == {"k_new"}           # citation-only view unchanged
assert SW.source_gaps(idx, chapter) == SW.uncited_routed_sources(idx, chapter)
prompt = SW.build_source_topup_prompt(framework="asyncio", chapter_title="Subprocess", section_heading="Spawning", section_description="d",
                                      existing_subheadings=["Spawning"], sources=gaps["s1"], max_new=4)
assert "DOES NOT STATE YET" in prompt and "limit (default 65536)" in prompt and "garbage collected" in prompt
print("3 fact gaps of cited sources reach the source top-up ok")

# 4) a finished single chapter merges its wall time into the study timing blob -------------------------------------------------------------------------
class FakeStore:
    def __init__(self, blob=None):
        self.blob = blob
        self.written = None

    async def read_text(self, key):
        if self.blob is None:
            raise FileNotFoundError(key)
        return self.blob

    async def write(self, key, data, content_type=None):
        self.written = json.loads(data)


def persist(blob):
    store = FakeStore(blob)
    orig = domains.dd.ingestion.storage.service.get_storage
    domains.dd.ingestion.storage.service.get_storage = lambda: store
    try:
        asyncio.run(DSP._persist_chapter_timing("asyncio", "ch-04", 2040000))
    finally:
        domains.dd.ingestion.storage.service.get_storage = orig
    return store.written


w = persist(None)
assert w["per_chapter_ms"] == {"ch-04": 2040000} and w["total_wall_ms"] == 2040000 and w["harmonize_ms"] == 0, w
prior = json.dumps({"slug": "asyncio", "per_chapter_ms": {"ch-01": 600000, "ch-04": 100}, "harmonize_ms": 5000, "session_wall_ms": 9999, "total_wall_ms": 605100})
w = persist(prior)
assert w["per_chapter_ms"] == {"ch-01": 600000, "ch-04": 2040000} and w["total_wall_ms"] == 600000 + 2040000 + 5000 and w["harmonize_ms"] == 5000 and w["session_wall_ms"] == 9999, w
print("4 single-chapter wall time merged ok")

# 5) define before use: a block calling run() follows the block that defines it; a helper only an UNUSED block defines is REQUIRED ---------------------------
RUN_DEF = "```python\nimport asyncio\n\nasync def run(cmd):\n    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)\n    out, _ = await proc.communicate()\n    print(out)\n\nasyncio.run(run('ls /zzz'))\n```"
GATHER = "```python\nasync def main():\n    await asyncio.gather(run('ls /zzz'), run('sleep 1; echo hi'))\n\nasyncio.run(main())\n```"
DATE = "```python\nimport asyncio, sys\n\nasync def get_date():\n    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', 'print(1)')\n    await proc.wait()\n\nasyncio.run(get_date())\n```"
vr = {H1: {"fence_text": DATE}, H2: {"fence_text": RUN_DEF}, H3: {"fence_text": GATHER}}
assert SW.code_dependencies(RUN_DEF) == ({"run"}, set()), SW.code_dependencies(RUN_DEF)
assert SW.code_dependencies(GATHER) == ({"main"}, {"run"}), SW.code_dependencies(GATHER)
assert SW.code_dependencies(DATE) == ({"get_date"}, set()) and SW.code_dependencies("```python\nthis is ( not python\n```") == (set(), set())
ordered, n = SW.order_by_code_dependencies([sec("s1", [st("Date", "The get_date coroutine reads the interpreter output from a pipe.", H1),
                                                        st("Gather", "asyncio.gather runs two run() calls concurrently and waits for both.", H3),
                                                        st("Shell", "The run helper wraps create_subprocess_shell and then awaits communicate() for output.", H2)])], vr)
assert n == 1 and [x.subheading for x in ordered[0].subtopics] == ["Date", "Shell", "Gather"], [x.subheading for x in ordered[0].subtopics]
same, n0 = SW.order_by_code_dependencies([sec("s1", [st("Shell", "The run helper wraps create_subprocess_shell and then awaits communicate() for output.", H2),
                                                      st("Gather", "asyncio.gather runs two run() calls concurrently and waits for both.", H3),
                                                      st("Date", "The get_date coroutine reads the interpreter output from a pipe.", H1)])], vr)
assert n0 == 0 and [x.subheading for x in same[0].subtopics] == ["Shell", "Gather", "Date"], "already define-before-use: untouched"
printed = [sec("s1", [st("Date", "The get_date coroutine reads the interpreter output from a pipe.", H1), st("Gather", "asyncio.gather runs two run() calls concurrently and waits for both.", H3), st("Plain", "A prose subtopic that stands alone in this section here.")])]
assert SW.dependency_blocks(printed, {"s1": [H2]}, vr) == {"s1": [H2]}                       # the unused block defines run(), which the printed gather calls
assert SW.dependency_blocks(printed, {"s1": [H4]}, {**vr, H4: {"fence_text": DATE.replace("get_date", "other")}}) == {}
nogather = [sec("s1", [st("Date", "The get_date coroutine reads the interpreter output from a pipe.", H1), st("Plain", "A prose subtopic that stands alone in this section here.")])]
assert SW.dependency_blocks(nogather, {"s1": [H2, H3]}, vr) == {"s1": [H2]}                   # the gather block is still unused, but it will call run(): the shell block is required
assert SW.dependency_blocks(nogather, {"s1": [H2]}, vr) == {}                                 # nothing calls it
assert SW.dependency_blocks(nogather, {"s1": [H3]}, vr) == {}                                 # a caller alone requires nothing
assert SW.order_by_code_dependencies(printed, None)[1] == 0
req_prompt = SW.build_topup_prompt(framework="asyncio", chapter_title="c", section_heading="h", section_description="d", existing_subheadings=[],
                                   blocks=[(H2, RUN_DEF)], facts=[], sources=[], max_new=2, required={H2})
assert "REQUIRED" in req_prompt and f"[{H2}] (REQUIRED" in req_prompt
assert "(REQUIRED" not in SW.build_topup_prompt(framework="asyncio", chapter_title="c", section_heading="h", section_description="d", existing_subheadings=[],
                                               blocks=[(H2, RUN_DEF)], facts=[], sources=[], max_new=2)
print("5 define-before-use ordering and required blocks ok")

# 6) "the documentation provides ..." filler is dropped from the source top-up -------------------------------------------------------------------------
src6 = [("k1", "exec reference", ["asyncio.create_subprocess_exec accepts a limit argument for the StreamReader buffers."])]
good = {"subtopics": [{"subheading": "The limit argument", "explanation": "asyncio.create_subprocess_exec accepts a limit argument that sets the buffer size of the StreamReader wrappers around stdout and stderr."}], "citations": []}
filler = {"subtopics": [{"subheading": "Subprocess examples", "explanation": "The documentation provides examples of how to use the asyncio create_subprocess_exec limit argument with StreamReader buffers for subprocesses."}], "citations": []}
assert len(SW.parse_source_topup(good, sources=src6, existing_subheads=set(), max_new=2)[0]) == 1
assert SW.parse_source_topup(filler, sources=src6, existing_subheads=set(), max_new=2)[0] == []
assert SW.META_FRAMING_RE.search("This page describes the limit argument") and not SW.META_FRAMING_RE.search("The loop parameter was removed in Python 3.10 and the page below shows")
print("6 filler dropped ok")

# 7) a top-up call that fails, or answers nothing valid, is asked again --------------------------------------------------------------------------------
SV = domains.dd.synth.nodes.sawc.service
chat = domains.settings.chat.service


def run_topup(answers):
    calls = []

    async def fake(prompt, **kw):
        calls.append(prompt)
        a = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(a, Exception):
            raise a
        return json.dumps(a), {}

    orig = chat.chat_text_async
    chat.chat_text_async = fake
    try:
        sections = [sec("s1", [st("Date", "The get_date coroutine reads the interpreter output from a pipe.", H1), st("Gather", "asyncio.gather runs two run() calls concurrently and waits for both.", H3), st("Plain", "A prose subtopic that stands alone in this section here.")])]
        idx = {"s1": [{"section_id": "s1", "source_key": "k1", "relevance": "primary", "summary": "s", "key_facts": ["a fact"], "code_refs": [H1, H2, H3]}]}
        out = asyncio.run(SV._topup_coverage(final_sections=sections, per_section_index=idx, vault_rich=vr, sections_by_id={"s1": {"description": "d"}},
                                             framework="asyncio", chapter_id="ch-04", chapter_title="Subprocess"))
    finally:
        chat.chat_text_async = orig
    return out, len(calls)


valid = {"subtopics": [{"code_ref_hash": H2, "subheading": "The run helper", "explanation": "The run coroutine wraps create_subprocess_shell and awaits communicate() to collect the output of the command."}], "citations": []}
(secs7, n7, _), calls7 = run_topup([RuntimeError("504"), valid])
assert calls7 == 2 and n7 == 1 and secs7[0].subtopics[-1].code_ref_hash == H2, (calls7, n7)         # one unused block is enough when it is REQUIRED
(secs7, n7, _), calls7 = run_topup([{"subtopics": [], "citations": []}, valid])
assert calls7 == 2 and n7 == 1
(secs7, n7, _), calls7 = run_topup([valid])
assert calls7 == 1 and n7 == 1
(secs7, n7, _), calls7 = run_topup([RuntimeError("504"), RuntimeError("504"), valid])
assert calls7 == P.TOPUP_ATTEMPTS + 1 == 3 and n7 == 1                                               # every call raised: one salvage call recovers it
(secs7, n7, _), calls7 = run_topup([RuntimeError("504")] * 4 + [valid])
assert calls7 == 2 * P.TOPUP_ATTEMPTS == 4 and n7 == 0                                               # bounded: fail-soft after the salvage attempts
(secs7, n7, _), calls7 = run_topup([{"subtopics": [], "citations": []}] * 4 + [valid])
assert calls7 == P.TOPUP_ATTEMPTS == 2 and n7 == 0                                                   # answered but nothing valid: not salvaged
print("7 top-up retried, bounded ok")

# 8) a judge reply whose verdicts are JSON strings is accepted --------------------------------------------------------------------------------------
CKS = domains.dd.synth.nodes.checklist.schemas
names = ["chapter_reads_coherently", "claims_grounded_in_sources", "terminology_consistent", "prose_code_first_not_meta_framing", "code_refs_introduced_in_prose"]
wrapped = {n: '{"passed": true, "feedback": ""}' for n in names}
wrapped["terminology_consistent"] = '{"passed": false, "feedback": "Mixes Process and process object across the section."}'
pl = CKS.LLMJudgePayload.model_validate(wrapped)
assert pl.chapter_reads_coherently.passed and not pl.terminology_consistent.passed and "Mixes" in pl.terminology_consistent.feedback
assert CKS.LLMJudgePayload.model_validate({n: {"passed": True, "feedback": ""} for n in names}).code_refs_introduced_in_prose.passed
try:
    CKS.LLMJudgePayload.model_validate({**wrapped, "claims_grounded_in_sources": "not json"})
    raise SystemExit("a non-JSON string verdict must still be rejected")
except ValueError:
    pass
print("8 stringified judge verdicts accepted ok")

# 9) a digest contribution with too many / out-of-bounds key facts is trimmed, not rejected ----------------------------------------------------------
DGD = domains.dd.synth.nodes.digest.domain
DGP = domains.dd.synth.nodes.digest.params
def payload(facts):
    return {"source_title": "MCP JSON configuration", "overall_summary": "How the mcp.json configuration file is structured and generated for the supported clients.",
            "contributes_to": [{"section_id": "s1", "source_key": "k", "relevance": "primary", "summary": "The page shows the mcp.json shape used by clients.", "key_facts": facts, "code_refs": []}],
            "unassigned_code_refs": []}
six = [f"fact number {i} about the configuration file format" for i in range(6)]
pl, err = DGD.try_parse_payload(payload(six))
assert pl is not None and err is None and pl.contributes_to[0].key_facts == six[:DGP.MAX_KEY_FACTS_PER_CONTRIB], err
messy = ["tiny", "a duplicated fact about the config file", "a duplicated fact about the config file", "x" * 20 + " " + "word " * 80]
pl, err = DGD.try_parse_payload(payload(messy))
kf = pl.contributes_to[0].key_facts
assert err is None and len(kf) == 2 and kf[0] == "a duplicated fact about the config file" and len(kf[1]) <= DGP.KEY_FACT_MAX_CHARS, (err, kf)
good = payload(six[:3])
assert DGD.trim_key_facts(good) == good                                                    # valid input is unchanged
assert DGD.try_parse_payload(payload(["no", "x"]))[0] is None                                 # nothing usable left: still rejected for the repair loop
print("9 digest key facts trimmed ok")

# 10) the outline's section cap follows the chapter's code-block count ----------------------------------------------------------------------------------
OL = domains.dd.synth.nodes.outline
assert OL.params.max_h2_for_n_sources(18) == 4 and OL.params.max_h2_for_n_sources(18, 0) == 4         # unchanged without a block count
assert OL.params.max_h2_for_n_sources(18, 135) == 12                                                    # fastmcp ch-01: 135 blocks -> 12 sections, not 4
assert OL.params.max_h2_for_n_sources(18, 30) == 4 and OL.params.max_h2_for_n_sources(23, 35) == 5     # asyncio ch-01 (23 sources, 35 blocks): the source cap still wins
assert OL.params.max_h2_for_n_sources(40, 60) == 10 and OL.params.max_h2_for_n_sources(0, 50) == 5
assert OL.params.max_h2_for_n_sources(4, 200) == OL.params.OUTLINE_BLOCK_CEILING == 12                  # never above the block ceiling
assert OL.domain.outline_optimal_stopping_min(18, 135) == 8 and OL.domain.outline_optimal_stopping_min(18) == 5
secs10 = [OL.schemas.OutlineSection(section_id=f"s{i + 1}", heading=f"Install for client number {chr(65 + i)}", description="Installing the server for one specific client and its configuration file.", prerequisites=[]) for i in range(8)]
ol10 = OL.schemas.ChapterOutline(sections=secs10)
dag10 = OL.domain.derive_dag(ol10.sections)
assert any("adaptive cap is 4" in x for x in OL.domain.validate_outline_structure(ol10, dag10, n_sources=18)[1])
assert not any("adaptive cap" in x for x in OL.domain.validate_outline_structure(ol10, dag10, n_sources=18, n_blocks=135)[1])
print("10 outline cap follows block count ok")

# 11) the coverage top-up runs a second round for sections that gained subtopics, never re-asks a section whose calls failed ------------------------------
def run_rounds(answer_for, n_blocks=20, subs_per_section=3):
    hs = [f"{i:016x}" for i in range(n_blocks)]
    vr11 = {h: {"fence_text": f"```python\nresult_{i} = widget_{i}.compute_{i}(value_{i})\n```"} for i, h in enumerate(hs)}
    sections11 = [sec(f"s{k}", [st(f"Topic {k} {j}", f"widget_{k}{j} compute_{k}{j} explanation words here for {k} {j} only.") for j in range(subs_per_section)]) for k in (1, 2)]
    idx11 = {f"s{k}": [{"section_id": f"s{k}", "source_key": "k1", "relevance": "primary", "summary": "s", "key_facts": ["a fact"], "code_refs": hs[(k - 1) * 10: k * 10]}] for k in (1, 2)}
    calls = []

    async def fake(prompt, **kw):
        calls.append(prompt)
        sid = "s1" if "'Heading s1'" in prompt else "s2"
        return json.dumps(answer_for(sid, prompt, len(calls))), {}

    orig = chat.chat_text_async
    chat.chat_text_async = fake
    try:
        out = asyncio.run(SV._topup_coverage(final_sections=sections11, per_section_index=idx11, vault_rich=vr11, sections_by_id={}, framework="a", chapter_id="c", chapter_title="t"))
    finally:
        chat.chat_text_async = orig
    return out, calls, vr11


def answer(sid, prompt, n):
    import re as _re
    shown = _re.findall(r"^\[([0-9a-f]{16})\]", prompt, _re.M)[:4]
    return {"subtopics": [{"code_ref_hash": h, "subheading": f"New topic {sid} {h[-3:]}", "explanation": f"The widget_{int(h, 16)} object runs compute_{int(h, 16)} on value_{int(h, 16)} and returns result_{int(h, 16)} here."} for h in shown], "citations": []}


(out11, calls11, _), = [run_rounds(answer)[:3]]
assert len(calls11) == 4, len(calls11)                                                     # 2 sections x 2 rounds
assert [len(x.subtopics) for x in out11[0]] == [11, 11] and out11[1] == 16 and out11[2] == 2, (out11[0], out11[1], out11[2])   # 4 per call, 2 rounds
failing = lambda sid, prompt, n: (_ for _ in ()).throw(RuntimeError("504")) if sid == "s1" else answer(sid, prompt, n)
out11b, calls11b, _ = run_rounds(failing)
s1_calls = [c for c in calls11b if "'Heading s1'" in c]
assert len(s1_calls) == 2 * P.TOPUP_ATTEMPTS == 4, len(s1_calls)                         # s1 raised on both attempts: skipped in round 2, asked once more in the salvage pass
assert len(out11b[0][0].subtopics) == 3 and len(out11b[0][1].subtopics) > 3
print("11 coverage top-up rounds ok")
print("ALL OK")
