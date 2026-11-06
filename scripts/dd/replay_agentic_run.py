"""Replay a saved agentic ingestion run (scripts/dd/dump_agentic_run.py) through today's audit and repair rules, with no model.

    cd apps/server && .venv/bin/python ../../scripts/dd/replay_agentic_run.py run.json [run2.json ...]

Per run it prints: the leads the page scans find, the stored findings today's sanitiser keeps, the stored accepted patches today's final gate
still accepts, and the size of the auditor's prompt. It is the regression bar for a change to the detectors, the prompts or the gates before
any model call is spent.
"""
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.dd import agents
from ai.dd.nodes.ingestion import page_audit, page_repair


def replay(path: str) -> None:
    files = json.loads(Path(path).read_text(encoding = "utf-8"))
    # The pages the run read (`inputs/pages/`), which patches apply to; the run's outputs (`pages/`) are not the source when a patch was applied.
    sources = "inputs/pages/" if any(name.startswith("inputs/pages/") for name in files) else "pages/"
    pages = {int(name[len(sources):len(sources) + 4]): text for name, text in files.items() if name.startswith(sources)}
    audits = {int(name[12:16]): json.loads(text) for name, text in files.items() if name.startswith("assessments/")}
    leads = collections.Counter()
    pages_with_leads = 0
    kept = dropped = accepted_now = rejected_now = payload_chars = 0
    reasons = collections.Counter()
    for idx, markdown in sorted(pages.items()):
        found = page_audit.detectors.candidates(markdown)
        pages_with_leads += bool(found)
        leads.update(lead["kind"] for lead in found)
        payload_chars += len(page_audit.domain.user_payload("slug", idx, "url", "title", markdown, found))
        audit = audits.get(idx)
        if audit is None:
            continue
        stored = audit["assessment"]
        kept_issues, notes = page_audit.domain.sanitize_assessment(stored, markdown = markdown)
        kept += len(kept_issues.issues)
        dropped += len(stored["issues"]) - len(kept_issues.issues)
        batch = {"patches": (audit.get("patches") or {}).get("accepted", [])}
        if batch["patches"]:
            _, report = page_repair.domain.apply_patches(
                markdown, hashlib.sha256(markdown.encode("utf-8")).hexdigest(), stored, batch,
            )
            accepted_now += len(report["accepted"])
            rejected_now += len(report["rejected"])
            reasons.update(item["reason"] for item in report["rejected"])
    prompt_chars = len(agents.ingestion.page_auditor.domain.system_prompt())
    print(f"{path}: {len(pages)} pages")
    print(f"  leads: {sum(leads.values())} on {pages_with_leads} pages {dict(leads)}")
    print(f"  stored findings kept by today's rules: {kept}, dropped: {dropped}")
    print(f"  stored accepted patches today's gate accepts: {accepted_now}, rejects: {rejected_now} {dict(reasons)}")
    print(f"  auditor prompt: {prompt_chars} chars; request per page: {payload_chars // max(len(pages), 1)} chars on average")


for argument in sys.argv[1:]:
    replay(argument)
