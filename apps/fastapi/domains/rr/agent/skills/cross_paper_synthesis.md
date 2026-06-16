# Skill: Cross-paper synthesis

You are reading the deep_read extractions for this scan's top-N papers and
identifying **what's notable across the set as a whole**. Output: themes,
cross-paper convergence, executive summary.

## Themes (3-7 short names)

A theme is a **conceptual cluster** that spans **≥2 papers**, not a paper
category. Good theme names:

- `constrained decoding`
- `speculative tool validation`
- `deep agent planning`
- `linear-time recurrence in RNNs`
- `state-space models for long-context attention`

Bad theme names (paper categories, not conceptual clusters):

- `NLP` (too broad — every paper is "NLP")
- `cs.LG` (arxiv tag, not a theme)
- `New Methods` (vacuous)

Each theme should be 2-5 words. If the corpus is small (top_n=4-8) you'll
usually find **3-5 themes**. For larger corpora, 5-7.

## Cross-paper convergence (4-8 sentences)

This is the radar's killer insight. Look for **where multiple papers
independently arrive at related ideas**. Examples:

- "Three independent groups (Papers A, B, C) all converge on a sparse-MoE
  routing scheme to reduce inference cost; they differ in the gating
  signal (input-token / hidden-state / attention-weight)."
- "Papers D and E both replace the LLM's softmax attention with a
  state-space layer (Mamba-style), but Paper D applies it at training
  time while Paper E retrofits it post-hoc via distillation."

If there's no real convergence (each paper is in its own corner), say so
explicitly: "This scan's papers cover distinct sub-areas; no significant
cross-paper convergence detected this week."

## Executive summary (2-3 sentences)

What's most striking about THIS scan, compared to a reader's prior. Bias
toward what would be NEW or SURPRISING to a builder in LLMOps / agents /
quantitative finance / applied math. Don't summarize every paper — pick the
1-2 strongest signals.

## Style rules

- **Concrete > abstract.** "5 papers explore reasoning over long context"
  is weak; "3 papers benchmark on 128k-token tasks with different attention
  variants" is strong.
- **Numbers help.** "4 of 8 papers focus on agent tool-calling" beats
  "many papers focus on agents."
- **Cite specific arxiv_ids** in the convergence note when claiming
  multiple papers do X.

## Per-paper theme assignment

After you've named the themes, you also assign each top_n paper to which
subset of those themes describes it. This goes in `write_synthesis_report`'s
`per_paper_themes` argument: a dict `{arxiv_id: [theme_name, ...]}`.

HARD RULES (the digest is unusable without these):

- **STRICT SUBSET of `themes`.** Only theme names that appear in your
  top-level `themes` list. No synonyms, no abbreviations, no new themes.
- **Max 2 per paper.** A paper that genuinely spans 3+ themes is rare;
  if you find yourself listing 3, drop the weakest. Listing 4+ is always
  wrong.
- **0 is OK.** Some papers don't fit any theme. Use an empty list `[]`,
  not a forced fit.
- **NEVER copy the full `themes` list into one paper.** That degenerate
  case ("every paper covers every theme") is the #1 quality failure mode.
- **Match by paper content.** Re-read the deep_read `problem` + `method`
  fields and ask "which 1-2 themes describe what THIS paper actually does?"
  — not "which theme names contain words from the title?".

Worked example (4-paper scan, themes
`["object-centric RL", "hierarchical task decomposition", "tool generation"]`):

```
{
  "2401.12345": ["object-centric RL"],                              # paper A — single theme
  "2402.06789": ["hierarchical task decomposition"],                # paper B — single theme
  "2403.00111": ["tool generation"],                                # paper C — single theme
  "2404.22222": ["hierarchical task decomposition", "tool generation"]  # paper D — 2 themes
}
```

Every paper has 1-2 themes. NONE has the full 3-theme list. Paper D's
two-theme assignment is the most a single paper should ever have.
