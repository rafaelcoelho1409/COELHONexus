# Page Auditor gold labels

Add one JSON object per line to `the_art_of_cyberwarfare.jsonl` after a human has
reviewed the complete issue set for that page. Do not treat a known finding as an
exhaustive label unless the entire page has been reviewed; incomplete labels make
issue-precision scores misleading.

Each record uses this shape:

```json
{
  "book_id": "BOOK_ID",
  "extraction_run_id": "RUN_ID",
  "page_number": 2,
  "source_sha256": "64-character hash returned by run.py",
  "page_kind": "blank",
  "issue_codes": []
}
```

The extraction ID and source hash pin the label to the exact immutable page that
was reviewed. `page_kind` and `issue_codes` must be fully adjudicated by a human.
The runner and scorer do not create or modify labels.
