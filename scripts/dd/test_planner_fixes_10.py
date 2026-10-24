"""Offline checks for the 2026-10-16 batch (no LLM, no cluster): Foundations never takes catalog-directory pages.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_planner_fixes_10.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import domains  # noqa: E402

sel = domains.dd.planner.nodes.chapter_select.domain


def corpus(catalog_overviews):
    """Two catalog dirs with ONE or TWO overview-labelled pages each (under FOUNDATIONS_CATALOG_MIN), four real overviews."""
    chapters = [{"title": "A", "member_doc_keys": [], "origin": "proposal"}, {"title": "B", "member_doc_keys": [], "origin": "proposal"}]
    fam, role, key_dir = {}, {}, {}
    n = 0

    def add(url, r, ch):
        nonlocal n
        k = f"k{n}"; n += 1
        fam[k], role[k] = url, r
        key_dir[k] = tuple(p for p in url.split("://", 1)[1].split("/")[1:-1])
        chapters[ch]["member_doc_keys"].append(k)
        return k
    cat = [add(f"https://x.dev/oss/integrations/providers/p{i}", "overview" if i < catalog_overviews else "reference", 0) for i in range(6)]
    cat += [add("https://x.dev/oss/integrations/embeddings", "overview", 0)]
    for i in range(30):
        add(f"https://x.dev/oss/guide/g{i}", "reference", 1)
    real = [add(f"https://x.dev/oss/{d}/overview", "overview", 1) for d in ("a", "b", "c", "d")]
    return chapters, role, fam, key_dir, cat, real


# 1) catalog-directory pages are excluded even when each directory holds fewer than FOUNDATIONS_CATALOG_MIN candidates
ch, role, fam, kd, cat, real = corpus(catalog_overviews = 2)
old, st_old = sel.apply_foundations(out_chapters = [dict(c, member_doc_keys = list(c["member_doc_keys"])) for c in ch], doc_role = role, doc_family = fam, key_idx = {})
assert {cat[0], cat[1]} <= set(old[0]["member_doc_keys"]), "premise: without key_dir the catalog pages are taken"
out, st = sel.apply_foundations(out_chapters = ch, doc_role = role, doc_family = fam, key_idx = {}, key_dir = kd)
assert out[0]["origin"] == "foundations" and set(out[0]["member_doc_keys"]) == set(real), out[0]["member_doc_keys"]
assert st["foundations_catalog_excluded"] == 3 and st["n_foundations"] == 4, st
print("1 catalog pages with < FOUNDATIONS_CATALOG_MIN candidates per dir stay out of Foundations ok", st)

# 2) the remaining candidates below FOUNDATIONS_MIN_DOCS -> no Foundations chapter at all (what the fgts_va langchain run had)
ch, role, fam, kd, cat, real = corpus(catalog_overviews = 2)
for k in real[2:]:
    role[k] = "reference"
out, st = sel.apply_foundations(out_chapters = ch, doc_role = role, doc_family = fam, key_idx = {}, key_dir = kd)
assert all(c.get("origin") != "foundations" for c in out) and st["n_foundations"] == 0 and len(out) == 2, st
print("2 too few real overviews -> no Foundations chapter ok")

# 3) no key_dir (old callers) -> unchanged behaviour
ch, role, fam, kd, cat, real = corpus(catalog_overviews = 2)
out, st = sel.apply_foundations(out_chapters = ch, doc_role = role, doc_family = fam, key_idx = {})
assert st["foundations_catalog_excluded"] == 0, st
print("3 key_dir optional ok")

# 4) replay of the stored langchain latency_ts run: Foundations is gone, every page is still placed (skipped when the archive is absent)
arch = Path.home() / "planner-runs" / "langchain-latency_ts-2026-10-16" / "blobs.json"
if arch.exists():
    b = json.loads(arch.read_text())
    E = b["_manifest"]["entries"]
    out = sel.select_pipeline(
        proposals = b["planner/langchain/chapter_propose-latest.json"]["proposals"],
        assignments = b["planner/langchain/chapter_assign-latest.json"]["assignments"], seeds = {},
        doc_family = {e["key"]: (e.get("url") or "").split("#", 1)[0] for e in E},
        doc_title = {e["key"]: e.get("title") or "" for e in E},
        distillates = b["planner/langchain/doc_distill-latest.json"]["distillates"],
        key_idx = {e["key"]: e.get("idx", 0) for e in E},
        anchored_family = sel.anchored_family_map(E), key_dir = sel.page_dirs(E),
    )
    placed = sum(len(c["member_doc_keys"]) for c in out["out_chapters"])
    assert not any(c.get("origin") == "foundations" for c in out["out_chapters"]) and placed == 98, (placed, out["stats"])
    hf = [k for k in out["doc_to_chapter"] if "hugging-face" in k][0]
    assert "Integration" in out["out_chapters"][out["doc_to_chapter"][hf]]["title"], out["out_chapters"][out["doc_to_chapter"][hf]]["title"]
    print("4 langchain latency_ts replay: no Foundations, 98 pages placed, Hugging Face page in", out["out_chapters"][out["doc_to_chapter"][hf]]["title"], "ok")
else:
    print("4 (skipped: ~/planner-runs archive absent)")
print("ALL OK")
