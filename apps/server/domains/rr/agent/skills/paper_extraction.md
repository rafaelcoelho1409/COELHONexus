# Skill: Paper Extraction

You are extracting **5 structured fields** from an academic paper's title +
abstract. The output is consumed by Triage's signal_score (`citations`,
`influential_ratio`) and the FastHTML digest card.

## Fields to extract

| Field | Length | What to capture |
|---|---|---|
| `problem` | 2-3 sentences | The real-world gap the paper closes. What was previously hard, unsolved, or expensive? |
| `method` | 4-6 sentences | What the paper does. Architectural moves, training recipe, key insight. |
| `math` | 1-2 formulas (LaTeX OK) | The mathematical content that makes the method work. If purely empirical, write `N/A`. |
| `how_to_build` | 3-5 sentences | Implementation notes — what to wire to what to reproduce or apply. Component names, key hyperparameters, plug-in points. |
| `money_angle` | 2-3 sentences | Where this could generate revenue or unblock a portfolio project. Bias toward LLMOps / agents / quant / applied math. |

Plus a `confidence` float in `[0, 1]`:
- `1.0` → the abstract was rich enough that you read all 5 fields directly from text
- `0.4-0.6` → you inferred 1-2 fields from typical patterns in this paper class
- `< 0.2` → the abstract was sparse; some fields are educated guesses

## Style rules

- **Be concrete.** "uses attention" is weak; "single-head softmax attention over
  64-d projections of token embeddings" is strong.
- **No marketing language.** "groundbreaking" / "state-of-the-art" / "novel" add
  zero signal — drop them.
- **Math notation matters.** Render formulas in LaTeX (`$X = ...$`). Define
  every symbol once.
- **`money_angle` should name a concrete product or workflow** — not "could
  help businesses." Bad: "useful for AI startups." Good: "drop-in replacement
  for the embedding layer in a RAG stack handling >10M tokens/day."

## Failure modes to avoid

1. **Hallucinating method details** the abstract doesn't actually mention.
   When unsure, write less + lower confidence.
2. **Confusing `problem` with `method`** — `problem` describes the world
   without the paper; `method` describes the paper's contribution.
3. **Writing a `money_angle` for every paper** even when there is none.
   For pure math / theory papers it's fine to write
   "Reference material for [specific area]; no direct commercial application."
