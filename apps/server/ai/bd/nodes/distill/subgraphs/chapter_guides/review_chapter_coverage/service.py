"""Bounded coverage review with source-grounded chapter corrections."""
from __future__ import annotations
import domains
from ...... import agents, graphs
from .. import synthesize_chapter

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any


logger = logging.getLogger(__name__)


async def _invoke_with_heartbeat(
    dependencies: graphs.distill.entities.DistillDependencies,
    task_label: str,
    invoke: Callable[[], Awaitable[Any]],
) -> Any:
    """Keep long model calls visible and cancellable while they await the provider."""
    await graphs.service.report_node_activity(
        dependencies.progress_callback,
        "review_chapter_coverage",
        task_label,
    )
    request = asyncio.create_task(invoke())
    elapsed_s = 0.0
    try:
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(request),
                    timeout = domains.bd.distill.ai.params.COVERAGE_REVIEW_HEARTBEAT_S,
                )
            except TimeoutError:
                if request.done():
                    return await request
                elapsed_s += domains.bd.distill.ai.params.COVERAGE_REVIEW_HEARTBEAT_S
                await graphs.service.report_node_activity(
                    dependencies.progress_callback,
                    "review_chapter_coverage",
                    f"{task_label} · waiting {int(elapsed_s)}s",
                )
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions = True)


def _review_packet(
    chapter_input: dict[str, Any],
    synthesis: domains.bd.distill.ai.schemas.ChapterSynthesis,
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "section": {
            key: chapter_input.get(key)
            for key in ("title", "section_path", "first_page", "last_page", "subsection_paths")
        },
        "verified_learning_units": [
            {
                "learning_unit_id": unit.get("learning_unit_id"),
                "kind": unit.get("kind"),
                "title": unit.get("title"),
                "claim": str(unit.get("claim", ""))[:900],
                "explanation": str(unit.get("explanation", ""))[:900],
                "evidence": [
                    {"page_number": item.get("page_number"), "quote": str(item.get("quote", ""))[:400]}
                    for item in unit.get("evidence", [])[:2]
                ],
            }
            for unit in units
        ],
        "chapter_guide": synthesis.model_dump(mode = "json"),
    }


async def _invoke_reviewer(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    packet: dict[str, Any],
    chapter_label: str,
) -> domains.bd.distill.ai.schemas.ReportReviewOutput:
    return await _invoke_with_heartbeat(
        dependencies,
        f"Reviewing chapter coverage · {chapter_label}",
        lambda: agents.distill.report_reviewer.service.review_packet(
            dependencies.report_reviewer,
            packet,
            run_id = run_id,
            context_kind = "chapter",
        ),
    )


async def review_chapter_coverage(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    ref = state["chapter_ref"]
    await graphs.service.report_node_activity(
        dependencies.progress_callback,
        "review_chapter_coverage",
        f"Preparing chapter coverage review · {ref['chapter_id'][:8]}",
    )
    storage = dependencies.storage
    artifact = json.loads(await storage.read_text(ref["artifact_key"]))
    chapter_input = json.loads(await storage.read_text(ref["input_key"]))
    synthesis = domains.bd.distill.ai.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
    units = await domains.bd.distill.ai.synthesize.service.read_learning_units(
        storage,
        state["book_id"],
        state["run_id"],
        ref["unit_ids"],
        ref.get("unit_sources") or chapter_input.get("unit_sources"),
    )
    chapter_label = str(chapter_input.get("title") or ref["chapter_id"][:8])[:120]

    def make_packet_hash(packet: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(packet, ensure_ascii = False, sort_keys = True, separators = (",", ":")).encode("utf-8"),
        ).hexdigest()

    packet = _review_packet(chapter_input, synthesis, units)
    packet_sha256 = make_packet_hash(packet)
    # Resume and task retries reuse a saved result for identical guide input. Explicit
    # node replay clears these fields so a user can deliberately request a new audit.
    saved_review_status = artifact.get("coverage_review_status")
    if (
        artifact.get("coverage_review_input_sha256") == packet_sha256
        and isinstance(saved_review_status, str)
        and saved_review_status in {"assistant_reviewed", "review_required", "unavailable"}
    ):
        chapter_label = str(chapter_input.get("title") or ref["chapter_id"][:8])[:120]
        await graphs.service.report_node_activity(
            dependencies.progress_callback,
            "review_chapter_coverage",
            f"Using saved coverage review · {chapter_label}",
        )
        return {"reviewed_chapters": [ref]}

    review_notes: list[str] = []
    review_status = "review_required"
    review_history = list(artifact.get("coverage_review_history", []))
    correction_count = artifact.get("coverage_review_correction_count", 0)
    if type(correction_count) is not int or correction_count < 0:
        correction_count = 0
    converged = False

    while True:
        packet = _review_packet(chapter_input, synthesis, units)
        packet_sha256 = make_packet_hash(packet)
        try:
            review = await _invoke_reviewer(dependencies, state["run_id"], packet, chapter_label)
        except Exception as error:  # noqa: BLE001 — review is a quality aid, not a publication dependency.
            review_status = "unavailable"
            review_notes.append(f"Chapter coverage review was unavailable ({type(error).__name__}).")
            logger.warning(
                "[bd-distill] chapter coverage review unavailable chapter=%s error_type=%s error=%s",
                ref["chapter_id"],
                type(error).__name__,
                str(error)[:300],
            )
            break

        findings = [
            item for item in review.findings
            if item.area in domains.bd.distill.ai.params.COVERAGE_REVIEW_AREAS
        ]
        actionable = [item for item in findings if item.severity in {"warning", "critical"}]
        review_history.append({
            "round": len(review_history) + 1,
            "input_sha256": packet_sha256,
            "finding_count": len(findings),
            "actionable_count": len(actionable),
        })
        if not actionable:
            review_status = "assistant_reviewed"
            converged = True
            review_notes.extend(
                f"{item.severity}: {item.issue[:350]} Recommendation: {item.recommendation[:350]}"
                for item in findings[:6]
            )
            break

        if correction_count >= domains.bd.distill.ai.params.COVERAGE_REVIEW_MAX_CORRECTIONS:
            review_notes.extend(
                f"{item.severity}: {item.issue[:350]} Recommendation: {item.recommendation[:350]}"
                for item in actionable[:6]
            )
            review_notes.append(
                "Coverage review still found actionable issues after "
                f"{correction_count} correction pass(es); manual review is required."
            )
            break

        finding_payload = [
            {
                "severity": item.severity,
                "area": item.area,
                "issue": item.issue,
                "recommendation": item.recommendation,
            }
            for item in actionable[:6]
        ]
        before_sha256 = hashlib.sha256(
            json.dumps(synthesis.model_dump(mode = "json"), ensure_ascii = False, sort_keys = True).encode("utf-8"),
        ).hexdigest()
        try:
            revised, citation_flags = await _invoke_with_heartbeat(
                dependencies,
                f"Correcting chapter coverage · {chapter_label} · pass {correction_count + 1}",
                lambda: synthesize_chapter.service.revise_chapter_for_coverage(
                    dependencies,
                    state["run_id"],
                    ref["chapter_id"],
                    chapter_input,
                    synthesis,
                    units,
                    finding_payload,
                ),
            )
        except Exception as error:  # noqa: BLE001 — preserve the saved guide if optional correction fails.
            review_notes.extend(
                f"{item.severity}: {item.issue[:350]} Recommendation: {item.recommendation[:350]}"
                for item in actionable[:6]
            )
            review_notes.append(
                f"A coverage correction could not be completed ({type(error).__name__}); manual review is required."
            )
            logger.warning(
                "[bd-distill] chapter coverage correction failed chapter=%s error_type=%s error=%s",
                ref["chapter_id"],
                type(error).__name__,
                str(error)[:300],
            )
            break

        after_sha256 = hashlib.sha256(
            json.dumps(revised.model_dump(mode = "json"), ensure_ascii = False, sort_keys = True).encode("utf-8"),
        ).hexdigest()
        if after_sha256 == before_sha256:
            review_notes.extend(
                f"{item.severity}: {item.issue[:350]} Recommendation: {item.recommendation[:350]}"
                for item in actionable[:6]
            )
            review_notes.append("A coverage correction produced no guide changes; manual review is required.")
            break

        synthesis = revised
        correction_count += 1
        artifact.update({
            "synthesis": synthesis.model_dump(mode = "json"),
            "citation_review_flags": list(dict.fromkeys([
                *artifact.get("citation_review_flags", []),
                *citation_flags,
            ])),
            "visual_review_flags": [],
            "visuals_status": "pending",
            "coverage_review_history": review_history,
            "coverage_review_correction_count": correction_count,
        })
        for field in ("coverage_review_status", "coverage_review_notes", "coverage_review_input_sha256"):
            artifact.pop(field, None)
        await storage.write(
            ref["artifact_key"],
            domains.bd.distill.ai.domain.json_body(artifact),
            content_type = domains.bd.distill.ai.params.JSON_TYPE,
        )

    if converged and not review_notes:
        review_notes.append(
            f"Coverage review converged after {correction_count} correction pass(es)."
        )

    await graphs.service.report_node_activity(
        dependencies.progress_callback,
        "review_chapter_coverage",
        f"Saving coverage review · {chapter_label}",
    )
    artifact.update({
        "coverage_review_status": review_status,
        "coverage_review_notes": list(dict.fromkeys(review_notes))[:8],
        "coverage_review_input_sha256": packet_sha256,
        "coverage_review_history": review_history,
        "coverage_review_correction_count": correction_count,
        "coverage_review_converged": converged,
    })
    await storage.write(
        ref["artifact_key"],
        domains.bd.distill.ai.domain.json_body(artifact),
        content_type = domains.bd.distill.ai.params.JSON_TYPE,
    )
    return {"reviewed_chapters": [ref]}
