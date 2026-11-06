"""Export the replay fixture for test_bd_replay_eval.py from a finished BD agentic run. Runs inside a Celery pod (it reads MinIO):

    kubectl exec -i -n coelhonexus <celery-pod> -c coelhonexus-celery -- python - < scripts/server/export_bd_replay_fixture.py \\
        | gzip -9 > scripts/server/data/bd_replay_workflow_1_10.json.gz

Set BOOK, RUN (agentic run) and EXT (Docling extraction) below. Only pages with specialist proposals or review items are kept.
"""
import asyncio
import json

import domains
import infra

BOOK = "4c6c6327360f7cf5"
RUN = "b571712f8f094422a066e3933e8bd34a"
EXT = "c94a1d6883c04bb0a9b9d339f85348f4"


async def main() -> None:
    storage = infra.minio.service.get_storage()
    keys = domains.bd.ingestion.storage.keys
    proposal_keys = await storage.list(keys.agentic_prefix(BOOK, RUN) + "patch-proposals/")
    pages = []
    for key in sorted(proposal_keys):
        record = json.loads(await storage.read_text(key))
        number = record["page_number"]
        if not record["proposals"] and not record["review_required_issue_ids"]:
            continue
        assessment = json.loads(await storage.read_text(keys.agentic_assessment_key(BOOK, RUN, number)))
        source = await storage.read_text(f"bd/books/{BOOK}/ingestion/raw/{EXT}/pages/{number:04d}.md")
        pages.append({
            "page": number,
            "source": source,
            "issues": assessment["issues"],
            "proposals": record["proposals"],
            "auto_applied": record["auto_applied_issue_ids"],
            "review_required": record["review_required_issue_ids"],
            "mechanical": record.get("mechanical_fixes", {}),
        })
    print(json.dumps(
        {"book": BOOK, "agentic_run": RUN, "workflow": "1.10", "pages": pages},
        ensure_ascii = False,
    ))


asyncio.run(main())
