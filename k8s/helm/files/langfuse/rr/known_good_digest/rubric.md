# Novelty rubric — RR digest freshness

Used by `infra/langfuse/evals/judges/domain.py::novelty`. Unlike the DD/YCS
judges, this is **deterministic** (no LLM call) — novelty is a set
computation, not a quality judgment, so there's nothing for an LLM to grade.

## Formula

```
novelty = 1.0 - |prior_arxiv_ids ∩ actual.arxiv_ids| / |actual.arxiv_ids|
```

- `1.0` — every id in `actual.arxiv_ids` is new (no overlap with `prior_arxiv_ids`).
- `0.0` — every id in `actual.arxiv_ids` already appeared in a prior digest (pure carry-over).
- `expected_output` is **not read** by this judge — RR doesn't drop
  non-novel candidates from a digest, it flags them (`item["is_new"]`) and
  scores freshness separately. `expected_output.arxiv_ids` documents what
  the runner's discovery/dedup step should produce for that scenario —
  today that's always the same as `input.current_arxiv_ids`, since none of
  these fixtures exercise a case where a candidate gets dropped outright.
- `metadata.expected_novelty` is a **human sanity-check annotation only**,
  not read by any code — it's there so someone editing this file can verify
  their new example computes the novelty they intended, by hand, before
  it's ever run.

## Extending the dataset

Add new items to `inputs.json` following the same shape:

```json
{
  "input":           {"prior_arxiv_ids": [...], "current_arxiv_ids": [...]},
  "expected_output":  {"arxiv_ids": [...]},
  "metadata":         {"fixture_version": "...", "description": "...", "expected_novelty": 0.0}
}
```

Then push: `python -m infra.langfuse.evals.datasets.uploader /etc/langfuse-fixtures/rr/known_good_digest rr.known_good_digest.v1`.
