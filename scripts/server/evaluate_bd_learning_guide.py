"""Aggregate paired human Learning Guide reviews from a JSONL file.

    cd apps/server && .venv/bin/python ../../scripts/server/evaluate_bd_learning_guide.py reviews.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

for name, value in {
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "x",
    "ENVIRONMENT": "local",
}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description = "Evaluate paired human reviews of Book Distiller reports.")
    parser.add_argument("reviews", type = Path, help = "JSONL file using the documented HumanLearningReview fields")
    arguments = parser.parse_args()
    review_type = domains.bd.distill.evaluation.HumanLearningReview
    reviews = [
        review_type.model_validate_json(line)
        for line in arguments.reviews.read_text(encoding = "utf-8").splitlines()
        if line.strip()
    ]
    result = domains.bd.distill.evaluation.aggregate_human_reviews(reviews)
    print(json.dumps(result, ensure_ascii = False, indent = 2, sort_keys = True))
    return 0 if result["small_beta_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
