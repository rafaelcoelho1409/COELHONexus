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
