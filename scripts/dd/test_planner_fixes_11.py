"""Offline checks for the 2026-10-18 planner change (no LLM, no cluster): a chapter holding more code blocks than its sections can print is split, whatever its page count.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_11.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

SEL = domains.dd.planner.nodes.chapter_select.domain
P = domains.dd.planner.nodes.chapter_select.params


def chap(title, n, **kw):
    return {"title": title, "description": "d", "member_doc_keys": [f"ingestion/fw/pages/{title[:3]}-{i:04d}.md" for i in range(n)],
            "n_member_docs": n, "origin": "proposal", **kw}


def blocks(chapter, per_doc):
    return {k: per_doc for k in chapter["member_doc_keys"]}


# 1) fenced blocks are counted in pairs; indented code and unclosed fences do not count
text = "intro\n```python\nx = 1\n```\n\ntext\n~~~bash\nls\n~~~\n    ```\n    indented\n    ```\n```js\nnever closed\n"
assert SEL.code_block_count(text) == 2 and SEL.code_block_count("") == 0 and SEL.code_block_count(None) == 0

# 2) a heavy chapter splits by blocks even when its pages are few and its share is small (deepagents ch-05: 8 pages, 303 blocks)
others = [chap(f"Other {i}", 8) for i in range(10)]
heavy = chap("Heavy chapter", 8)
chapters = others[:3] + [heavy] + others[3:]
db = {}
for c in chapters:
    db.update(blocks(c, 5))
db.update(blocks(heavy, 38))                                                       # 304 blocks
plan = SEL.plan_splits(chapters, doc_blocks=db)
assert plan == [(3, 2)], plan                                                      # 8 pages // MIN_DOCS_PER_CHAPTER(3) = 2 parts at most
assert SEL.plan_splits(chapters) == [], SEL.plan_splits(chapters)                   # without block counts: unchanged behaviour

# 3) more pages -> more parts (22 pages, 270 blocks -> 2 parts; 300 blocks -> ceil(300/140) = 3)
big = chap("Integrations", 22)
chs = others[:3] + [big] + others[3:]
db2 = {}
for c in chs:
    db2.update(blocks(c, 4))
db2.update({k: 12 for k in big["member_doc_keys"]})                                 # 264 blocks
assert SEL.plan_splits(chs, doc_blocks=db2) == [(3, 2)]
db2.update({k: 14 for k in big["member_doc_keys"]})                                 # 308 blocks
assert SEL.plan_splits(chs, doc_blocks=db2) == [(3, 3)], SEL.plan_splits(chs, doc_blocks=db2)

# 4) at or under the cap: untouched; tiny chapters, foundations and appendices are left alone; the chapter cap is respected
mid = chap("Medium", 18)
chs3 = others[:3] + [mid] + others[3:]
db3 = {}
for c in chs3:
    db3.update(blocks(c, 4))
db3.update({k: 11 for k in mid["member_doc_keys"]})                                 # 198 blocks <= 200
assert SEL.plan_splits(chs3, doc_blocks=db3) == []
few = chap("Few pages", 5)                                                          # 5 pages < SPLIT_BLOCKS_MIN_DOCS
chs4 = others[:3] + [few] + others[3:]
db4 = {k: 80 for k in few["member_doc_keys"]}
assert SEL.plan_splits(chs4, doc_blocks=db4) == []
found = chap("Foundations", 9, origin="foundations")
assert SEL.plan_splits(others[:3] + [found] + others[3:], doc_blocks={k: 60 for k in found["member_doc_keys"]}) == []
app = chap("Appendix", 9, role="appendix")
assert [p for p in SEL.plan_splits(others[:3] + [app] + others[3:], doc_blocks={k: 60 for k in app["member_doc_keys"]}) if p[0] == 3] == []
crowded = others[:3] + [heavy] + others[3:]
assert SEL.plan_splits(crowded, max_chapters=len(crowded), doc_blocks=db) == []     # no room for another chapter
assert P.SPLIT_MAX_BLOCKS == 200 and P.SPLIT_BLOCKS_PER_PART == 140
print("ALL OK")
