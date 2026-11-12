"""mgsr — pure helpers: the trivial pass, the reviewer's decision (validated against the outline), the fallback and the manifest hash."""
from __future__ import annotations
from . import params, schemas, versions

import hashlib


def is_trivial_pass(checklist: dict) -> bool:
    """True when the chapter already passed at the threshold; it is not reviewed."""
    if not checklist:
        return False
    if not bool(checklist.get("chapter_passed", False)):
        return False
    return float(checklist.get("pass_rate", 0.0)) >= params.PASS_RATE_TRIVIAL


def build_trivial_pass_decision(pass_rate: float) -> schemas.MGSRDecision:
    """The halt decision for the trivial-pass case."""
    return schemas.MGSRDecision(
        halt = True,
        halt_reason = "chapter_passed",
        confidence = 1.0,
        rationale_overall = (
            f"Chapter passed the checklist with pass_rate {pass_rate:.2%} ≥ {params.PASS_RATE_TRIVIAL:.0%}. "
            f"No rewrite needed; remaining failed criteria (if any) are acceptable below the chapter-pass threshold."
        ),
    )


def fallback_decision(reason: str) -> schemas.MGSRDecision:
    """The reviewer failed irrecoverably: the loop goes on and rewrites the whole chapter, as it did before reviewers existed."""
    return schemas.MGSRDecision(
        halt = False,
        halt_reason = "reviewer_unavailable",
        confidence = 0.0,
        rationale_overall = f"Reviewer unavailable: {reason}. The chapter is rewritten whole from the checklist feedback.",
    )


def reviewer_decision(
    *, halt: bool, confidence: float, rationale: str, revisions: list[dict], known_section_ids: set[str],
) -> tuple[schemas.MGSRDecision, list[str]]:
    """The decision a reviewer's answer amounts to, plus what was dropped from it. A section id that is not in the outline, a repeated one and an
    instruction outside the length bounds are dropped; at most MAX_REVISIONS are kept. A halt below HALT_MIN_CONFIDENCE is a whole-chapter rewrite."""
    kept: list[schemas.Revision] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in revisions:
        sid = str((item or {}).get("section_id") or "").strip()
        instruction = " ".join(str((item or {}).get("instruction") or "").split())
        if sid not in known_section_ids or sid in seen:
            dropped.append(f"{sid or '?'}: not a section of this outline" if sid not in known_section_ids else f"{sid}: repeated")
            continue
        if not (params.INSTRUCTION_MIN_CHARS <= len(instruction) <= params.INSTRUCTION_MAX_CHARS):
            dropped.append(f"{sid}: instruction of {len(instruction)} chars")
            continue
        seen.add(sid)
        kept.append(schemas.Revision(section_id = sid, instruction = instruction))
        if len(kept) >= params.MAX_REVISIONS:
            break
    confidence = max(0.0, min(1.0, float(confidence)))
    rationale = " ".join((rationale or "").split())[:params.RATIONALE_MAX_CHARS]
    if halt and not kept and confidence >= params.HALT_MIN_CONFIDENCE:
        return schemas.MGSRDecision(halt = True, halt_reason = "reviewer_halt", confidence = confidence, rationale_overall = rationale), dropped
    return schemas.MGSRDecision(
        halt = False,
        halt_reason = "targeted_revision" if kept else "full_rewrite",
        confidence = confidence,
        revisions = kept,
        rationale_overall = rationale,
    ), dropped


def section_index(sections: list[dict]) -> list[dict]:
    """What the reviewer sees of each section before it opens one: counts and the validator's issues."""
    return [
        {
            "section_id": s.get("section_id"),
            "heading": s.get("heading"),
            "n_subtopics": len(s.get("subtopics") or []),
            "n_code_blocks": sum(1 for st in (s.get("subtopics") or []) if (st or {}).get("code_ref_hash")),
            "n_citations": len(s.get("citations") or []),
            "issues": list(s.get("issues") or []),
        }
        for s in sections
    ]


def section_text(section: dict) -> str:
    """One section as the reviewer reads it: intro, each subtopic (subheading, explanation, the code it prints by hash), citations and issues."""
    lines = [f"## {section.get('heading')} ({section.get('section_id')})", section.get("intro") or ""]
    for st in section.get("subtopics") or []:
        st = st or {}
        code = f"code {st.get('code_ref_hash')}" if st.get("code_ref_hash") else ("derived code" if st.get("code_source") == "derived" else "no code")
        lines.append(f"\n### {st.get('subheading')}  [{code}]\n{st.get('explanation') or ''}")
    cites = [f"- {c.get('source_key')}: {c.get('claim')}" for c in section.get("citations") or []]
    if cites:
        lines.append("\nCitations:\n" + "\n".join(cites))
    if section.get("issues"):
        lines.append("\nValidator issues: " + "; ".join(section["issues"]))
    return "\n".join(lines)


def compute_manifest_hash(*, checklist_manifest_hash: str, outline_manifest_hash: str) -> str:
    payload = (
        f"checklist={checklist_manifest_hash}|"
        f"outline={outline_manifest_hash}|"
        f"prompt={versions.MGSR_PROMPT_VERSION}|"
        f"schema={versions.MGSR_SCHEMA_VERSION}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
