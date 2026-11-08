"""Planner V2 chapter-count choices and Synth exclusions preserve source coverage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.dd.nodes.pipeline.planner.chapter_design import domain


def check(label: str, condition: bool) -> None:
    assert condition, label
    print(f"ok  {label}")


def page(key: str, code_hash: str) -> dict:
    return {
        "key": key,
        "title": key.rsplit("/", 1)[-1],
        "topic": key.split("/")[-2],
        "summary": "A practical guide to build and run an agent workflow",
        "teaches": ["agent workflow", "tool use"],
        "kind": "concept",
        "n_best_code": 1,
        "reading_minutes": 2,
        "tutorial_example_minutes": 1,
        "best_code": [{"hash": code_hash, "defines": ["run"]}],
    }


def candidates_for(pages: list[dict], code_hashes: list[str]) -> dict:
    groups = []
    for index, item in enumerate(pages):
        groups.append({
            "group_id": f"g{index}",
            "kind": "teaching",
            "pages": [item],
            "best_code": [{"hash": code_hashes[index]}],
        })
    return {
        "partitions": [{"id": "p", "metrics": {"score": 0.8}, "groups": groups}],
        "chapter_overhead_minutes": 1,
    }


def chapter(title: str, group_id: str, *, eligible: bool = True, reason: str = "") -> dict:
    return {
        "title": title,
        "description": f"Learn {title}.",
        "objective": f"Build {title}.",
        "group_ids": [group_id],
        "kind": "tutorial",
        "synth_eligible": eligible,
        "synth_exclusion_reason": reason,
        "capstone_ability": True,
    }


cards = [
    {
        "key": f"dd/fw/ingestion/pages/topic-{index:02d}.md",
        "title": f"Topic {index}",
        "topic": f"topic-{index}",
        "summary": f"How to configure topic {index} and compose its agent workflow",
        "teaches": [f"configure topic {index}"],
        "kind": "concept",
        "tutorial_example_minutes": 15,
    }
    for index in range(40)
]
vectors = domain.local_tfidf(cards)
candidate_options = domain.build_candidate_partitions(
    cards,
    vectors,
    total_minutes=875,
    must_teach_by_page={item["key"]: 1 for item in cards},
)
chosen_counts = {item["metrics"]["n_tutorial_chapters"] for item in candidate_options["partitions"]}
check(
    "candidate design exposes chapter counts above the former 30-chapter cap",
    candidate_options["target_chapters"] == 35 and any(count > 30 for count in chosen_counts),
)
check("candidate alternatives remain prompt-bounded without capping chapter count", len(candidate_options["partitions"]) <= 8)

pages = [page("dd/fw/ingestion/pages/agents.md", "code-a"), page("dd/fw/ingestion/pages/reference.md", "code-b")]
candidates = candidates_for(pages, ["code-a", "code-b"])
answer = {
    "candidate_partition_id": "p",
    "chapters": [
        chapter("Agent Workflow", "g0"),
        chapter("Reference Details", "g1", eligible=False, reason="Lookup-only API detail."),
    ],
}
chapters, stats = domain.reconcile_design(answer, candidates, pages)
excluded = next(item for item in chapters if item["title"] == "Reference Details")
check("unique best code overrides a Synth exclusion", excluded["synth_eligible"] is True and stats["n_synth_exclusion_overrides"] == 1)
check(
    "Planner retains every exact source Markdown filename",
    {key for item in chapters for key in item["member_doc_keys"]} == {item["key"] for item in pages},
)

repeated_candidates = candidates_for(pages, ["shared-code", "shared-code"])
repeated_answer = {
    "candidate_partition_id": "p",
    "chapters": [
        chapter("Agent Workflow", "g0"),
        chapter("Reference Details", "g1", eligible=False, reason="Duplicate lookup detail."),
    ],
}
repeated_chapters, repeated_stats = domain.reconcile_design(repeated_answer, repeated_candidates, pages)
repeated_excluded = next(item for item in repeated_chapters if item["title"] == "Reference Details")
check(
    "a duplicated best-code family may remain reference-only",
    repeated_excluded["synth_eligible"] is False and repeated_stats["n_synth_excluded"] == 1,
)

distinct_pages = [page("dd/fw/ingestion/pages/agents.md", "shared-code"), page("dd/fw/ingestion/pages/reference.md", "shared-code")]
distinct_pages[1]["topic"] = "streaming checkpoints"
distinct_pages[1]["teaches"] = ["resume streaming workflows from checkpoints"]
distinct_candidates = candidates_for(distinct_pages, ["shared-code", "shared-code"])
distinct_answer = {
    "candidate_partition_id": "p",
    "chapters": [
        chapter("Agent Workflow", "g0"),
        chapter("Streaming Checkpoints", "g1", eligible=False, reason="Additional lookup detail."),
    ],
}
distinct_chapters, distinct_stats = domain.reconcile_design(distinct_answer, distinct_candidates, distinct_pages)
distinct_exclusion = next(item for item in distinct_chapters if item["title"] == "Streaming Checkpoints")
check(
    "a Synth exclusion cannot discard a unique source-card teaching point",
    distinct_exclusion["synth_eligible"] is True and distinct_stats["n_synth_concept_exclusion_overrides"] == 1,
)
