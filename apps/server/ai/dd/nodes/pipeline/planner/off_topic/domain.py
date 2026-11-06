"""off_topic pure helpers: the judge's request (head + tail of a page), reference-page detection, verdict aggregation."""
from __future__ import annotations
from . import params

import json


def _article(word: str) -> str:
    """a/an for the anchor sentence (categories like AI or Infrastructure need 'an')."""
    return "an" if (word or "")[:1].lower() in "aeiou" else "a"


def build_positive_descriptor(entry: dict, summary: str = "") -> str:
    """The anchor sentence for the framework, from the catalog name and category. `summary` (the Tier 2 llms.txt blockquote, ""
    otherwise) appends the author's own description. Reported in the node's stats only."""
    name = entry.get("name") or entry.get("slug") or "unknown"
    category = entry.get("category") or ""
    if category:
        base = (
            f"Documentation for {name}, {_article(category)} {category} library / framework. "
            f"Teaching content: tutorials, guides, API reference, how-to articles, conceptual explanations."
        )
    else:
        base = f"Documentation for {name}. Teaching content: tutorials, guides, API reference, how-to articles, conceptual explanations."
    if summary:
        base += f" About {name}: {summary}"
    return base


def head_tail_truncate(body: str) -> str:
    """Head + tail of a page: models attend most to the start and the end, so for a binary decision the middle is wasted
    attention (arXiv 2403.12799: head+tail beats head-only by 1-3 F1). The whole page when it fits both windows."""
    s = (body or "").strip()
    if not s:
        return "(empty page)"
    if len(s) <= params.JUDGE_BODY_MIN_FOR_SPLIT:
        return s
    return s[:params.JUDGE_HEAD_CHARS] + params.JUDGE_HEAD_TAIL_SEP + s[-params.JUDGE_TAIL_CHARS:]


def judge_payload(framework_name: str, category: str, summary: str, page: str) -> str:
    """The judge's request for one page (`page` is already `head_tail_truncate`d)."""
    return json.dumps(
        {"task": "Decide whether this page is teaching content for the framework.",
         "framework": framework_name, "category": category, "about": summary, "page": page},
        ensure_ascii = False,
    )


def reference_page_keys(entries: list[dict]) -> set[str]:
    """Doc keys that are virtual sub-pages of a reference page: they carry
    anchors, and their parent page (URL without fragment) produced at least
    REFERENCE_MIN_SIBLINGS such sub-pages. Pure; `entries` are manifest entries."""
    parent = lambda e: (e.get("url") or "").split("#", 1)[0]
    anchored = [e for e in entries if e.get("key") and e.get("anchors") and parent(e)]
    siblings: dict[str, int] = {}
    for e in anchored:
        siblings[parent(e)] = siblings.get(parent(e), 0) + 1
    return {
        e["key"] for e in anchored
        if siblings[parent(e)] >= params.REFERENCE_MIN_SIBLINGS
    }


def aggregate_verdicts(
    *,
    verdicts: list[tuple[bool, str, str | None, dict]],
    raw_files: list[str],
) -> dict:
    """Fan judge verdicts (in raw_files order) into judge_decisions +
    keep/drop stats + per-deployment usage/reward summary + error
    breakdown. Pure — no I/O, no logging."""
    judge_decisions: list[dict] = []
    judge_errors: list[str] = []
    deployment_usage: dict[str, int] = {}
    keep_flags: list[bool] = [False] * len(raw_files)
    for doc_idx, (keep, raw_resp, err, meta) in enumerate(verdicts):
        keep_flags[doc_idx] = keep
        dep = (meta or {}).get("deployment") or "?"
        deployment_usage[dep] = deployment_usage.get(dep, 0) + 1
        judge_decisions.append({
            "key":        raw_files[doc_idx],
            "margin":     0.0,
            "verdict":    "KEEP" if keep else "DROP",
            "raw":        raw_resp[:60],   # cap for state payload size
            "error":      err,
            "deployment": dep,
            "latency_s":  (meta or {}).get("latency_s"),
            "reward":     (meta or {}).get("reward"),
            "attempts":   (meta or {}).get("attempts"),
        })
        if err:
            judge_errors.append(err)

    relevant: list[str] = []
    per_file: list[tuple[str, float, str, bool]] = []
    for i, key in enumerate(raw_files):
        keep = keep_flags[i]
        leaf = key.rsplit("/", 1)[-1]
        per_file.append((leaf, 0.0, "llm", keep))
        if keep:
            relevant.append(key)

    llm_kept = sum(1 for d in judge_decisions if d["verdict"] == "KEEP")
    llm_dropped = sum(1 for d in judge_decisions if d["verdict"] == "DROP")

    rewards_by_dep: dict[str, list[float]] = {}
    for d in judge_decisions:
        r = d.get("reward")
        if r is None:
            continue
        rewards_by_dep.setdefault(
            d.get("deployment") or "?", [],
        ).append(float(r))
    deployment_summary = [
        {
            "deployment": dep,
            "calls":      deployment_usage.get(dep, 0),
            "reward_avg": (sum(rewards) / len(rewards)) if rewards else 0.0,
        }
        for dep, rewards in sorted(
            rewards_by_dep.items(),
            key = lambda kv: -deployment_usage.get(kv[0], 0),
        )
    ]

    error_breakdown: dict[str, int] = {}
    for err in judge_errors:
        kind = err.split(":", 1)[0].strip() or "unknown"
        error_breakdown[kind] = error_breakdown.get(kind, 0) + 1

    return {
        "relevant":           relevant,
        "per_file":           per_file,
        "judge_decisions":    judge_decisions,
        "judge_errors":       judge_errors,
        "llm_kept":           llm_kept,
        "llm_dropped":        llm_dropped,
        "deployment_summary": deployment_summary,
        "error_breakdown":    error_breakdown,
    }
