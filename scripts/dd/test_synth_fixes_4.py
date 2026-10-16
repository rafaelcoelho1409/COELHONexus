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
print("ALL OK")
