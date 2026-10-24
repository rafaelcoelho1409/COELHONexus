# Skill: Digest Rendering

You are assembling the final ranked digest for a Research Radar scan.
Output: a structured JSON digest written to `fs/digest.json`.

## Shape

```json
{
  "scan_id":    "<uuid>",
  "summary":    "<2-3 sentence executive summary, lifted from synthesis>",
  "themes":     ["<theme 1>", "<theme 2>", ...],
  "items": [
    {
      "arxiv_id":   "2406.12345",
      "rank":       1,
      "signal":     0.83,
      "title":      "...",
      "authors":    ["...", ...],
      "summary":    "<1-sentence: what's new in this paper>",
      "themes":     ["<theme name from synthesis>", ...],
      "sources":    ["arxiv", "semantic_scholar", "hn", ...],
      "extraction": {
        "problem":      "...",
        "method":       "...",
        "math":         "...",
        "how_to_build": "...",
        "money_angle":  "...",
        "confidence":   0.85
      }
    }
  ]
}
```

## Per-item rules

- **`rank`**: 1 is the best (highest signal), N is the lowest. Ranks must
  be contiguous and start at 1.
- **`signal`**: copy verbatim from `fs/triage/top_n.json`. Do not re-score.
- **`summary`** (per-item): ONE sentence. The deep_read `problem` field
  truncated to 1 sentence is usually right. Don't repeat the title.
- **`themes`** (per-item): a STRICT SUBSET of the top-level `themes` list,
  containing only the themes that ACTUALLY apply to THIS specific paper.

  **HARD RULES** (the digest is unusable without these):

    * **NEVER copy the top-level `themes` list into a per-item field.**
      That is the most common failure mode and produces a degenerate
      digest where every paper looks like it covers every theme.
    * **Max 2 themes per item.** A paper that genuinely spans 3+ themes
      is rare; if you find yourself listing 3, you're probably over-
      assigning — drop the weakest. Listing 4+ is always wrong.
    * **0 themes is OK.** Some papers are legitimately uncategorizable
      against the synthesis theme set. Don't reach for a theme just to
      "fill the field". Empty list `[]` is the right answer then.
    * **Match by paper content, not by theme name overlap.** Re-read the
      paper's `problem` + `method` extraction. Ask "which 1-2 themes
      describe what THIS paper does?" — not "which theme names contain
      words from this paper's title?".

  Worked example (digest of 4 papers, themes
  `["object-centric RL", "hierarchical task decomposition", "tool generation"]`):

  ```
  paper A: "Object-centric attention for Atari"     → ["object-centric RL"]
  paper B: "Hierarchical RL via subgoal discovery"  → ["hierarchical task decomposition"]
  paper C: "Code-generating agents w/ MCP tools"    → ["tool generation"]
  paper D: "AlphaApollo — hierarchical tool-using"  → ["hierarchical task decomposition", "tool generation"]
  ```

  All 4 items differ. NONE has the full 3-theme list. Paper D's 2 themes
  is the most a single paper should ever have.
- **`extraction`**: lift verbatim from `fs/extractions/{arxiv_id}.json`.
  If no extraction exists (deep_read was skipped or failed), set to `null`.
- **`sources`**: lift from `fs/triage/top_n.json` — preserve the union
  across cross-source dedup.

## Top-level rules

- **`summary`** (top-level): the synthesis executive summary. Don't
  re-write it.
- **`themes`** (top-level): the synthesis themes list. Same ordering as
  what synthesis wrote.

## Failure modes

1. **Copying the top-level `themes` list into every item's `themes`
   field.** This is the #1 quality bug — produces a digest where every
   paper appears to cover every theme. The reader can't tell which
   papers are actually about object-centric RL vs hierarchical task
   decomposition. SEE the per-item `themes` rule above.
2. **Forgetting `extraction`.** Every item must include the field, even if
   `null`. Without it the FastHTML cards have nothing to render below
   the title.
3. **Renumbering ranks**. Triage already ranked. Just copy `rank` from
   triage's order.
4. **Re-summarizing**. The synthesis subagent already wrote the
   executive summary. Lift it as-is; don't paraphrase.
