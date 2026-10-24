"""Offline checks for the 2026-10-14 batch-10 fix (no LLM, no cluster): a big pool of docs no proposal fits
becomes the catch-all appendix (split by directory afterwards) instead of being scattered by title words.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_planner_fixes_8.py [planner_artifacts.jsonl ...]

With dumps from dump_planner_artifacts.py it also replays the rule on every stored plan."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
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


def run(n_fit_per, n_nofit, key_dir=None):
    proposals, assignments, titles = build(n_fit_per, n_nofit)
    return SEL.select_pipeline(
        proposals=proposals, assignments=assignments, seeds={}, doc_family={k: k for k in assignments},
        doc_title=titles, distillates={}, key_idx={k: i for i, k in enumerate(sorted(assignments))}, anchored_family={},
        key_dir=key_dir,
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

# 3b) a small pool whose directory is owned by one chapter joins it (title words say nothing) ---------------------------
proposals, assignments, titles = build(10, 5)
kd = {k: ("docs", "storage") if ("beta" in k or "stray" in k) else ("docs", "other-" + k.split("-")[1]) for k in assignments}
res = SEL.select_pipeline(
    proposals=proposals, assignments=assignments, seeds={}, doc_family={k: k for k in assignments}, doc_title=titles,
    distillates={}, key_idx={k: i for i, k in enumerate(sorted(assignments))}, anchored_family={}, key_dir=kd,
)
beta = next(c for c in res["out_chapters"] if c["title"] == "Beta Storage")
assert sum("stray" in k for k in beta["member_doc_keys"]) == 5 and res["stats"]["n_leftover_dir_owned"] == 5, res["stats"]
assert res["stats"]["n_leftover_placed"] == 0
res = run(10, 5)
assert res["stats"]["n_leftover_dir_owned"] == 0 and res["stats"]["n_leftover_placed"] == 5          # no directories known -> as before
print("3b directory-owned strays ok")

# 3c) assign prompt: the page's URL path is shown when known, the static prefix is unchanged otherwise -------------------
CA = domains.dd.planner.nodes.chapter_assign
props = [{"title": f"C{i}", "description": "d", "key_concepts": []} for i in range(14)]
kw = dict(framework="fw", source_key="ingestion/fw/pages/0001-x-fw.md", doc_summary="s", doc_terms=["t"], doc_body="", proposals=props)
with_path = CA.prompts.build_prompt(**kw, doc_path="integrations/frameworks/agno-agents")
without = CA.prompts.build_prompt(**kw)
assert "PAGE URL PATH: integrations/frameworks/agno-agents" in with_path and "PAGE URL PATH" not in without
assert with_path.rindex("PAGE URL PATH") > with_path.index("== FILE:") and CA.prompts.PATH_RUBRIC in with_path
assert "EXACTLY 14 keys" in CA.prompts.to_fixed_key_prompt(with_path, 14)
up = CA.domain.url_paths([
    {"key": "a", "url": "https://langfuse.com/integrations/frameworks/agno-agents#sec"},
    {"key": "b", "url": "https://docs.python.org/3/library/asyncio-task.html?x=1"},
    {"key": "c", "source_path": "https://x.dev/docs/api/", "url": ""},
    {"key": "d", "url": "https://x.dev/"}, {"url": "https://x.dev/z"},
])
assert up == {"a": "integrations/frameworks/agno-agents", "b": "3/library/asyncio-task", "c": "docs/api"}, up
print("3c assign prompt carries the URL path ok")

# 3d) order: chapters made of a catalog directory's sub-directories share one home and end up adjacent ----------------------
OC = domains.dd.planner.nodes.order_chapters.domain
def mk(title, dirs, **kw):
    return {"title": title, "member_doc_keys": [f"k/{title}/{i}" for i in range(len(dirs))], **kw}, {f"k/{title}/{i}": d for i, d in enumerate(dirs)}
parts = [
    mk("Frameworks", [("integrations", "frameworks")] * 6),
    mk("Evaluation", [("docs", "evaluation")] * 6),
    mk("Providers", [("integrations", "model-providers")] * 5 + [("docs", "x")]),
    mk("Tracing", [("docs", "observability")] * 6),
    mk("Gateways", [("integrations", "gateways")] * 4),
    mk("Admin", [("docs", "administration")] * 5),
    mk("Mixed integrations", [("integrations", "other")] * 3 + [("integrations", "analytics")] * 3 + [("docs", "y")]),
    mk("Mostly docs", [("integrations", "frameworks")] * 2 + [("docs", "observability")] * 4),
]
chs_, kd_ = [c for c, _ in parts], {k: v for _, d in parts for k, v in d.items()}
homes = OC.chapter_home_dirs(chs_, kd_)
assert [homes[i] for i in (0, 2, 4, 6)] == [("integrations",)] * 4 and homes[7] != ("integrations",), homes
scattered = [0, 1, 3, 5, 7, 2, 4, 6]                       # integration chapters at positions 0, 5, 6, 7 -> gaps > 2 pulled up
new_order, moved = OC.group_by_home_dir(scattered, chs_, homes)
pos = [new_order.index(i) for i in (0, 2, 4, 6)]
assert max(pos) - min(pos) <= 4 and sorted(new_order) == list(range(8)) and moved > 0, new_order
print("3d catalog-family chapters stay together ok:", new_order)

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
            anchored_family=SEL.anchored_family_map(ents), key_dir=SEL.page_dirs(ents),
        )
        n = sum(len(c["member_doc_keys"]) for c in res["out_chapters"])
        assert n == len(row["assignments"]) and res["stats"]["n_unplaced_final"] == 0, row["slug"]
        print(f"   {row['slug']}: {len(res['out_chapters'])} chapters, appendix pool {res['stats']['n_leftover_appendix']}, directory-owned {res['stats']['n_leftover_dir_owned']}, lexical leftovers {res['stats']['n_leftover_placed']}")
        n_rows += 1
if n_rows:
    print(f"4 replay ok on {n_rows} stored plans")
print("ALL OK")
