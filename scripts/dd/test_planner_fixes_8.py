"""Offline checks for the 2026-10-14 batch-10 fix (no LLM, no cluster): a big pool of docs no proposal fits
becomes the catch-all appendix (split by directory afterwards) instead of being scattered by title words.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_8.py [planner_artifacts.jsonl ...]

With dumps from dump_planner_artifacts.py it also replays the rule on every stored plan."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

SEL = domains.dd.planner.nodes.chapter_select.domain
P = domains.dd.planner.nodes.chapter_select.params


def build(n_fit_per, n_nofit):
    names = ["Alpha Engine", "Beta Storage", "Gamma Network", "Delta Security"]
    proposals = [{"title": t, "description": f"{t} docs", "key_concepts": []} for t in names]
    assignments, titles = {}, {}
    for ci in range(4):
        for j in range(n_fit_per):
            k = f"ingestion/fw/pages/{ci * 100 + j:04d}-{names[ci].split()[0].lower()}-{j}.md"
            assignments[k] = [{"chapter_idx": i, "confidence": 1.0 if i == ci else 0.0} for i in range(4)]
            titles[k] = f"{names[ci]} {j}"
    for j in range(n_nofit):
        k = f"ingestion/fw/pages/{900 + j:04d}-stray-{j}.md"
        assignments[k] = [{"chapter_idx": i, "confidence": 0.0} for i in range(4)]
        titles[k] = f"Stray page {j}"
    return proposals, assignments, titles


def run(n_fit_per, n_nofit):
    proposals, assignments, titles = build(n_fit_per, n_nofit)
    return SEL.select_pipeline(
        proposals=proposals, assignments=assignments, seeds={}, doc_family={k: k for k in assignments},
        doc_title=titles, distillates={}, key_idx={k: i for i, k in enumerate(sorted(assignments))}, anchored_family={},
    )


# 1) 24 of 64 docs fit nowhere -> one appendix chapter holding exactly those docs ------------------------------------
out = run(10, 24)
app = [c for c in out["out_chapters"] if c.get("role") == "appendix"]
assert len(app) == 1 and app[0]["title"] == P.GAP_APPENDIX_TITLE and len(app[0]["member_doc_keys"]) == 24, [c["title"] for c in out["out_chapters"]]
assert all("stray" in k for k in app[0]["member_doc_keys"]) and out["stats"]["n_leftover_appendix"] == 24 and out["stats"]["n_leftover_placed"] == 0
assert sum(len(c["member_doc_keys"]) for c in out["out_chapters"]) == 64 and out["stats"]["n_unplaced_final"] == 0
print("1 big no-fit pool -> appendix ok")

# 2) 5 of 45 docs fit nowhere -> unchanged path (lexical placement, no appendix) --------------------------------------
out = run(10, 5)
assert not [c for c in out["out_chapters"] if c.get("role") == "appendix"] and out["stats"]["n_leftover_appendix"] == 0
assert out["stats"]["n_leftover_placed"] == 5 and sum(len(c["member_doc_keys"]) for c in out["out_chapters"]) == 45
print("2 small no-fit pool -> unchanged ok")

# 3) 16 docs but under the share bar (300 docs) -> unchanged -----------------------------------------------------------
out = run(71, 16)
assert out["stats"]["n_leftover_appendix"] == 0 and out["stats"]["n_leftover_placed"] == 16
print("3 pool under the share bar -> unchanged ok")

# 4) replay on stored plans ----------------------------------------------------------------------------------------------
n_rows = 0
for path in sys.argv[1:]:
    for line in open(path):
        row = json.loads(line)
        ents = (row.get("manifest") or {}).get("entries") or []
        res = SEL.select_pipeline(
            proposals=row["proposals"], assignments=row["assignments"], seeds={},
            doc_family={e["key"]: (e.get("url") or "").split("#", 1)[0] for e in ents if e.get("key")},
            doc_title={e["key"]: e.get("title") or "" for e in ents if e.get("key")},
            distillates=row.get("distillates") or {}, key_idx={e["key"]: int(e.get("idx") or 0) for e in ents if e.get("key")},
            anchored_family=SEL.anchored_family_map(ents),
        )
        n = sum(len(c["member_doc_keys"]) for c in res["out_chapters"])
        assert n == len(row["assignments"]) and res["stats"]["n_unplaced_final"] == 0, row["slug"]
        print(f"   {row['slug']}: {len(res['out_chapters'])} chapters, appendix pool {res['stats']['n_leftover_appendix']}, lexical leftovers {res['stats']['n_leftover_placed']}")
        n_rows += 1
if n_rows:
    print(f"4 replay ok on {n_rows} stored plans")
print("ALL OK")
