# Faithfulness rubric — DD chapter outline

This dataset feeds **two** judges from `infra/langfuse/evals/judges/service.py` —
`faithfulness` and `citation_accuracy` — both against the same `expected_output.chapters`.
They score different dimensions, so a single run should report both.

## faithfulness — overall outline shape

Template in `infra/langfuse/evals/judges/prompts.py::FAITHFULNESS_PROMPT`. The
LLM judge sees this rubric inline in its prompt; do not rely on the markdown
structure when editing.

## Criteria

1. **Chapter count** — actual within ±1 of expected.
2. **Title specificity** — each title concrete and specific (no "Introduction",
   "Overview", "Conclusion", "Getting Started", "About", "Background",
   "References").
3. **Key-concept overlap** — each chapter's key concepts overlap meaningfully
   with the expected chapter's concepts (semantic match, not lexical).
4. **Scope distinctness** — no two chapters cover the same scope.

## Scoring (1-5)

| Score | Meaning |
|---|---|
| 5 | All criteria met, semantic alignment |
| 4 | All criteria met, slight rewording acceptable |
| 3 | 1 criterion missed |
| 2 | 2 criteria missed |
| 1 | 3+ criteria missed or fundamentally wrong shape |

## citation_accuracy — per-chapter concept grounding

Template in `infra/langfuse/evals/judges/prompts.py::CITATION_ACCURACY_PROMPT`.
Where `faithfulness` grades the outline's overall shape, this judge grades
whether each chapter's `key_concepts` are actually grounded in the expected
concepts for that chapter (no spurious concepts, right granularity).

1. Each actual chapter's key concepts has ≥1 expected concept (semantic match).
2. No spurious concepts unrelated to the topic.
3. Concept granularity matches (specific names, not vague categories).

| Score | Meaning |
|---|---|
| 5 | Every chapter has high concept overlap, no spurious concepts |
| 4 | High overlap, minor wording variance |
| 3 | 1 chapter has weak overlap or 1 spurious concept |
| 2 | 2 chapters with overlap issues |
| 1 | Systemic mismatch |

## Extending the dataset

Add new items to `inputs.json` following the same shape:

```json
{
  "input":           {...framework + source_keys + target_chapters},
  "expected_output": {"chapters": [...]},
  "metadata":        {"framework_type": "...", "fixture_version": "..."}
}
```

Then push: `python -m infra.langfuse.datasets.uploader observability/fixtures/dd/reference_book dd.reference_book.v1`.
