"""Pedagogical-ordering prompt; domain-agnostic rubric to avoid baking in framework-specific biases."""
from __future__ import annotations
from . import domain, params



def _normalize_description(desc: str) -> str:
    """Trim + clamp for prompt compactness; the LLM needs only a hint of each chapter."""
    s = (desc or "").strip().replace("\n", " ")
    if len(s) > params.DESCRIPTION_CHARS:
        return s[:params.DESCRIPTION_CHARS].rstrip() + "..."
    return s


def _composition(comp: dict | None) -> str:
    if not comp:
        return ""
    return f" ({comp['n_docs']} pages, {comp['reference_pct']}% API/reference)"


def build_order_prompt(
    chapters: list[dict],
    composition: list[dict | None] | None = None,
    framework: str = "",
    sample_idx: int = 0,
) -> str:
    """Pedagogical-ordering prompt: the model returns chapter TITLES (not positions) in reading
    order. Chapters are shown in a per-sample shuffled arrangement; domain-agnostic rubric."""
    labels = domain.chapter_labels(chapters)
    chapter_block = "\n".join(
        f"- {labels[i]} \u2014 {_normalize_description(chapters[i].get('description', ''))}"
        f"{_composition(composition[i]) if composition else ''}"
        for i in domain.shuffled_indices(len(chapters), sample_idx)
    )
    n = len(chapters)
    about = f" about {framework}" if framework else ""
    return (
        f"You are a curriculum designer ordering the chapters of a technical learning book{about}. "
        f"A reader goes through it front to back, so every chapter must be understandable from "
        f"the ones before it.\n\n"
        f"Think in reading stages:\n"
        f"1. Orientation: what the framework is, installation, setup, quickstart, the core mental model.\n"
        f"2. Core usage: the building blocks nearly every application uses.\n"
        f"3. Extended features: optional capabilities that build on the core.\n"
        f"4. Production and operations: security and auth, deployment, observability, testing, "
        f"performance, command-line tooling.\n"
        f"5. Reference and maintenance: API listings, migration or upgrade guides, changelogs, FAQ, "
        f"miscellany.\n"
        f"Migration and upgrade chapters are for readers who already know an older version: stage 5, "
        f"never first. A chapter made mostly of API/reference pages comes after the chapters that "
        f"teach the same area with guides. Within a stage, concrete usage comes before abstract "
        f"internals and simple topics before complex ones.\n\n"
        f"CHAPTERS ({n}, shown in no particular order):\n{chapter_block}\n\n"
        f'Respond ONLY with valid JSON: {{"rationale": "<2-3 sentences>", "order": ["<chapter title '
        f'exactly as given>", ... all {n} titles, each exactly once, in reading order]}}'
    )
