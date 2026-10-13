"""Offline END-TO-END runs of the chapter_select and order_chapters nodes on stored artifacts, with an
in-memory MinIO and a fake LLM (no cluster, no Rotator): proves the node wiring, not just the helpers.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_run_5.py planner_artifacts.jsonl

For every stored framework: chapter_select_run (cluster split named by a fake model, appendix split
included) then order_chapters_run (title-order samples from a fake model that reads the stages)."""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
chat = domains.settings.chat


class FakeMinio:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def exists(self, key):
        return key in self.store

    async def read_text(self, key):
        if key not in self.store:
            raise FileNotFoundError(key)
        return self.store[key]

    async def write(self, key, data, content_type=None):
        self.store[key] = data if isinstance(data, str) else data.decode()


async def _noop(*a, **k):
    return None


async def _settle(cap):
    return {"waited_s": 0, "reason": "test"}


def patch(row, minio):
    domains.dd.ingestion.storage.service.get_storage = lambda: minio
    async def _manifest(m, slug):
        return row["manifest"]
    domains.dd.ingestion.storage.service.read_framework_manifest = _manifest
    domains.settings.chat.service.wait_for_rotator_settle = _settle
    domains.dd.planner.runtime.progress.service.emit_progress = _noop

    async def _dist(m, slug):
        return row["distillates"]
    nodes.doc_distill.service.load_distillates = _dist


async def run_one(row):
    slug = row["slug"]
    minio = FakeMinio()
    patch(row, minio)
    proposals_ref, assignments_ref = f"planner/{slug}/proposals.json", f"planner/{slug}/assign.json"
    minio.store[proposals_ref] = json.dumps({"seeds": {}})

    async def _props(m, s):
        return nodes.chapter_propose.schemas.ChapterProposalList.model_validate({"proposals": row["proposals"]})
    async def _assign(m, s):
        return row["assignments"]
    nodes.chapter_propose.service.load_proposals = _props
    nodes.chapter_assign.service.load_assignments = _assign

    named: list[str] = []
    async def _fake(prompt, schema, **kw):
        name = schema.__name__
        if name == "GroupNames":
            ids = [int(i) for i in re.findall(r"^\[(\d+)\] \d+ pages", prompt, re.M)]
            named.extend(str(i) for i in ids)
            title = re.search(r"oversize chapter, '(.*?)'", prompt).group(1)
            return schema(groups=[{"id": i, "title": f"{title[:18].strip()} Part {i + 1}", "description": "Pages that belong together."} for i in ids]), {}
        raise AssertionError(f"unexpected LLM call {name}")
    chat.service.chat_structured_async = _fake

    state = {"framework_slug": slug, "thread_id": "t", "chapter_proposals_ref": proposals_ref,
             "chapter_doc_assignments_ref": assignments_ref}
    out = await nodes.chapter_select.service.chapter_select_run(state)
    st = out["select_stats"]
    plan = json.loads(minio.store[out["chapter_plan_ref"]])
    chapters = plan["outline"]["chapters"]
    n_docs = sum(len(c["member_doc_keys"]) for c in chapters)
    assert n_docs == len(row["assignments"]), (slug, n_docs, len(row["assignments"]))
    assert len({c["title"].casefold() for c in chapters}) == len(chapters), "duplicate chapter titles"
    sizes = [len(c["member_doc_keys"]) for c in chapters]
    print(f"  select {slug}: {len(chapters)} chapters, largest {round(100 * max(sizes) / n_docs)}%, "
          f"splits {st['consolidation'].get('n_split_applied')}/{st['consolidation'].get('n_split_planned')}, "
          f"sizes {sorted(sizes, reverse=True)[:8]}")

    # ---- order: a fake model that orders by stage keywords (install first, migration last, reference late)
    async def _order(prompt, schema, **kw):
        assert schema.__name__ == "ChapterTitleOrder"
        titles = re.findall(r"^- (.*?) — ", prompt, re.M)
        assert len(titles) == len(chapters), (len(titles), len(chapters))
        rank = lambda t: (0 if re.search(r"install|setup|quickstart", t, re.I) else 2 if re.search(r"migrat|upgrad", t, re.I) else 1, titles.index(t))
        return schema(rationale="fake", order=sorted(titles, key=rank)), {"deployment": "fake"}
    chat.service.chat_structured_async = _order
    plan_ref = out["chapter_plan_ref"]
    res = await nodes.order_chapters.service.order_chapters_run({"framework_slug": slug, "thread_id": "t", "chapter_plan_ref": plan_ref})
    blob = json.loads(minio.store[res["chapter_order_ref"]])
    order = blob["order"]
    assert sorted(order) == list(range(len(chapters))), order
    titles = [c["title"] for c in chapters]
    mig = [i for i, t in enumerate(titles) if re.search(r"migrat|upgrad", t, re.I)]
    if mig:
        assert order[-len(mig):] == [i for i in order if i in mig] or all(order.index(m) > max(order.index(i) for i in range(len(titles)) if i not in mig and chapters[i].get("role") != "appendix") for m in mig), (slug, [titles[i] for i in order])
    app = [i for i, c in enumerate(chapters) if c.get("role") == "appendix"]
    if app:
        assert order[-1] == app[0], (slug, [titles[i] for i in order])
    print(f"  order  {slug}: {len(blob['samples'])}/5 samples, trailing pinned {blob.get('trailing_idx')}, first -> last: "
          f"{titles[order[0]]!r} ... {titles[order[-1]]!r}")


async def main(path):
    for line in open(path):
        await run_one(json.loads(line))
    print("ALL OK")

asyncio.run(main(sys.argv[1]))
