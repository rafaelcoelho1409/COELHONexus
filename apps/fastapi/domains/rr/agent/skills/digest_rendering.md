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
- **`themes`** (per-item): only the synthesis themes that ACTUALLY apply
  to this specific paper. Most papers will match 1-2 themes; a few may
  match 0 (legitimately uncategorizable). It's fine.
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

1. **Forgetting `extraction`.** Every item must include the field, even if
   `null`. Without it the FastHTML cards have nothing to render below
   the title.
2. **Renumbering ranks**. Triage already ranked. Just copy `rank` from
   triage's order.
3. **Re-summarizing**. The synthesis subagent already wrote the
   executive summary. Lift it as-is; don't paraphrase.
