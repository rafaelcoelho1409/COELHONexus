# Book Distiller Learning Guide evaluation

Use paired human reviews to check whether a Learning Guide is accurate, useful, and faster to learn from than its source book. The server also performs deterministic checks for source page coverage, page identity, evidence quote support, and stored artifact integrity. Automated checks cannot establish learning outcomes by themselves.

## Pilot set

Review at least three varied books before considering the initial small-beta gate. Include a scanned or OCR-heavy book, a technical/reference book, and a prose nonfiction book when available. Keep the evaluation books separate from prompts and tuning examples. Sample pages and concepts across the beginning, middle, and end of each book.

The current workspace does not contain a human-reviewed gold set. Add one JSON object per paired review to a private JSONL file. Do not commit copyrighted book text or personal reviewer data.

```json
{
  "book_id": "book-id",
  "distill_run_id": "run-id",
  "coverage_rating": 4,
  "citation_accuracy_rating": 5,
  "clarity_rating": 4,
  "usefulness_rating": 4,
  "source_comprehension_score": 80,
  "guide_comprehension_score": 82,
  "source_delayed_recall_score": 70,
  "guide_delayed_recall_score": 72,
  "guide_transfer_score": 75,
  "source_reading_minutes": 300,
  "guide_reading_minutes": 90,
  "missed_topics": [],
  "unsupported_claims": []
}
```

Use the same concept and application questions for the source-book and guide comprehension checks. Run delayed recall after a consistent interval and record the interval in the review notes maintained with the private evaluation data. Cite unsupported claims by report lesson title or stable report location; record a missed topic when the guide omits an idea the reviewer considers important.

## Rating anchors

- **Coverage:** 1 = major chapter or central ideas missing; 3 = useful but meaningful omissions; 5 = all expected important ideas are represented.
- **Citation accuracy:** 1 = citations routinely fail to support claims; 3 = mixed support or difficult source navigation; 5 = claims reliably resolve to supporting pages and excerpts.
- **Clarity:** 1 = hard to understand; 3 = understandable with gaps; 5 = clear enough to teach the ideas without losing needed caveats.
- **Usefulness:** 1 = not actionable; 3 = some learning or decision value; 5 = strongly supports recall, application, or decisions.

## Initial small-beta gate

The evaluator in `apps/server/domains/bd/distill/evaluation.py` uses an explicit initial gate: three distinct books; mean coverage at least 4/5; citation accuracy at least 4.5/5; clarity and usefulness at least 4/5; guide comprehension and delayed recall each no more than five points below the paired source-book result; positive average reading-time savings; and no critical unsupported-claim findings. Each book must also meet those quality, learning, time-savings, and grounding floors on its own, so a strong title cannot hide a weak one. The JSON output includes per-book results and equally weights books in the overall means. These are pilot thresholds to revisit after the first reviewed set, not a claim of statistical proof or a guarantee of equivalent learning for every reader.

Run the aggregation with:

```bash
cd apps/server
.venv/bin/python ../../scripts/server/evaluate_bd_learning_guide.py /path/to/private-reviews.jsonl
```

Exit code `0` means the current small-beta gate passes. Exit code `2` means the evidence is insufficient or one or more thresholds failed. Keep individual reviews and the printed per-book results available for manual inspection.
