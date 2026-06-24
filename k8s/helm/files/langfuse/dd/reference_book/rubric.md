# Faithfulness rubric — DD chapter outline

Used by `infra/langfuse/evals/judges/faithfulness.py`. The LLM judge sees
this rubric inline in its prompt; do not rely on the markdown structure
when editing.

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
