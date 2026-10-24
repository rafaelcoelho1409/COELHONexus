"""Offline checks for the 2026-10-14 batch-7 fixes (no LLM, no cluster): trunk-directory breakdown in
SOURCE STRUCTURE, weak-batch propose retry, off_topic second opinion, directory-aware part-count /
clustering of oversize and catch-all splits, orientation micro-chapters folded into Foundations.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_planner_fixes_6.py [planner_artifacts.jsonl ...]

With dumps from dump_planner_artifacts.py (several files allowed) it also replays the selection on every
stored framework and checks the split groups."""
import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
SEL = nodes.chapter_select.domain
PR, OT = nodes.chapter_propose, nodes.off_topic
chat = domains.settings.chat


def ent(i, url, title=None):
    return {"idx": i, "key": f"ingestion/fw/pages/{i:04d}-{url.rsplit('/', 1)[-1]}.md", "url": url, "source_path": "",
            "title": title or url.rsplit("/", 1)[-1]}


# 1) trunk directory: listed with module-prefix groups; topic-word directories are not -------------------------
guides = [ent(i, f"https://x.dev/guides/topic-{w}") for i, w in enumerate("alpha beta gamma delta epsilon zeta eta theta".split())]
sdk = [ent(100 + i, f"https://x.dev/api/fw-{pkg}-{mod}") for i, (pkg, mod) in enumerate(
    [("server", f"auth-m{j}") for j in range(20)] + [("client", f"t-m{j}") for j in range(14)] + [("cli", f"c{j}") for j in range(8)])]
st = PR.domain.build_source_structure(guides + sdk)
assert "api (42 pages, grouped by name prefix):" in st, st
assert "fw-server (20 pages)" in st and "fw-client (14 pages)" in st and "fw-cli (8 pages)" in st, st
words = [ent(i, f"https://x.dev/ref/{w}-page") for i, w in enumerate(
    "acp ag async backends comparison content context data deep dynamic event fault going human interpreters memory models quickstart sandbox skills streaming tools "
    "profiles permissions customization middleware interrupts".split())]
st2 = PR.domain.build_source_structure(guides + words * 1)
assert "grouped by name prefix" not in st2, st2                                 # slugs are topic words: prefixes say nothing
flat = [ent(i, f"https://x.dev/lib/mod-{i}") for i in range(30)]
assert PR.domain.build_source_structure(flat) == ""                              # a single directory: nothing to report
print("1 trunk directory breakdown ok")

# 2) weak-batch retry: best evidence score < WEAK_BEST_SCORE -> 3 more samples; a strong batch draws none ------
class FakeMinio:
    def __init__(self):
        self.store = {}
    async def exists(self, key):
        return key in self.store
    async def read_text(self, key):
        if key not in self.store:
            raise FileNotFoundError(key)
        return self.store[key]
    async def write(self, key, data, content_type=None):
        self.store[key] = data if isinstance(data, str) else data.decode()
    async def read_many(self, keys):
        return ["body"] * len(keys)

async def _noop(*a, **k):
    return None
async def _settle(cap):
    return {"waited_s": 0, "reason": "test"}
S = PR.schemas

def props(titles_concepts):
    return S.ChapterProposalList(proposals=[
        S.ChapterProposal(title=t, description=f"Covers {t} of the framework in depth.", key_concepts=list(c)) for t, c in titles_concepts])

DOCS = {f"k{i}": {"summary": f"{w} handling and {w} configuration reference", "key_terms": [w, f"{w}-api"]}
        for i, w in enumerate(["alpha", "beta", "gamma", "delta", "epsilon", "zeta"] * 5)}

async def run_propose(sample_for):
    minio = FakeMinio()
    domains.dd.ingestion.storage.service.get_storage = lambda: minio
    async def _manifest(m, slug):
        return {"entries": []}
    domains.dd.ingestion.storage.service.read_framework_manifest = _manifest
    chat.service.wait_for_rotator_settle = _settle
    domains.dd.planner.runtime.progress.service.emit_progress = _noop
    async def _dist(m, slug):
        return DOCS
    nodes.doc_distill.service.load_distillates = _dist
    called = []
    async def _draft(prompt, idx, failures=None, max_tokens=None):
        called.append(idx)
        return sample_for(idx)
    PR.service.draft_one = _draft
    async def _vote(*a, **k):
        return 0
    PR.service.usc_pick = _vote
    state = {"framework_slug": "fw", "thread_id": "t", "relevant_files": list(DOCS), "doc_distill_ref": "x"}
    out = await PR.service.chapter_propose_run(state)
    return called, out["propose_stats"]

weak = props([("Zzz Unrelated One", ["qqq", "qqq-api"]), ("Zzz Unrelated Two", ["www", "www-api"]), ("Zzz Unrelated Three", ["eee", "eee-api"]), ("Zzz Unrelated Four", ["rrr", "rrr-api"])])
strong = props([(f"{w.title()} Handling", [w, f"{w}-api"]) for w in ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]])
PR.params.WEAK_RETRY_SAMPLES = 3
called, stats = asyncio.run(run_propose(lambda idx: weak if idx < 10 else strong))
assert sorted(called[:5]) == [0, 1, 2, 3, 4] and sorted(called[5:]) == [21, 22, 23], called        # angles 1-3, retry index space 20+
assert stats["n_samples_valid"] == 8 and stats["candidate_scores"] and max(s_["score"] for s_ in stats["candidate_scores"]) > 0.8, stats
called, stats = asyncio.run(run_propose(lambda idx: strong))
assert len(called) == 1 or sorted(called) == [0, 1, 2, 3, 4], called                                 # strong batch: no retry round
called, stats = asyncio.run(run_propose(lambda idx: weak if idx < 10 else None))
assert sorted(called[5:]) == [21, 22, 23] and stats["n_samples_valid"] == 5, (called, stats)        # failed retries do not break the run
called, stats = asyncio.run(run_propose(lambda idx: None if idx in (1, 3) else (weak if idx < 10 else strong)))
assert sorted(called[:5]) == [0, 1, 2, 3, 4] and sorted(called[5:]) == [11, 13, 21, 22, 23], called   # top-up + weak retry = ONE round (5 drafts)
called, stats = asyncio.run(run_propose(lambda idx: None if idx in (1, 2, 3) else (weak if idx < 10 else strong)))
assert sorted(called[5:]) == [11, 12, 13], called                                                      # 3 of 5 lost: top up only, no weak retry on a struggling Rotator
print("2 weak-batch retry ok")

# 3) off_topic second opinion ----------------------------------------------------------------------------------
verdicts = [(False, "DROP", None, {}), (False, "DROP", None, {}), (True, "KEEP", None, {}), (False, "DROP", "err", {}), (False, "DROP", None, {})]
keys = ["x" * 2500, "y" * 100, "z" * 3000, "w" * 3000, "v" * 2600]
second = {"x" * 2500: True, "v" * 2600: False}
async def fake_judge(sem, name, cat, body, on_complete=None, summary=""):
    return (second[body], "KEEP" if second[body] else "DROP", None, {})
OT.service.judge_one = fake_judge
out, rechecked, rescued = asyncio.run(OT.service.recheck_drops(asyncio.Semaphore(2), "fw", "", keys, verdicts))
assert rechecked == [0, 4] and rescued == 1, (rechecked, rescued)           # short page (1) and errored verdict (3) are not re-judged
assert [v[0] for v in out] == [True, False, True, False, False], out        # KEEP wins; DROP+DROP stays dropped
print("3 off_topic second opinion ok")

# 4) split sizing: parts by pages, floor by part, chapter cap ---------------------------------------------------
def chap(title, n, **kw):
    return {"title": title, "description": "d", "member_doc_keys": [f"ingestion/fw/pages/{i:04d}-{title.lower().replace(' ', '-')}-{i}.md" for i in range(n)],
            "n_member_docs": n, "origin": "proposal", **kw}
chs = [chap("Integrations", 115)] + [chap(f"Topic {i}", 14) for i in range(11)]
plans = SEL.plan_splits(chs)
assert plans == [(0, 6)], plans                                              # 115 pages: round(115/20) = 6 parts (share cap alone said 2)
assert SEL.split_floor(115, 6) == 11 and SEL.split_floor(114, 8) == 8 and SEL.split_floor(100) == 15
app = [chap("Additional Topics", 114, role="appendix")] + [chap(f"T{i}", 15) for i in range(12)]
assert SEL.plan_splits(app) == [(0, 8)], SEL.plan_splits(app)
tight = [chap("Integrations", 115)] + [chap(f"Topic {i}", 3) for i in range(26)]
assert SEL.plan_splits(tight, max_chapters=30) == [(0, 4)], SEL.plan_splits(tight, max_chapters=30)   # 27 chapters: room for 3 more only
print("4 split sizing ok:", plans)

# 5) URL-directory similarity separates sibling directories even when summaries are noise -----------------------
entries = ([ent(i, f"https://x.dev/integrations/frameworks/p{i}") for i in range(14)]
           + [ent(20 + i, f"https://x.dev/integrations/model-providers/q{i}") for i in range(14)]
           + [ent(40 + i, f"https://x.dev/integrations/gateways/r{i}") for i in range(10)])
kd = SEL.page_dirs(entries)
assert kd[entries[0]["key"]] == ("integrations", "frameworks") and kd[entries[20]["key"]] == ("integrations", "model-providers")
assert SEL.page_dirs([{"key": "k", "url": "https://x.dev/llms-full.txt", "source_path": "https://x.dev/apps/architecture"}])["k"] == ("apps",)
rng = random.Random(3)
distl = {e["key"]: {"summary": " ".join(rng.choice(["tracing", "setup", "token", "client", "config", "model", "trace", "key"]) for _ in range(8)), "key_terms": [], "role": "reference"} for e in entries}
units = SEL.build_split_units([e["key"] for e in entries], anchored_family={}, doc_title={e["key"]: e["title"] for e in entries},
                              distillates=distl, key_idx={e["key"]: e["idx"] for e in entries}, key_dir=kd)
for u, e in zip(units, entries):                                             # stems carry no signal either: only the directory does
    u["slug"] = f"page-{rng.randint(0, 999)}"
texts = [SEL.unit_text(u, distl) for u in units]
groups = SEL.cluster_units(units, texts, 3, SEL.split_floor(len(units), 3))
dirs_per_group = [{units[j]["dir"][-1] for j in g} for g in groups]
assert len(groups) == 3 and all(len(d) == 1 for d in dirs_per_group), dirs_per_group
print("5 directory-aware clustering ok:", [len(g) for g in groups])

# 6) orientation micro-chapters fold into Foundations; others stay -------------------------------------------------
fnd = chap("Foundations and Diagnostics", 9, origin="foundations", role="foundations")
body = [chap("Evaluation", 30), chap("Langfuse Foundations", 3, origin="rescued"), chap("Quickstart: Tracing Your First Call", 4, origin="rescued"),
        chap("Troubleshooting and FAQ", 3, origin="rescued"), chap("Tiny Topic", 3, origin="rescued"), chap("Setup Guides", 12),
        chap("Migration and Upgrade Guides", 3, origin="migration")]
out, st = SEL.fold_micro_chapters([fnd] + body)
assert st["n_micro_folded"] == 3 and [c["title"] for c in out] == ["Foundations and Diagnostics", "Evaluation", "Tiny Topic", "Setup Guides", "Migration and Upgrade Guides"], [c["title"] for c in out]
assert out[0]["n_member_docs"] == 19 and len(out[0]["member_doc_keys"]) == 19
assert SEL.fold_micro_chapters(body)[1]["n_micro_folded"] == 0               # no Foundations chapter: nothing moves
print("6 micro-chapter fold ok")

# 6b) catalog directories pull their low-confidence strays; confident pages, split siblings and locked chapters stay -------
def keyp(i, d): return f"ingestion/fw/pages/{i:04d}-{d.replace('/', '-')}-{i}.md"
ks = {f"{n}": [keyp(100 * j + i, d) for i in range(cnt)] for j, (n, d, cnt) in enumerate(
    [("A", "integrations/frameworks", 12), ("B", "integrations/frameworks", 4), ("topic", "docs/observability", 10), ("memory", "docs/memory", 6)])}
home = {"title": "Framework Integrations", "origin": "split", "member_doc_keys": ks["A"], "n_member_docs": 12}
sib = {"title": "Framework Integrations 2", "origin": "split", "member_doc_keys": ks["B"], "n_member_docs": 4}
topical = {"title": "Tracing", "origin": "proposal", "source_proposal_idx": 0, "member_doc_keys": ks["topic"] + [keyp(900 + i, "integrations/frameworks") for i in range(3)], "n_member_docs": 13}
mem = {"title": "Memory", "origin": "proposal", "source_proposal_idx": 1, "member_doc_keys": ks["memory"] + [keyp(950, "integrations/frameworks")], "n_member_docs": 7}
kd6 = {k: tuple(k.split("-")[1:-1][:0]) or () for c in (home, sib, topical, mem) for k in c["member_doc_keys"]}
for c in (home, sib, topical, mem):
    for k in c["member_doc_keys"]:
        d = k.split("/")[-1].split("-", 1)[1].rsplit("-", 1)[0]
        kd6[k] = tuple(d.replace("integrations-frameworks", "integrations/frameworks").replace("docs-observability", "docs/observability").replace("docs-memory", "docs/memory").split("/"))
asg = {}
for k in topical["member_doc_keys"]:
    asg[k] = [{"chapter_idx": 0, "confidence": 0.9 if "docs" in k else 0.3}]       # the 3 integration strays: low confidence
asg[mem["member_doc_keys"][-1]] = [{"chapter_idx": 1, "confidence": 0.9}]          # a confident memory integration page stays
out, st = SEL.harmonize_catalog_dirs([home, sib, topical, mem], kd6, asg)
assert st["n_catalog_rehomed"] == 3 and len(out[0]["member_doc_keys"]) == 15 and len(out[1]["member_doc_keys"]) == 4, (st, [len(c["member_doc_keys"]) for c in out])
assert len(out[2]["member_doc_keys"]) == 10 and len(out[3]["member_doc_keys"]) == 7
print("6b catalog directory harmonization ok:", st)

# 6c) sibling `Family: part` chapters stay contiguous; pins untouched ---------------------------------------------------------
OC = nodes.order_chapters.domain
chs6 = [{"title": t, **kw} for t, kw in [
    ("Foundations and Diagnostics", {"origin": "foundations", "role": "foundations"}), ("Prompt Management: Core", {}), ("Evaluation: Scores", {}),
    ("Tracing", {}), ("Prompt Management: SDK", {}), ("Evaluation: Datasets", {}), ("Prompt Management: Automation", {}), ("Migration and Upgrade Guides", {"origin": "migration"})]]
new, moved = OC.group_siblings([0, 1, 2, 3, 4, 5, 6, 7], chs6)
assert new == [0, 1, 4, 6, 2, 5, 3, 7] and moved > 0, new
assert OC.group_siblings([0, 3, 7], chs6) == ([0, 3, 7], 0)
print("6c sibling chapters contiguous ok:", new)

# 6d) the naming prompt carries each group's URL sections -------------------------------------------------------------------------
gunits = [{"id": 0, "keys": ["a", "b", "c"], "label": "Trace A with Langfuse", "summary": "s", "dir": ("integrations", "frameworks")},
          {"id": 1, "keys": ["d"], "label": "Trace B with Langfuse", "summary": "s", "dir": ("integrations", "gateways")}]
pp = SEL.__class__ and nodes.chapter_select.prompts.build_name_prompt(framework="fw", title="Integrations", groups=[[0], [1]], units=gunits, other_titles=[])
assert "URL sections: integrations/frameworks x3" in pp and "URL sections: integrations/gateways x1" in pp and "name what that section IS" in pp, pp
print("6d naming prompt ok")

# 7) replay on stored frameworks --------------------------------------------------------------------------------------
import replay_select as R  # noqa: E402
import collections
n_rows = 0
for path in sys.argv[1:]:
    for line in open(path):
        row = json.loads(line)
        out = R.run_pipeline(row)
        chs_ = out["out_chapters"]
        by = {e["key"]: e for e in row["manifest"]["entries"]}
        kd_ = SEL.page_dirs(row["manifest"]["entries"])
        for i, k in SEL.plan_splits(chs_):
            ch = chs_[i]
            n = len(ch["member_doc_keys"])
            us = SEL.build_split_units(ch["member_doc_keys"], anchored_family=SEL.anchored_family_map(row["manifest"]["entries"]),
                                       doc_title={kk: v["title"] for kk, v in by.items()}, distillates=row["distillates"],
                                       key_idx={kk: v.get("idx", 0) for kk, v in by.items()}, key_dir=kd_)
            tx = [SEL.unit_text(u, row["distillates"]) for u in us]
            gr = SEL.cluster_units(us, tx, k, SEL.split_floor(n, k))
            sizes = sorted((sum(len(us[j]["keys"]) for j in g) for g in gr), reverse=True)   # a whole URL directory may fill one part (up to SPLIT_MAX_DOCS)
            assert gr and len(gr) >= 2 and sizes[0] <= max((0.6 if k == 2 else 0.45) * n + 2, SEL.params.SPLIT_MAX_DOCS), \
                     (row["slug"], ch["title"], n, k, sizes)
            print(f"   {row['slug']}: {ch['title']!r} {n} pages k={k} -> {sizes}")
        n_rows += 1
if n_rows:
    print(f"7 replay ok on {n_rows} stored corpora")
print("ALL OK")
