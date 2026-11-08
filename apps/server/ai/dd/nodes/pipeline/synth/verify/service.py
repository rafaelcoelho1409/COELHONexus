"""Verify canonical chapter examples and generated code, and persist an honest status report."""
from __future__ import annotations
import ai, domains
from ..... import graphs

import hashlib
import json
import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)
Context = graphs.pipeline.synth.entities.SynthContext


async def verify_code(state: graphs.pipeline.synth.states.SynthState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return graphs.pipeline.synth.service.failure(
            "verify_path", "verify_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )

    digest_key = ai.dd.nodes.pipeline.synth.ledger_prepare.keys.latest_blob_key(slug, chapter_id)
    sawc_key = ai.dd.nodes.pipeline.synth.sawc.keys.latest_blob_key(slug, chapter_id)
    digest_payload = await domains.dd.pipeline.storage.service.load_json(blobs, digest_key)
    sawc_payload = await domains.dd.pipeline.storage.service.load_json(blobs, sawc_key)
    if digest_payload is None or sawc_payload is None:
        return graphs.pipeline.synth.service.failure(
            "verify_path", "verify_stats", "inputs_unreadable", "ledger digest or written chapter is missing", started,
            digest_path = digest_key, sawc_path = sawc_key,
        )

    ledger = await domains.dd.pipeline.ledger.service.build(blobs, slug, sorted(digest_payload.get("source_keys") or []))
    blocks_by_hash: dict[str, tuple[str, str]] = {
        digest: (ledger.blocks[digest].lang, ledger.blocks[digest].body)
        for digest in ledger.must_teach
        if digest in ledger.blocks
    }
    capstone_prefix = ai.dd.nodes.pipeline.synth.sawc_derive.params.CAPSTONE_PREFIX
    for section in sawc_payload.get("sections") or []:
        is_capstone = str(section.get("heading") or "").startswith(capstone_prefix)
        for subtopic in section.get("subtopics") or []:
            subtopic = subtopic or {}
            code = (subtopic.get("derived_code") or "").strip()
            if code:
                digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
                blocks_by_hash[digest] = ("python", code)
            elif is_capstone:
                continue

    verify_domain = domains.dd.pipeline.verify
    environment = verify_domain.entities.Environment(known = frozenset({"deepagents", "langgraph"}))
    report = await verify_domain.service.verify_blocks(
        [(digest, lang, body) for digest, (lang, body) in blocks_by_hash.items()],
        environment,
        context.executor,
    )
    results = [
        {"hash": result.hash, "lang": result.lang, "level": result.level, "ok": result.ok, "status": result.status, "issues": list(result.issues)}
        for result in report.results
    ]
    summary = report.summary()
    status = "failed" if summary["failed"] else "unverified" if summary["unverified"] or not summary["total"] else "verified"
    verification_manifest = hashlib.sha256(json.dumps(results, sort_keys = True).encode("utf-8")).hexdigest()[:16]
    latest_key = ai.dd.graphs.pipeline.synth.keys.latest_key(slug, chapter_id, "verification")
    versioned_key = f"dd/{slug}/synth/{chapter_id}/verification/{verification_manifest}.json"
    payload = {
        "chapter_id": chapter_id,
        "framework_slug": slug,
        "manifest_hash": verification_manifest,
        "summary": summary,
        "execution_enabled": context.executor is not None,
        "required_hashes": list(ledger.must_teach),
        "results": results,
    }
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await blobs.write(versioned_key, body, content_type = "application/json")
    await blobs.write(latest_key, body, content_type = "application/json")
    elapsed = graphs.service.elapsed_ms(started)
    stats = {
        **summary,
        "status": status,
        "execution_enabled": context.executor is not None,
        "manifest_hash": verification_manifest,
        "store_path": latest_key,
        "versioned_path": versioned_key,
        "wall_ms": elapsed,
        "results": results,
    }
    graphs.service.emit(
        "verify", "done", chapter_id = chapter_id, status = stats["status"], verified = summary["verified"],
        unverified = summary["unverified"], failed = summary["failed"], execution_enabled = stats["execution_enabled"], wall_ms = elapsed,
    )
    logger.info(
        f"[verify] {slug}/{chapter_id}: verified={summary['verified']} unverified={summary['unverified']} failed={summary['failed']} "
        f"execution_enabled={stats['execution_enabled']} ({elapsed} ms)"
    )
    return {"verify_path": latest_key, "verify_stats": stats}
