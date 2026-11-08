#!/usr/bin/env python3
"""Compare a planner plan with structural invariants, an optional gold manifest, and a v1 baseline."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding = "utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def plan_metrics(plan: dict[str, Any], gold: dict[str, Any] | None = None) -> dict[str, Any]:
    chapters = [chapter for chapter in plan.get("chapters") or [] if isinstance(chapter, dict)]
    source_occurrences = [
        source for chapter in chapters for source in (chapter.get("sources") or [])
        if isinstance(source, str)
    ]
    source_set = set(source_occurrences)
    duplicate_sources = sorted(source for source, count in Counter(source_occurrences).items() if count > 1)
    unassigned = set(plan.get("unassigned") or [])
    expected_sources = set((gold or {}).get("source_keys") or [])
    required_code = set((gold or {}).get("must_teach_code_ids") or [])
    planned_code = {
        digest
        for chapter in chapters
        for digest in (chapter.get("best_code_ids") or [])
        if isinstance(digest, str)
    }
    positions = {chapter.get("id"): index for index, chapter in enumerate(chapters) if chapter.get("id")}
    edges = []
    dangling = []
    backward = []
    for chapter in chapters:
        chapter_id = chapter.get("id")
        for dependency in chapter.get("prerequisites") or []:
            if dependency not in positions:
                dangling.append({"chapter": chapter_id, "prerequisite": dependency})
            elif positions[dependency] >= positions.get(chapter_id, -1):
                backward.append({"chapter": chapter_id, "prerequisite": dependency})
            else:
                edges.append((dependency, chapter_id))

    tutorial_minutes = sum(
        float(chapter.get("tutorial_minutes") or 0.0)
        for chapter in chapters
        if chapter.get("kind") != "appendix" and chapter.get("role") != "appendix"
    )
    over_budget = [
        chapter.get("id")
        for chapter in chapters
        if chapter.get("kind") != "appendix" and chapter.get("role") != "appendix"
        and float(chapter.get("tutorial_minutes") or 0.0) > 45.0
    ]
    appendix_with_tutorial_time = [
        chapter.get("id")
        for chapter in chapters
        if (chapter.get("kind") == "appendix" or chapter.get("role") == "appendix")
        and float(chapter.get("tutorial_minutes") or 0.0) > 0.0
    ]
    metrics: dict[str, Any] = {
        "planner_version": plan.get("planner_version", "v1"),
        "n_chapters": len(chapters),
        "n_sources": len(source_set),
        "n_duplicate_source_assignments": len(duplicate_sources),
        "duplicate_sources": duplicate_sources,
        "n_unassigned": len(unassigned),
        "tutorial_minutes": round(tutorial_minutes, 1),
        "chapters_over_45_tutorial_minutes": over_budget,
        "appendix_chapters_with_tutorial_time": appendix_with_tutorial_time,
        "n_best_code_ids": len(planned_code),
        "n_prerequisite_edges": len(edges),
        "n_dangling_prerequisites": len(dangling),
        "n_prerequisites_after_chapter": len(backward),
        "dangling_prerequisites": dangling,
        "prerequisites_after_chapter": backward,
    }
    checks = {
        "unique_source_assignment": not duplicate_sources,
        "all_expected_sources_assigned": expected_sources <= source_set if expected_sources else None,
        "all_must_teach_code_preserved": required_code <= planned_code if required_code else None,
        "prerequisites_resolve": not dangling and not backward,
        "tutorial_chapters_within_soft_budget": not over_budget,
        "appendices_excluded_from_tutorial_budget": not appendix_with_tutorial_time,
    }
    metrics["checks"] = checks
    metrics["missing_expected_sources"] = sorted(expected_sources - source_set) if expected_sources else []
    metrics["missing_must_teach_code_ids"] = sorted(required_code - planned_code) if required_code else []
    applicable = [value for value in checks.values() if value is not None]
    metrics["structural_score_pct"] = round(100 * sum(applicable) / len(applicable), 1) if applicable else None
    return metrics


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    fields = ("n_chapters", "n_sources", "n_unassigned", "tutorial_minutes", "n_best_code_ids")
    return {
        field: {
            "baseline": baseline.get(field),
            "current": current.get(field),
            "delta": round(float(current[field]) - float(baseline[field]), 1)
            if isinstance(current.get(field), (int, float)) and isinstance(baseline.get(field), (int, float))
            else None,
        }
        for field in fields
    }


def main() -> int:
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument("plan", help = "Planner plan JSON")
    parser.add_argument("--gold", help = "JSON with source_keys and must_teach_code_ids arrays")
    parser.add_argument("--baseline", help = "Optional v1 plan JSON for side-by-side structural comparison")
    args = parser.parse_args()
    plan = read_json(args.plan)
    gold = read_json(args.gold) if args.gold else None
    result = {"plan": plan_metrics(plan, gold)}
    if args.baseline:
        result["baseline_comparison"] = compare(
            result["plan"], plan_metrics(read_json(args.baseline)),
        )
    print(json.dumps(result, indent = 2, ensure_ascii = False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
