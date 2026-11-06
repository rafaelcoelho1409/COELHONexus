"""Export the "needs review" suggestions of a book's current Agentic AI run as a CSV to label by hand.

    python3 scripts/server/export_bd_review_labels.py --book BOOK_ID [--base-url http://localhost:8000] \
        [--sample 50] [--seed 1] [--out labels.csv]

Read-only: it only issues GET requests. Fill the `label` column with `correct` (applying it improves the page),
`wrong` (applying it harms the page) or `cosmetic` (harmless either way), then count: the share of `correct` among
what the automatic gate would apply is the precision to hold against the 98% bar.
"""
import argparse
import csv
import json
import random
import sys
import time
import urllib.error
import urllib.request


def get(url: str) -> dict:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout = 30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description = __doc__.splitlines()[0])
    parser.add_argument("--book", required = True)
    parser.add_argument("--base-url", default = "http://localhost:8000")
    parser.add_argument("--sample", type = int, default = 0, help = "keep a random sample of this size (0 = all)")
    parser.add_argument("--seed", type = int, default = 1)
    parser.add_argument("--out", default = "-")
    args = parser.parse_args()

    api = f"{args.base_url.rstrip('/')}/api/v1/bd/ingestion/{args.book}"
    run = get(api).get("agentic")
    if not run:
        print("this book has no Agentic AI run", file = sys.stderr)
        return 1

    rows = []
    for page in run["review_pages"]:
        quality = get(f"{api}/pages/{page}/quality")
        for issue in quality["issues"]:
            proposal = issue.get("proposal")
            if issue["resolution"] != "review" or not proposal:
                continue
            rows.append({
                "run": run["agentic_run_id"],
                "page": page,
                "issue_id": issue["issue_id"],
                "code": issue["code"],
                "risk": proposal["risk"],
                "confidence": proposal["confidence"],
                "target": proposal["target_text"],
                "replacement": proposal["replacement"],
                "rationale": proposal["rationale"],
                "label": "",
            })
    if args.sample and len(rows) > args.sample:
        rows = sorted(random.Random(args.seed).sample(rows, args.sample), key = lambda row: (row["page"], row["issue_id"]))

    out = sys.stdout if args.out == "-" else open(args.out, "w", newline = "", encoding = "utf-8")
    writer = csv.DictWriter(out, fieldnames = list(rows[0]) if rows else ["run"])
    writer.writeheader()
    writer.writerows(rows)
    if out is not sys.stdout:
        out.close()
    print(f"{len(rows)} suggestion(s) from run {run['agentic_run_id']}", file = sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
