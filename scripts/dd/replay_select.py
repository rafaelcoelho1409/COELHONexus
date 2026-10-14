"""Offline replay of the Planner's deterministic chapter-selection logic on
stored artifacts — no LLM, no cluster, no redeploy. Used to measure changes to
chapter_select / consolidation against every stored framework before shipping.

    # 1) dump (see dump_planner_artifacts.py), 2) replay with the app's venv:
    cd apps/fastapi && .venv/bin/python ../../scripts/dd/replay_select.py planner_artifacts.jsonl

Prints per-framework metrics. Compare runs before/after a change:
  n_ch / max% / gini        chapter count, largest chapter share, size inequality
  torn / stray              families (>=3 docs, same parent page) split across
                            chapters, and docs outside their family's top chapter
  unplaced                  docs missing from the plan (must be 0)
  found / harm / split      docs moved to Foundations / docs re-homed by family
                            harmonization / chapters the LLM split pass would act on
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

sel = domains.dd.planner.nodes.chapter_select.domain


def run_pipeline(row: dict) -> dict:
    proposals, assignments = row["proposals"], row["assignments"]
    entries = (row.get("manifest") or {}).get("entries") or []
    doc_family = {e["key"]: (e.get("url") or "").split("#", 1)[0] for e in entries if e.get("key")}
    doc_title = {e["key"]: e.get("title") or "" for e in entries if e.get("key")}
    key_idx = {e["key"]: e.get("idx", 0) for e in entries if e.get("key")}
    if hasattr(sel, "select_pipeline"):
        return sel.select_pipeline(
            proposals=proposals, assignments=assignments, seeds={},
            doc_family=doc_family, doc_title=doc_title,
            distillates=row.get("distillates") or {}, key_idx=key_idx,
            anchored_family=sel.anchored_family_map(entries), key_dir=sel.page_dirs(entries),
        ) | {"doc_family": doc_family}
    selected, d2c = sel.greedy_select(proposals=proposals, assignments=assignments, pinned_indices=set())
    final = sel.prune_and_finalize_selection(
        selected=selected, doc_to_chapter=d2c, assignments=assignments, pinned=set(), proposals=proposals,
    )
    cons = sel.consolidate_placements(
        kept=final["kept"], doc_to_chapter=final["doc_to_chapter"], assignments=assignments,
        proposals=proposals, doc_family=doc_family, doc_title=doc_title,
    )
    return {**cons, "doc_family": doc_family}


def metrics(row: dict, out: dict) -> dict:
    chapters = out["out_chapters"]
    sizes = sorted((c["n_member_docs"] for c in chapters), reverse=True)
    total = sum(sizes) or 1
    n = len(sizes)
    gini = (sum(abs(a - b) for a in sizes for b in sizes) / (2 * n * total)) if n else 0.0
    doc_ch = {k: i for i, c in enumerate(chapters) for k in c["member_doc_keys"]}
    fams = defaultdict(list)
    for k, f in out["doc_family"].items():
        if k in doc_ch:
            fams[f or k].append(doc_ch[k])
    torn = stray = 0
    for docs in fams.values():
        if len(docs) >= 3:
            top = Counter(docs).most_common(1)[0][1]
            if top < len(docs):
                torn += 1
                stray += len(docs) - top
    return {
        "docs": len(row["assignments"]), "n_ch": n, "max%": round(100 * sizes[0] / total) if sizes else 0,
        "gini": round(gini, 2), "torn": torn, "stray": stray,
        "unplaced": out["stats"].get("n_unplaced_final", 0),
        "found": out["stats"].get("n_foundations", 0),
        "harm": out["stats"].get("n_family_harmonized", 0),
        "split": len(sel.plan_splits(chapters)) if hasattr(sel, "plan_splits") else 0,
        "sizes": sizes[:8],
    }


def main(path: str) -> None:
    rows = [json.loads(l) for l in open(path)]
    print(f"{'slug':22}{'docs':>5}{'n_ch':>5}{'max%':>5}{'gini':>6}{'torn':>5}{'stray':>6}{'unpl':>5}{'found':>6}{'harm':>5}{'split':>6}  top sizes")
    for row in rows:
        m = metrics(row, run_pipeline(row))
        print(f"{row['slug']:22}{m['docs']:>5}{m['n_ch']:>5}{m['max%']:>5}{m['gini']:>6}{m['torn']:>5}{m['stray']:>6}{m['unplaced']:>5}{m['found']:>6}{m['harm']:>5}{m['split']:>6}  {m['sizes']}")


if __name__ == "__main__":
    main(sys.argv[1])
