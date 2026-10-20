"""Offline checks for the 2026-10-19 digest change (no LLM, no cluster): a page with many code blocks is digested in chunks and the chunk digests are merged.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_7.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

DG = domains.dd.synth.nodes.digest
DOM, SV, DS, P = DG.domain, DG.service, DG.schemas, DG.params


def page(n_blocks, with_headings=True):
    parts = ["# The page", "", "Intro prose."]
    for i in range(n_blocks):
        if with_headings and i % 10 == 0:
            parts += ["", f"## Topic {i // 10}", "", f"Prose about topic {i // 10}."]
        parts += ["", f"Example {i}:", "", f'<code-ref hash="{i + 1:016x}"/>']
    return "\n".join(parts) + "\n"


# 1) a page under the cap is untouched; a page over it is cut into chunks of whole blocks, at headings ------------------------------------------
small = page(20)
assert DOM.split_source_by_blocks(small, 30) == [small]
big = page(86)
chunks = DOM.split_source_by_blocks(big, 30)
assert len(chunks) == 3, len(chunks)
per_chunk = [DOM.extract_vault_hashes(c) for c in chunks]
assert sum(len(set(h)) for h in per_chunk) == 86 and all(len(set(h)) <= 40 for h in per_chunk)
assert set().union(*map(set, per_chunk)) == set(DOM.extract_vault_hashes(big))                      # every block lands in exactly one chunk
assert all(not set(a) & set(b) for i, a in enumerate(per_chunk) for b in per_chunk[i + 1:])
assert all(c.lstrip().startswith("# The page (continued)") for c in chunks[1:]) and chunks[0].startswith("# The page\n")
assert all(c.split("\n\n", 1)[1].startswith("## Topic") for c in chunks[1:])                             # cut at a heading, not inside a topic
nohead = DOM.split_source_by_blocks(page(70, with_headings=False), 30)
assert len(nohead) == 3 and all(len(set(DOM.extract_vault_hashes(c))) <= 24 for c in nohead)        # no headings: cut at a blank line

# 2) merging: refs and facts united, best relevance kept, unassigned only when no chunk placed the block -------------------------------------
def contrib(sid, rel, refs, facts):
    return DS.SectionContribution(section_id=sid, source_key="k", relevance=rel, summary="y" * 30, key_facts=facts, code_refs=refs)


a = DS.SourceDigest(source_key="k", source_title="Page", overall_summary="s" * 40, deployment="m1", wall_ms=100,
                    contributes_to=[contrib("s1", "supporting", ["a" * 16], ["fact one long enough"]), contrib("s2", "primary", ["b" * 16], ["fact two long enough"])],
                    unassigned_code_refs=["e" * 16, "f" * 16])
b = DS.SourceDigest(source_key="k", source_title="Page 2", overall_summary="t" * 40, deployment="m2", wall_ms=300,
                    contributes_to=[contrib("s1", "primary", ["c" * 16, "a" * 16], ["fact one long enough", "fact three long enough"]), contrib("s3", "tangential", ["d" * 16], ["fact four long enough"])],
                    unassigned_code_refs=["f" * 16, "e" * 16])
m = DOM.merge_chunk_digests("k", [a, b])
by = {c.section_id: c for c in m.contributes_to}
assert [c.section_id for c in m.contributes_to] == ["s1", "s2", "s3"]
assert by["s1"].code_refs == ["a" * 16, "c" * 16] and by["s1"].relevance == "primary"                # united without duplicates, better relevance
assert by["s1"].key_facts == ["fact one long enough", "fact three long enough"]
assert m.source_title == "Page" and m.wall_ms == 300 and m.unassigned_code_refs == ["e" * 16, "f" * 16]
def merged_facts(n):
    cs = [contrib("s1", "primary", [f"{i:016x}"], [f"fact number {i} is long enough"]) for i in range(1, n + 1)]
    ps = [DS.SourceDigest(source_key="k", source_title="P", overall_summary="s" * 40, contributes_to=[c]) for c in cs]
    return DOM.merge_chunk_digests("k", ps).contributes_to[0]


assert len(merged_facts(4).key_facts) == 4 and len(merged_facts(4).code_refs) == 4
assert len(merged_facts(8).key_facts) == P.MAX_KEY_FACTS_PER_CONTRIB and len(merged_facts(8).code_refs) == 8      # facts are capped, code refs never are

# 3) the service wrapper: one call per chunk, a failed chunk is skipped, all chunks failed -> the source fails ------------------------------------
calls = []
real = SV._digest_one_source


def fake_factory(fail_idx=()):
    async def fake(**kw):
        calls.append(kw["source_md"])
        i = len(calls) - 1
        if i in fail_idx:
            return None, "ChatError: boom"
        hs = DOM.extract_vault_hashes(kw["source_md"])
        return DS.SourceDigest(source_key=kw["source_key"], source_title="T", overall_summary="s" * 40,
                               contributes_to=[contrib("s1", "primary", hs, ["fact long enough here"])]), None
    return fake


kw = dict(sem=asyncio.Semaphore(4), sample_idx=0, n_total=1, thread_id="t", chapter_id="c", chapter_title="C", framework="f", outline_sections=[], valid_section_ids={"s1"})
SV._digest_one_source = fake_factory()
d, err = asyncio.run(SV._digest_source(source_key="k", source_md=big, **kw))
assert err is None and len(calls) == 3 and len(d.contributes_to) == 1 and len(d.contributes_to[0].code_refs) == 86       # 3 calls, one merged digest holding all 86 blocks
SV.params.WAVE_PAUSE_S = 0
calls.clear()
SV._digest_one_source = fake_factory(fail_idx=(1,))
d, err = asyncio.run(SV._digest_source(source_key="k", source_md=big, **kw))
assert err is None and len(calls) == 4 and len(d.contributes_to[0].code_refs) == 86                                            # the failed chunk is retried once and recovers
calls.clear()
SV._digest_one_source = fake_factory(fail_idx=(1, 3))
d, err = asyncio.run(SV._digest_source(source_key="k", source_md=big, **kw))
assert err is None and len(calls) == 4 and 40 <= len(d.contributes_to[0].code_refs) < 86                                        # still failing after the retry: skipped, the other two chunks count
calls.clear()
SV._digest_one_source = fake_factory(fail_idx=(0, 1, 2))
d, err = asyncio.run(SV._digest_source(source_key="k", source_md=big, **kw))
assert d is None and err == "ChatError: boom" and len(calls) == 3                                                                # every chunk failed: no chunk-level retry, the page-level wave handles it
calls.clear()
SV._digest_one_source = fake_factory()
d, err = asyncio.run(SV._digest_source(source_key="k", source_md=small, **kw))
assert err is None and len(calls) == 1                                                                                           # a small page: a single call as before
SV._digest_one_source = real

# 4) outline repair acceptance: a repair that clears the section floor is kept even with a few more issues --------------------------------------
OL = domains.dd.synth.nodes.outline
FLOOR = OL.params.FLOOR_ISSUE_PREFIX + " 5 H2 sections for 160 blocks"
assert OL.domain.is_floor_issue(FLOOR)
assert OL.domain.accept_repair([FLOOR], ["a", "b"], 2)                       # floor cleared, one more issue than before: kept (qdrant ch-10: 20 sections, 2 issues)
assert OL.domain.accept_repair([FLOOR], ["a", "b", "c"], 2)                  # exactly slack more: kept
assert not OL.domain.accept_repair([FLOOR], ["a", "b", "c", "d"], 2)         # more than slack: rejected
assert not OL.domain.accept_repair([FLOOR], [FLOOR, "a"], 2)                 # floor still open and worse: rejected
assert OL.domain.accept_repair([FLOOR, "x"], [FLOOR], 2)                     # fewer issues: kept as before
assert OL.domain.accept_repair(["x"], ["y"], 2) and not OL.domain.accept_repair(["x"], ["y", "z"], 2)   # no floor issue involved: the old strict rule
assert OL.params.REPAIR_FLOOR_SLACK == 2
print("ALL OK")
