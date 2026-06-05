"""order_chapters substep — pedagogical chapter ordering via LLM + USC vote.

Bundle 8 (2026-05-25). Sits between `chapter_select` (which produces
chapter candidates in arbitrary order) and `plan_write` (which hydrates
per-chapter source lists into the final plan).

The chapter_select node's order is essentially arbitrary — proposals
emerge in whatever sequence the underlying LLM emitted, often source-
key order. That's pedagogically random: a learner reading chapter 1
(Middleware) before chapter 3 (Transport Protocols) gets confused
because middleware depends on understanding transports.

This node samples K pedagogical orderings from a bandit-routed LLM,
Borda-aggregates them into a single ranking, and applies a deterministic
foundational-prefix rule (install/setup/cli chapters anchor at position 0).

Sources:
- arXiv 2507.18479 — How Well Do LLMs Predict Prerequisite Skills?
- arXiv 2511.17041 — CLLMRec: LLM-powered Cognitive-Aware Recommendation
- arXiv 2501.12300 — LLM-Assisted KG Completion for Curriculum Modelling
- arXiv 2311.17311 — Universal Self-Consistency
"""
from .domain import load_chapter_order
from .node import order_chapters


__all__ = ["load_chapter_order", "order_chapters"]
