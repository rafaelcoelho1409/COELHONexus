"""Move Book Distiller objects to the book-first, run-scoped MinIO layout.

    cd apps/server && .venv/bin/python ../../scripts/server/migrate_bd_ingestion_storage.py
    cd apps/server && .venv/bin/python ../../scripts/server/migrate_bd_ingestion_storage.py --apply
    cd apps/server && .venv/bin/python ../../scripts/server/migrate_bd_ingestion_storage.py --apply --delete-legacy

Dry-run is the default. Applying copies objects from both previous namespaces and writes each canonical manifest last.
Deleting old objects is a separate, explicit option performed only after destination verification.
MINIO_BUCKET_COELHONEXUS selects the bucket.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

for name, value in {
    "REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local",
}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import infra


async def migrate(*, apply: bool, delete_legacy: bool) -> int:
    storage = infra.minio.service.get_storage()
    service = domains.bd.ingestion.storage.service
    keys = domains.bd.ingestion.storage.keys
    previous_ids = set(await storage.list_subfolders(keys.PREVIOUS_BOOKS_PREFIX))
    legacy_ids = set(await storage.list_subfolders(keys.LEGACY_BOOKS_PREFIX))
    book_ids = sorted(previous_ids | legacy_ids)
    print(
        f"bucket={storage.bucket} previous_books={len(previous_ids)} legacy_books={len(legacy_ids)} "
        f"unique_books={len(book_ids)} mode={'apply' if apply else 'dry-run'}"
    )
    failed = 0
    for book_id in book_ids:
        try:
            result = await service.migrate_legacy_book(
                storage, book_id, dry_run = not apply, delete_legacy = delete_legacy,
            )
            if result:
                print(
                    f"{result['status']:>15} book={book_id} extraction={result['extraction_id']} "
                    f"objects={result['objects']}"
                )
        except Exception as error:  # noqa: BLE001 — continue to report all books for an operator-run migration
            failed += 1
            print(f"       FAILED book={book_id}: {type(error).__name__}: {error}", file = sys.stderr)
    print(f"complete failed={failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument("--apply", action = "store_true", help = "copy objects and write canonical manifests")
    parser.add_argument(
        "--delete-legacy", action = "store_true", help = "delete old objects after verifying copied destinations",
    )
    args = parser.parse_args()
    if args.delete_legacy and not args.apply:
        parser.error("--delete-legacy requires --apply")
    return asyncio.run(migrate(apply = args.apply, delete_legacy = args.delete_legacy))


if __name__ == "__main__":
    raise SystemExit(main())
