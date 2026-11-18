"""Issue categories and specialist-routing policy for repair planning."""
from __future__ import annotations

MECHANICAL_ISSUES = frozenset({"control_characters", "soft_hyphen"})
ISSUES_BY_ROLE = {
    "text_repair": frozenset({
        "control_characters",
        "split_word",
        "soft_hyphen",
        "fused_marker",
        "text_corruption",
    }),
    "structure": frozenset({"structure", "toc_noise", "table_or_index_layout"}),
}
KNOWN_ISSUES = frozenset({
    "control_characters",
    "near_empty",
    "split_word",
    "soft_hyphen",
    "toc_noise",
    "fused_marker",
    "text_corruption",
    "structure",
    "table_or_index_layout",
    "metadata",
    "other",
})
