from __future__ import annotations

import asyncio
import json
import re

import numpy as np

from domains.llm.rotator.chain import chat_judge_bandit_async

from .constants import (
    _BLOB_PREFIX,
    _CTFIDF_DOC_CHARS,
    _JSON_RE,
    _K_MAX,
    _K_MIN,
    _KEYWORDS_PER_CLUSTER,
    _MAX_TOKENS_OUTLINE,
    _MISC_CHAPTER_TITLE,
    _REP_DOC_CHARS,
    _TEMPERATURE,
)


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapters/{manifest_hash}.json"


def _pick_rep_first_line(
    cluster_id: int,
    refined_assignments: np.ndarray,
    soft: np.ndarray,
    bodies: list[str],
) -> str:
    """First non-empty line of the in-cluster doc with highest soft
    membership for its (refined) cluster."""
    cluster_mask = refined_assignments == cluster_id
    if not cluster_mask.any():
        return ""
    if cluster_id < 0 or cluster_id >= soft.shape[1]:
        idx = int(np.where(cluster_mask)[0][0])
    else:
        membership = soft[:, cluster_id]
        masked = np.where(cluster_mask, membership, -np.inf)
        idx = int(np.argmax(masked))
    body = (bodies[idx] or "").strip()
    if not body:
        return ""
    for line in body.split("\n"):
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:_REP_DOC_CHARS]
    return body[:_REP_DOC_CHARS]


def _parse_response(text: str) -> dict | None:
    """Best-effort JSON extraction. Handles raw JSON + JSON wrapped in
    code-fences. Same pattern as refine/label."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _validate_outline(
    outline: dict, input_cluster_ids: set[int],
) -> tuple[list[int], list[int], list[int]]:
    """Return (missing_ids, duplicate_ids, unknown_ids)."""
    if not outline or not isinstance(outline, dict):
        return sorted(input_cluster_ids), [], []
    chapters = outline.get("chapters") or []
    seen: dict[int, int] = {}
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        for cid in (ch.get("member_cluster_ids") or []):
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            seen[cid_int] = seen.get(cid_int, 0) + 1
    seen_ids = set(seen.keys())
    missing = sorted(input_cluster_ids - seen_ids)
    duplicates = sorted([cid for cid, n in seen.items() if n > 1])
    unknown = sorted(seen_ids - input_cluster_ids)
    return missing, duplicates, unknown


def _build_cluster_context_block(
    cluster_id: int, label: str, size: int,
    keywords: list[str], rep_line: str,
) -> str:
    kw_str = ", ".join(keywords[:_KEYWORDS_PER_CLUSTER]) or "(no keywords)"
    line = (rep_line or "").strip().replace("\n", " ")[:_REP_DOC_CHARS]
    line_part = f' | first-line: "{line}"' if line else ""
    return (
        f"[cluster {cluster_id}] {label} (size {size}) — "
        f"keywords: {kw_str}{line_part}"
    )


def _build_reduce_prompt(cluster_blocks: list[str], target_k: int) -> str:
    blocks = "\n".join(cluster_blocks)
    return (
        f"You are a documentation architect organizing topical clusters "
        f"into a coherent chapter outline.\n\n"
        f"Below are {len(cluster_blocks)} clusters from a software "
        f"framework's documentation. Merge them into a chapter outline. "
        f"Produce {_K_MIN}-{_K_MAX} chapters, ideally ~{target_k}; only "
        f"deviate from {target_k} if the material genuinely demands it. "
        f"Every cluster MUST appear in exactly one chapter. Order "
        f"chapters intro → core → advanced.\n\n"
        f"CLUSTERS:\n{blocks}\n\n"
        f"Respond ONLY with valid JSON matching this schema:\n"
        f"{{\n"
        f'  "chapters": [\n'
        f'    {{"title": "<2-6 word Title Case noun phrase>", '
        f'"description": "<1 sentence>", '
        f'"member_cluster_ids": [<int>, ...], '
        f'"order": <1-based int>}},\n'
        f'    ...\n'
        f'  ],\n'
        f'  "assigned_cluster_ids": [<every cluster id used, sorted ascending>]\n'
        f"}}\n\n"
        f"Hard rules:\n"
        f"- Total chapters: between {_K_MIN} and {_K_MAX}.\n"
        f"- Coverage: every input cluster_id appears in EXACTLY ONE chapter "
        f"AND in `assigned_cluster_ids`.\n"
        f"- Chapter titles must be specific, 2-6 words, Title Case, "
        f"noun phrase (NOT verbs like 'Getting Started').\n"
        f"- Descriptions: 1 sentence summarizing what the chapter covers.\n"
        f"- If a noise/'Unclustered' cluster exists, merge into the most "
        f"thematically-close chapter; only keep as standalone "
        f'"{_MISC_CHAPTER_TITLE}" chapter when it has substantial unique '
        f"content.\n"
        f'Bad chapter titles (avoid): "Various Topics", "Information", '
        f'"Documentation", "Misc".\n'
        f'Good chapter titles: "Observability and Tracing", '
        f'"Agent Orchestration", "Authentication and Security".'
    )


def _build_usc_vote_prompt(
    samples: list[dict], input_cluster_ids: set[int],
) -> str:
    lines = []
    for i, s in enumerate(samples):
        chapters = (s or {}).get("chapters") or []
        title_list = "; ".join(
            f"{(c or {}).get('title', '?')} "
            f"({len((c or {}).get('member_cluster_ids') or [])} clusters)"
            for c in chapters
        )
        missing, dupes, unknown = _validate_outline(s, input_cluster_ids)
        coverage = (
            f"missing={len(missing)}, dup={len(dupes)}, "
            f"unknown={len(unknown)}"
        )
        lines.append(
            f"[{i}] {len(chapters)} chapters · coverage [{coverage}] · "
            f"{title_list}"
        )
    rubric = "\n".join(lines)
    return (
        "You are reviewing candidate chapter outlines for a documentation "
        "framework. From the following candidates, pick the SINGLE BEST "
        "one — best coverage (no missing/duplicate clusters), best "
        "coherence (related clusters grouped), best chapter naming.\n\n"
        f"Candidates:\n{rubric}\n\n"
        'Respond ONLY with valid JSON: {"chosen_index": <int>}'
    )


def _build_refine_feedback_prompt(
    outline: dict, input_cluster_ids: set[int],
) -> str:
    chapters = (outline or {}).get("chapters") or []
    title_list = "\n".join(
        f"- [order {(c or {}).get('order', '?')}] "
        f"{(c or {}).get('title', '?')}: "
        f"clusters {(c or {}).get('member_cluster_ids') or []} — "
        f"{(c or {}).get('description', '')}"
        for c in chapters
    )
    missing, dupes, unknown = _validate_outline(outline, input_cluster_ids)
    coverage = (
        f"missing IDs: {missing[:10]}; duplicate IDs: {dupes[:10]}; "
        f"unknown IDs: {unknown[:10]}"
    )
    return (
        "Review this chapter outline. Identify SPECIFIC problems:\n"
        "(a) any 2+ chapters that overlap >50% conceptually\n"
        "(b) any chapter holding clusters that don't belong together\n"
        "(c) ordering coherence (intro → core → advanced)\n"
        "(d) coverage issues (missing/duplicate/unknown cluster IDs)\n\n"
        f"Current outline:\n{title_list}\n\n"
        f"Coverage: {coverage}\n\n"
        "Return a concise list of ≤5 issues to fix (or 'no issues' if "
        "the outline is already good). 1 line per issue. Plain text, "
        "no JSON."
    )


def _build_refine_apply_prompt(
    outline: dict, feedback: str, cluster_blocks: list[str], target_k: int,
) -> str:
    blocks = "\n".join(cluster_blocks)
    return (
        f"Apply this feedback to improve the chapter outline. Keep the "
        f"same JSON schema; preserve good chapters; only change what "
        f"the feedback identifies as broken. Produce {_K_MIN}-{_K_MAX} "
        f"chapters, soft-target ~{target_k}.\n\n"
        f"CLUSTERS (full context):\n{blocks}\n\n"
        f"CURRENT OUTLINE:\n{json.dumps(outline, indent=2)}\n\n"
        f"FEEDBACK:\n{feedback}\n\n"
        f"Respond ONLY with valid JSON matching the original schema "
        f"(chapters + assigned_cluster_ids)."
    )


def _build_coverage_repair_prompt(
    outline: dict, missing: list[int], duplicates: list[int],
    unknown: list[int], cluster_blocks: list[str],
) -> str:
    blocks = "\n".join(cluster_blocks)
    issues = []
    if missing:
        issues.append(
            f"- {len(missing)} clusters are MISSING from the outline: "
            f"{missing}"
        )
    if duplicates:
        issues.append(
            f"- {len(duplicates)} clusters appear in MULTIPLE chapters: "
            f"{duplicates}"
        )
    if unknown:
        issues.append(
            f"- {len(unknown)} cluster IDs in the outline DO NOT EXIST in "
            f"the input: {unknown}"
        )
    issues_text = "\n".join(issues)
    return (
        f"Fix coverage issues in this chapter outline. EVERY cluster must "
        f"appear in EXACTLY ONE chapter. Only assign clusters that exist "
        f"in the input.\n\n"
        f"CLUSTERS (full context, only these IDs are valid):\n{blocks}\n\n"
        f"CURRENT OUTLINE:\n{json.dumps(outline, indent=2)}\n\n"
        f"ISSUES TO FIX:\n{issues_text}\n\n"
        f"Respond ONLY with valid JSON matching the original schema "
        f"(chapters + assigned_cluster_ids). Keep good chapters, only "
        f"modify what's needed to fix the coverage issues."
    )


async def _generate_one_outline(prompt: str) -> tuple[dict | None, dict]:
    try:
        response, meta = await chat_judge_bandit_async(
            prompt, max_tokens=_MAX_TOKENS_OUTLINE,
            temperature=_TEMPERATURE,
        )
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    parsed = _parse_response(response)
    if not parsed:
        return None, {**meta, "error": "parse_failed",
                      "raw": (response or "")[:120]}
    return parsed, {**meta}


def _force_coverage_fallback(
    outline: dict, missing: list[int], duplicates: list[int],
    unknown: list[int],
) -> dict:
    """Last-resort: dump missing into Miscellaneous, dedupe duplicates
    (keep first chapter's claim), drop unknowns. Called only after
    _MAX_REPAIR_RETRIES exhausted."""
    chapters = list((outline or {}).get("chapters") or [])
    seen_ids: set[int] = set()
    unknown_set = set(unknown)
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        deduped = []
        for cid in (ch.get("member_cluster_ids") or []):
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            if cid_int in unknown_set or cid_int in seen_ids:
                continue
            seen_ids.add(cid_int)
            deduped.append(cid_int)
        ch["member_cluster_ids"] = deduped
    chapters = [c for c in chapters if c.get("member_cluster_ids")]
    if missing:
        chapters.append({
            "title":              _MISC_CHAPTER_TITLE,
            "description":        (
                "Topics not assigned during automated chapter merging."
            ),
            "member_cluster_ids": list(missing),
            "order":              len(chapters) + 1,
        })
    for i, ch in enumerate(chapters, start=1):
        ch["order"] = i
    return {
        "chapters": chapters,
        "assigned_cluster_ids": sorted(
            cid for ch in chapters
            for cid in (ch.get("member_cluster_ids") or [])
        ),
    }


def load_outline(text: str) -> dict:
    """Convenience loader for downstream nodes (validate, plan_write)."""
    payload = json.loads(text)
    return payload.get("outline") or {}
