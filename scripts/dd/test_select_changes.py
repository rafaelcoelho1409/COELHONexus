"""Self-checking tests for the deterministic chapter_select changes (family
harmonization, Foundations chapter, oversize-split planning/validation) on a
real manifest with synthetic assignment scores. Run with the app's venv:

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_select_changes.py asyncio_fixture.json

The fixture is {"entries", "distillates", "proposals"} dumped for one framework
whose manifest has anchor-split families (asyncio). Prints OK or raises.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

sel = domains.dd.planner.nodes.chapter_select.domain
P = domains.dd.planner.nodes.chapter_select.params

fx = json.load(open(sys.argv[1]))
entries, proposals = fx["entries"], fx["proposals"]
page = lambda e: (e.get("url") or "").split("#", 1)[0].rsplit("/", 1)[-1].removesuffix(".html")
HOME = {  # parent page → proposal index (proposals: running, tasks, coordinating, sync, streams, subprocess, queues, loop, protocols, advanced)
    "asyncio": 0, "asyncio-runner": 0, "asyncio-task": 1, "asyncio-graph": 2, "asyncio-future": 2,
    "asyncio-sync": 3, "asyncio-stream": 4, "asyncio-subprocess": 5, "asyncio-queue": 6,
    "asyncio-eventloop": 7, "asyncio-protocol": 8, "asyncio-policy": 9, "asyncio-extending": 9,
    "asyncio-exceptions": 1,
}
fam_docs = defaultdict(list)
for e in entries:
    fam_docs[page(e)].append(e["key"])

# Synthetic assigner: every doc scores its page's home chapter 0.80. Failure injection =
# the observed bug: every 4th doc of each family (≥3 docs) is *confidently* placed in another
# chapter (0.85) while still rating the family's home chapter plausible (0.55).
assignments, injected = {}, set()
for pg, keys in fam_docs.items():
    home = HOME.get(pg, 9)
    for i, k in enumerate(keys):
        sc = {c: 0.1 for c in range(len(proposals))}
        sc[home] = 0.8
        if len(keys) >= 3 and i % 4 == 3:
            other = (home + 3) % len(proposals)
            sc[home], sc[other] = 0.55, 0.85
            injected.add(k)
        assignments[k] = [{"chapter_idx": c, "confidence": v} for c, v in sc.items()]

doc_family = {e["key"]: (e.get("url") or "").split("#", 1)[0] for e in entries}
doc_title = {e["key"]: e.get("title") or "" for e in entries}
key_idx = {e["key"]: e["idx"] for e in entries}
anchored = sel.anchored_family_map(entries)


def strays(out):
    ch = {k: i for i, c in enumerate(out["out_chapters"]) for k in c["member_doc_keys"]}
    n = 0
    for pg, keys in fam_docs.items():
        if len(keys) >= 3 and pg != "asyncio-exceptions":
            n += len(keys) - Counter(ch[k] for k in keys).most_common(1)[0][1]
    return n


kw = dict(proposals=proposals, assignments=assignments, seeds={}, doc_family=doc_family,
          doc_title=doc_title, distillates=fx["distillates"], key_idx=key_idx)
control = sel.select_pipeline(**kw)                       # no anchored families (old behavior)
fixed = sel.select_pipeline(**kw, anchored_family=anchored)
print(f"strays: control={strays(control)} harmonized={strays(fixed)} (moved {fixed['stats']['n_family_harmonized']})")
assert strays(control) > 0, "fixture must reproduce torn families"
if strays(fixed):
    ch = {k: c["title"][:22] for c in fixed["out_chapters"] for k in c["member_doc_keys"]}
    for pg, keys in fam_docs.items():
        cnt = Counter(ch[k] for k in keys)
        if len(keys) >= 3 and len(cnt) > 1:
            print("  torn:", pg, len(keys), dict(cnt), "injected:", sum(k in injected for k in keys))
            for k in keys:
                if k in injected:
                    print("    ", k[-40:], "anchored:", k in anchored, "scores:", {a["chapter_idx"]: a["confidence"] for a in assignments[k] if a["confidence"] > 0.2})
assert strays(fixed) == 0, "harmonization must keep sibling sub-pages together"
assert fixed["stats"]["n_unplaced_final"] == 0
assert sum(c["n_member_docs"] for c in fixed["out_chapters"]) == len(entries), "no doc may go missing"

# a strong, specific disagreement must NOT be overridden (conf gap > HARMONIZE_MAX_GAP)
k0 = fam_docs["asyncio-task"][0]
a2 = dict(assignments); a2[k0] = [{"chapter_idx": c, "confidence": (0.99 if c == 5 else 0.05)} for c in range(len(proposals))]
r2 = sel.select_pipeline(**{**kw, "assignments": a2}, anchored_family=anchored)
assert any(k0 in c["member_doc_keys"] and c["title"] == proposals[5]["title"] for c in r2["out_chapters"]), "specific disagreement was overridden"

# single-URL dumps (every doc one URL, no anchors) must be untouched
flat_family = {k: "https://x/llms-full.txt" for k in doc_family}
r3 = sel.select_pipeline(**{**kw, "doc_family": flat_family}, anchored_family=sel.anchored_family_map([{**e, "anchors": None} for e in entries]))
assert r3["stats"]["n_family_harmonized"] == 0

# Foundations: LLM-labelled single-page overview/cross-cutting docs → one front-matter chapter
roles = {"asyncio": "overview", "asyncio-api-index": "overview", "asyncio-llapi-index": "overview",
         "asyncio-tools": "cross_cutting", "asyncio-platforms": "cross_cutting", "asyncio-dev": "cross_cutting",
         "asyncio-threading": "cross_cutting"}
dist = {k: {**d, "role": roles.get(page(next(e for e in entries if e["key"] == k)), "reference")} for k, d in fx["distillates"].items()}
r4 = sel.select_pipeline(**{**kw, "distillates": dist}, anchored_family=anchored)
f = r4["out_chapters"][0]
print("foundations:", f["title"], f["n_member_docs"], "| chapters:", [(c["title"][:18], c["n_member_docs"]) for c in r4["out_chapters"]])
assert f["origin"] == "foundations" and f["n_member_docs"] == 7 and f["order"] == 1
assert all(c["n_member_docs"] >= P.MIN_DOCS_PER_CHAPTER for c in r4["out_chapters"][1:]), "a chapter fell below the minimum"
assert r4["stats"]["n_unplaced_final"] == 0
assert sum(c["n_member_docs"] for c in r4["out_chapters"]) == len(entries)
# below FOUNDATIONS_MIN_DOCS → nothing moves
few = {k: ({**d, "role": "overview"} if i < 2 else d) for i, (k, d) in enumerate(dist.items())}
few = {k: ({**d, "role": "reference"} if "role" in d and i >= 2 else d) for i, (k, d) in enumerate(few.items())}
assert sel.select_pipeline(**{**kw, "distillates": few}, anchored_family=anchored)["stats"]["n_foundations"] == 0
# family members are never pulled out as Foundations even if mislabelled
fam_roles = {k: {**d, "role": "overview"} for k, d in fx["distillates"].items()}
r5 = sel.select_pipeline(**{**kw, "distillates": fam_roles}, anchored_family=anchored)
cap = max(P.FOUNDATIONS_MIN_DOCS, int(P.FOUNDATIONS_MAX_SHARE * len(entries)))
fk = next((c for c in r5["out_chapters"] if c.get("origin") == "foundations"), None)
assert fk is None or fk["n_member_docs"] <= cap
assert fk is None or all(len(fam_docs[page(next(e for e in entries if e["key"] == k))]) == 1 for k in fk["member_doc_keys"])

# oversize split: planning + validation + application
big = [{"title": "Big", "description": "d", "member_doc_keys": [f"k{i}" for i in range(38)], "n_member_docs": 38, "origin": "proposal", "source_proposal_idx": 1}] + \
      [{"title": f"C{j}", "description": "d", "member_doc_keys": [f"c{j}_{i}" for i in range(n)], "n_member_docs": n, "origin": "proposal", "source_proposal_idx": j}
       for j, n in enumerate([23, 15, 11, 10, 7])]
assert sel.plan_splits(big) == [(0, 2)], sel.plan_splits(big)
assert sel.plan_splits([{**c, "member_doc_keys": c["member_doc_keys"][:5], "n_member_docs": 5} for c in big]) == []   # tiny corpus
units = sel.build_split_units(big[0]["member_doc_keys"], anchored_family={f"k{i}": "fam" for i in range(0, 6)}, doc_title={}, distillates={}, key_idx={})
assert len(units) == 33 and len(units[0]["keys"]) == 6, (len(units), units[0])
ok, why = sel.validate_split([{"title": "Alpha Part", "unit_ids": list(range(0, 17))}, {"title": "Beta Part", "unit_ids": list(range(17, 33))}], units, {"c0"})
assert ok, why
assert not sel.validate_split([{"title": "Alpha Part", "unit_ids": list(range(0, 17))}, {"title": "Beta Part", "unit_ids": list(range(16, 33))}], units, set())[0]   # duplicate unit
assert not sel.validate_split([{"title": "Alpha Part", "unit_ids": list(range(0, 17))}, {"title": "Beta Part", "unit_ids": list(range(17, 32))}], units, set())[0]   # missing unit
assert not sel.validate_split([{"title": "Alpha Part", "unit_ids": list(range(0, 31))}, {"title": "Beta Part", "unit_ids": [31, 32]}], units, set())[0]           # lopsided / too small
assert not sel.validate_split([{"title": "Alpha Part", "unit_ids": list(range(0, 17))}, {"title": "alpha part", "unit_ids": list(range(17, 33))}], units, set())[0]  # duplicate title
assert sel.validate_split([{"title": "Alpha", "unit_ids": list(range(0, 17))}, {"title": "Beta Part", "unit_ids": list(range(17, 33))}], units, set())[0]           # 1-word title is fine now
assert not sel.validate_split([{"title": "Overview", "unit_ids": list(range(0, 17))}, {"title": "Beta Part", "unit_ids": list(range(17, 33))}], units, set())[0]    # generic 1-word title
parts = [{"title": "Alpha Part", "description": "a", "unit_ids": list(range(0, 17))}, {"title": "Beta Part", "description": "b", "unit_ids": list(range(17, 33))}]
new = sel.apply_split(big[0], parts, units)
assert [c["n_member_docs"] for c in new] == [22, 16] and new[0]["source_proposal_idx"] == 1 and new[1]["source_proposal_idx"] is None
assert sorted(new[0]["member_doc_keys"] + new[1]["member_doc_keys"]) == sorted(big[0]["member_doc_keys"])
print("OK")
