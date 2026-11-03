"""A/B of how a captured docs source becomes pages: arm A (`current`, the tier code as it is) against any arm registered in
`domains.dd.ingestion.ab.params.ARM_MODULES` — the agent arm built in `apps/server/ai/dd/`.

Both arms start from the SAME capture: what the tier fetched, exactly as it came, kept under `ingestion/raw/{slug}/` in MinIO.

    cd apps/server
    PY=.venv/bin/python
    $PY ../../scripts/dd/ab_ingestion.py capture                         # every catalog source with an llms-full.txt (Tier 1); or name slugs
    $PY ../../scripts/dd/ab_ingestion.py run --arm current               # make the pages, measure them, keep them under --out
    $PY ../../scripts/dd/ab_ingestion.py run --arm current --arm agent   # …and the agent arm on the same captures
    $PY ../../scripts/dd/ab_ingestion.py report --arm agent              # one row per source + the verdict of each arm against A

The verdict rule is in `ab.params.VERDICT`, fixed before any agent arm ran. Per source, what an arm made is `{out}/{slug}/{arm}/`:
`pages/NNNN.md`, `pages.json`, `card.json` — open two arms' pages side by side to judge them yourself.

From a shell outside the cluster the MinIO variables the server reads are taken from the ones the shell has (MINIO_ENDPOINT host,
MINIO_USE_SSL, MINIO_ACCESS_KEY, MINIO_SECRET_KEY); the bucket is `coelhonexus`.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
if "AWS_ACCESS_KEY_ID" not in os.environ and os.environ.get("MINIO_ACCESS_KEY"):
    scheme = "https" if os.environ.get("MINIO_USE_SSL", "").lower() in ("1", "true", "yes") else "http"
    host = os.environ["MINIO_ENDPOINT"]
    os.environ["MINIO_ENDPOINT"] = host if host.startswith("http") else f"{scheme}://{host}"
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["MINIO_ACCESS_KEY"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["MINIO_SECRET_KEY"]
    os.environ.setdefault("MINIO_BUCKET_COELHONEXUS", "coelhonexus")

import domains, infra

ingestion = domains.dd.ingestion
ab = ingestion.ab
DEFAULT_OUT = Path("/tmp/dd_ab")


def catalog_slugs(wanted: list[str]) -> list[str]:
    """The catalog sources that declare an llms-full.txt (Tier 1), or the ones named."""
    index = domains.dd.resolver.service.index_by_slug()
    if wanted:
        unknown = [s for s in wanted if s not in index]
        if unknown:
            sys.exit(f"not in the catalog: {', '.join(unknown)}")
        return wanted
    return sorted(slug for slug, entry in index.items() if entry.get("llms_full"))


async def capture_all(slugs: list[str]) -> None:
    index = domains.dd.resolver.service.index_by_slug()
    minio = infra.minio.service.get_storage()
    for slug in slugs:
        entry = index[slug]
        started = time.monotonic()
        try:
            kept = await ingestion.tiers.tier1.service.capture(
                minio, url = entry["llms_full"], framework_slug = slug,
                path_filter = entry.get("path_filter"), nav_url = entry.get("llms_txt"),
            )
        except Exception as error:  # noqa: BLE001 — one unusable source must not stop the others
            print(f"{slug:14} FAILED {type(error).__name__}: {error}")
            continue
        print(f"{slug:14} {sum(f.bytes for f in kept.files) / 1e6:6.2f} MB  files={[f.name for f in kept.files]}  sha256={kept.files[0].sha256[:12]}  {time.monotonic() - started:.0f}s")


async def run_all(slugs: list[str], arms: list[str], out: Path) -> None:
    minio = infra.minio.service.get_storage()
    for slug in slugs:
        try:
            inputs = await ab.service.prepare(minio, slug)
        except (LookupError, NotImplementedError) as error:
            print(f"{slug:14} skipped: {error}")
            continue
        reference = await asyncio.to_thread(ab.domain.analyze, inputs.text)
        for arm in arms:
            try:
                pages, seconds = await ab.service.run_arm(arm, inputs)
            except Exception as error:  # noqa: BLE001 — an arm that fails on one source is a result, not a reason to stop
                print(f"{slug:14} {arm:10} FAILED {type(error).__name__}: {error}")
                continue
            card = await ab.service.score(inputs, pages, reference = reference)
            ab.service.write_result(out, slug, arm, pages, card, seconds)
            print(row(slug, arm, card, seconds))


def row(slug: str, arm: str, card: dict, seconds: float | None = None) -> str:
    agreement = card["agreement"]
    return (
        f"{slug:14} {arm:10} pages={card['pages']:5} <1k={card['small_pages']:4} >100k={card['oversized_pages']:3} noH1={card['no_h1']:4} "
        f"recall={card['recall']:.3f} prec={card['precision']:.3f} code_changed={card['fences']['tolerant_changed']:3}/{card['fences']['distinct']:<5} "
        f"noise/MB={card['noise_per_mb']:7.1f} agree={'-' if agreement is None else f'{agreement:.2f}'}"
        + (f"  {seconds:.0f}s" if seconds is not None else "")
    )


def report(slugs: list[str], arms: list[str], out: Path) -> None:
    judged = json.loads((out / "judge.json").read_text()) if (out / "judge.json").exists() else {}   # {slug: {arm: B's win rate vs A}}
    tally: dict[str, dict[str, int]] = {arm: {"A": 0, "B": 0, "tie": 0} for arm in arms if arm != ab.params.BASELINE_ARM}
    for slug in slugs:
        base = ab.service.read_card(out, slug, ab.params.BASELINE_ARM)
        if base is None:
            print(f"{slug:14} no baseline card (run --arm {ab.params.BASELINE_ARM})")
            continue
        print(row(slug, ab.params.BASELINE_ARM, base, base.get("seconds")))
        for arm in arms:
            card = ab.service.read_card(out, slug, arm)
            if card is None or arm == ab.params.BASELINE_ARM:
                continue
            verdict = ab.domain.verdict(base, card, judge_win_rate = judged.get(slug, {}).get(arm))
            tally[arm][verdict["winner"]] += 1
            failed = [g for g, ok in verdict["gates"].items() if not ok]
            print(row(slug, arm, card, card.get("seconds")) + f"\n{'':25}-> {verdict['winner']}  measures={verdict['measures']}" + (f"  FAILED GATES: {failed}" if failed else ""))
    for arm, counts in tally.items():
        print(f"\n{arm} against {ab.params.BASELINE_ARM}: B wins {counts['B']}, A wins {counts['A']}, tie (keeps A) {counts['tie']}")


def main() -> None:
    parser = argparse.ArgumentParser(description = __doc__, formatter_class = argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest = "command", required = True)
    for name in ("capture", "run", "report"):
        p = sub.add_parser(name)
        p.add_argument("slugs", nargs = "*", help = "catalog slugs (default: every source with an llms-full.txt)")
        if name != "capture":
            p.add_argument("--arm", action = "append", help = f"arms to {name} (default: {ab.params.BASELINE_ARM})")
            p.add_argument("--out", type = Path, default = DEFAULT_OUT)
    args = parser.parse_args()
    slugs = catalog_slugs(args.slugs)
    if args.command == "capture":
        asyncio.run(capture_all(slugs))
        return
    arms = args.arm or [ab.params.BASELINE_ARM]
    if args.command == "run":
        asyncio.run(run_all(slugs, arms, args.out))
    else:
        report(slugs, sorted({ab.params.BASELINE_ARM, *arms}, key = lambda a: (a != ab.params.BASELINE_ARM, a)), args.out)


main()
