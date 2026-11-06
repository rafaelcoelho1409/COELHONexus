"""chapter_propose — bump when the proposal logic changes so re-plans don't cache-hit the old set."""
from __future__ import annotations


# v1..v18 (2026-09 .. 2026-10-14): five sampled drafts from one prompt, an evidence pick and an LLM vote; heading seeds,
# SOURCE STRUCTURE and the family / catalog-section rules grew in that period.
# v19 (2026-11-05): a deep-agent team. Section analysts (one per URL directory, in parallel) sketch their chapters; the
# chapter architect merges the sketches against the read-only corpus, checks the outline with the score tool, and has plan_critic
# review it through two lenses; a weak score sends the architect back once with the uncovered pages. Best-of-N, the weak-batch
# retry and the vote are gone.
PROMPT_VERSION = "v19-deep-architect-2026-11-05"
