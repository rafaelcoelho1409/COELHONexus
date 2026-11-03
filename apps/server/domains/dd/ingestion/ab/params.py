from __future__ import annotations

from dataclasses import dataclass


# The arms that can be run, by name: a dotted module whose `async def transform(inputs) -> list[Page]` makes the pages of one source.
# `current` is the tier code as it is; an agent arm is added here by whoever builds it (e.g. "agent": "ai.dd.agents.<name>.service").
ARM_MODULES = {
    "current": "domains.dd.ingestion.ab.current.service",
}
BASELINE_ARM = "current"

# Page sizes that say something is off with a split: a fragment, and a page the Planner and Synth would truncate
# (`post.params.OVERSIZED_SPLIT_BYTES`, 100 KB, is the second).
SMALL_PAGE_BYTES = 1_000

# How a page's H1 is matched to the title the site's own index gives it.
TITLE_LOOSE_MIN_CHARS = 4


@dataclass(frozen=True, slots=True)
class VerdictRules:
    """Written BEFORE any agent arm is run, so the numbers cannot be bent to a conclusion afterwards. An arm B beats A on a source only
    if it passes every gate AND is better on at least `wins_needed` of the three measures without being worse on the others by more
    than that measure's slack; anything else is a tie, and a tie keeps A (deterministic, free, already in production)."""
    # gates (B must clear all): nothing invented, nothing lost, no code damaged
    min_token_precision: float = 0.99     # share of B's words that the reference has
    recall_slack:        float = 0.02     # B's recall may be this far under A's (noise it removes is not recall it lost)
    code_slack:          float = 0.002    # B may change this share more of the reference's distinct code blocks than A does
    # measures: noise per MB (lower), agreement with the site's index (higher), pages of the wrong size (fewer)
    wins_needed:         int = 2
    noise_slack:         float = 0.10     # relative: B's noise may be this share above A's before it counts as worse
    agreement_slack:     float = 0.02
    wrong_size_slack:    float = 0.02     # share of pages
    # a blind pairwise judge (`ai.dd`), when it has run: B's share of wins over A must reach this to count as a fourth measure
    judge_min_win_rate:  float = 0.60


VERDICT = VerdictRules()
