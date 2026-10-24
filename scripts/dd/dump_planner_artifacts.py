"""Dump the stored Planner artifacts of every framework to JSON-lines on stdout,
for offline replay (scripts/dd/replay_select.py). Read-only.

Run inside a server pod (it needs MinIO access), redirecting stdout locally:

    kubectl -n coelhonexus exec -i deploy/coelhonexus-server -c coelhonexus-server -- \
        python - < scripts/dd/dump_planner_artifacts.py > planner_artifacts.jsonl

One line per framework: {slug, manifest, distillates, proposals, assignments, plan}.
Frameworks with no completed chapter_assign blob are skipped.
"""
import asyncio
import json
import sys

import domains


async def _read(minio, key):
    try:
        return json.loads(await minio.read_text(key))
    except Exception:
        return None


async def main() -> None:
    minio = domains.dd.ingestion.storage.service.get_storage()
    nodes = domains.dd.planner.nodes
    slugs = sorted(
        s.rstrip("/").split("/")[-1] for s in await minio.list_subfolders("planner/")
    )
    for slug in slugs:
        assign = await _read(minio, nodes.chapter_assign.keys.latest_key(slug))
        if not assign or not assign.get("assignments"):
            continue
        row = {
            "slug": slug,
            "manifest": await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug),
            "distillates": ((await _read(minio, nodes.doc_distill.keys.latest_key(slug))) or {}).get("distillates") or {},
            "proposals": ((await _read(minio, nodes.chapter_propose.keys.latest_key(slug))) or {}).get("proposals") or [],
            "assignments": assign["assignments"],
            "plan": await _read(minio, nodes.chapter_select.keys.select_latest_key(slug)),
        }
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        sys.stdout.flush()


asyncio.run(main())
