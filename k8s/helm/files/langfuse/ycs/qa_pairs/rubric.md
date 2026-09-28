# Relevance rubric — YCS question answering

Used by `infra/langfuse/evals/judges/service.py::ragas_relevance` (template in
`infra/langfuse/evals/judges/prompts.py::RAGAS_RELEVANCE_PROMPT`). The LLM
judge sees this rubric inline in its prompt; do not rely on the markdown
structure when editing.

## Criteria

1. **Directly addresses the question** — no off-topic content.
2. **Grounded** — no hallucinated specifics not supported by retrieved context.
3. **Completeness** — covers what the reference (`expected_output.answer`) does.
4. **Concision** — no unnecessary preamble or padding.

## Scoring (1-5)

| Score | Meaning |
|---|---|
| 5 | Answers precisely, fully, and grounded |
| 4 | Answers fully with minor wording variance |
| 3 | Partial answer or some off-topic content |
| 2 | Barely answers, or contains hallucination |
| 1 | Fails to answer, or wrong |

## Fixture design notes

- `ground_truth` is a compressed keyword form of `answer` — kept separate
  because some future judge may want a cheaper lexical-overlap check instead
  of a full LLM read of the prose answer.
- `channel_ids: []` means "search across all ingested channels" — set this
  to specific channel ids only when a fixture needs to test scoping/filtering,
  not topic relevance.
- Include at least one **negative case** (a question the indexed transcripts
  don't cover) so the judge is exercised on criterion 1 and 2 — the correct
  answer is to decline, not fabricate. See the `off-topic-negative-case` item.

## Extending the dataset

Add new items to `inputs.json` following the same shape:

```json
{
  "input":           {"question": "...", "channel_ids": []},
  "expected_output": {"answer": "...", "ground_truth": "..."},
  "metadata":        {"topic": "...", "fixture_version": "..."}
}
```

Then push: `python -m infra.langfuse.evals.datasets.uploader /etc/langfuse-fixtures/ycs/qa_pairs ycs.qa_pairs.v1`.
