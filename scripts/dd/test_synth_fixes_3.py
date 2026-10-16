"""Offline checks for the 2026-10-16 synth batch (no LLM, no cluster): list trimming and the capacity-aware coverage gate.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_3.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

N = domains.dd.synth.nodes
SW, CK = N.sawc.domain, N.checklist.domain


def draft(n_sub, cites):
    return {"heading": "H", "intro": "A one sentence intro that names the section's core APIs and is long enough.",
            "subtopics": [{"code_ref_hash": "", "subheading": f"Topic {i}", "explanation": "word " * 10} for i in range(n_sub)],
            "citations": [{"source_key": k, "claim": "a claim that is long enough"} for k in cites]}


# 1) a draft over the citation cap is trimmed (duplicate sources collapse first), not rejected --------------------------------------------
cites16 = [f"src{i}" for i in range(14)] + ["src0", "src3"]                                      # 16 entries, 14 distinct
d, err = SW.try_parse_draft(draft(4, cites16), expected_heading="H")
assert d is not None and err is None, err
assert len(d.citations) == N.sawc.params.CITATIONS_MAX == 12 and [c.source_key for c in d.citations] == [f"src{i}" for i in range(12)]
d2, _ = SW.try_parse_draft(draft(4, ["a", "b", "a", "c"] * 5), expected_heading="H")             # 20 entries, 3 distinct -> all kept once
assert d2 is not None and [c.source_key for c in d2.citations] == ["a", "b", "c"]
print("1 over-long citation lists trimmed ok")

# 2) subtopics over the cap are trimmed in the writer's order; valid drafts are untouched ---------------------------------------------------
d3, err3 = SW.try_parse_draft(draft(15, ["a", "b"]), expected_heading="H")
assert d3 is not None and len(d3.subtopics) == N.sawc.params.SUBTOPICS_MAX and d3.subtopics[0].subheading == "Topic 0" and d3.subtopics[-1].subheading == "Topic 11", err3
ok = draft(5, ["a", "b", "c"])
assert SW.trim_lists(ok) == ok
_, err4 = SW.try_parse_draft(draft(2, ["a"]), expected_heading="H")                              # the floors still reject
assert err4 and "subtopics" in err4
print("2 over-long subtopic lists trimmed, floors still enforced ok")


# 3) coverage gate scales to what the sections can print ---------------------------------------------------------------------------------
def chapter(n_routed, n_sections, per_section_used):
    routed = [f"{i:016x}" for i in range(n_routed)]
    secs, j = [], 0
    for _ in range(n_sections):
        subs = [{"code_ref_hash": routed[(j + t) % n_routed]} for t in range(per_section_used)]
        j += per_section_used
        secs.append({"subtopics": subs, "citations": [{"source_key": "s0"}]})
    return {"_routed_hashes": routed, "_digest_sources": ["s0"], "sections": secs}


big_ok = CK.check_code_coverage(chapter(133, 4, 16))                                              # 64 printed of 133: 48 needed (75% of 64 slots)
assert big_ok.passed, big_ok.feedback
big_low = CK.check_code_coverage(chapter(133, 4, 10))                                             # 40 printed < 48
assert not big_low.passed and "40 of 133" in big_low.feedback
small = CK.check_code_coverage(chapter(10, 3, 2))                                                 # 6 printed of 10 = 60% -> still the plain bar
assert small.passed
small_low = CK.check_code_coverage(chapter(10, 3, 1))                                             # 3 of 10
assert not small_low.passed
print("3 coverage gate is capacity-aware: 133 blocks / 4 sections need 48, 10 blocks still need 6 ok")
print("ALL OK")
