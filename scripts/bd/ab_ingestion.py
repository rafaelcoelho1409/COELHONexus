"""A/B of the Book Distiller's page extractor: Docling (layout models, CPU) against vision models served through the LLM rotator.

The question: does a vision model read a book page better than Docling? If it does, Docling can leave the pipeline (and its torch image
with it). If Docling reads better, the extraction moves into the main server image and runs on the existing Celery workers. The verdict
rule is written BEFORE the run (see VERDICT below), so the numbers cannot be bent to a conclusion afterwards.

Run order (Docling is part of the server dependencies; the VLM arm needs only httpx):

    cd apps/server && uv sync
    PY="uv run --group bd python"                       # or the bd venv's python
    $PY ../../scripts/bd/ab_ingestion.py sample --books A.pdf B.pdf C.pdf --per-stratum 2 --seed 7
    $PY ../../scripts/bd/ab_ingestion.py docling
    BD_AB_URL=http://<rotator>/api/v1/llm/openai/v1 $PY ../../scripts/bd/ab_ingestion.py vlm --model llama-3.2-90b-vision --rpm 20
    BD_AB_URL=...                                     $PY ../../scripts/bd/ab_ingestion.py judge --a docling --b vlm-llama-3.2-90b-vision --judge-model gemini-2.5-pro
    $PY ../../scripts/bd/ab_ingestion.py report docling vlm-llama-3.2-90b-vision

Everything is cached per (arm, page) under --out (default /tmp/bd_ab), so a run that dies on a rate limit continues where it was.

What is measured, per page, against the PDF's own text layer (near-perfect ground truth for the TEXT of a born-digital page):
  * text recall / precision / F1 over word tokens (a multiset overlap: recall = what the page says that the output has; precision = what
    the output says that the page has, i.e. the opposite of hallucination);
  * heading recall: the PDF outline entries that fall on the page, found as a markdown heading in the output;
  * structure proxies: a code page has a fenced block, a table page has a pipe table;
  * seconds per page and the failure rate;
  * a blind pairwise judge (a vision model shown the page image and both outputs in random order) for what the text layer cannot say:
    tables, code indentation, lists, figures.
A "scan" stratum rasterizes real pages into image-only PDFs, so Docling must OCR them and the vision models must read pixels: the case
where a vision model is expected to win.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

SERVER = Path(__file__).resolve().parents[2] / "apps" / "server"
sys.path.insert(0, str(SERVER))
for _name, _value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(_name, _value)

STRATA = ("prose", "code", "figure", "table", "scan")
RENDER_DPI = 144

# The verdict, decided before any number exists. A vision model replaces Docling only if it is clearly better on born-digital pages
# (not just on scans) AND reliable; otherwise Docling stays, and it moves into the main image.
#
# Revised 2026-11-02, after Docling's own numbers (median text F1 0.98) and before any vision-model number existed: the first draft asked
# the VLM to beat Docling's text F1 by 0.02, which a score capped at 1.0 cannot do. Text must only be NOT WORSE; the win has to show
# in what the text layer cannot score (tables, code, lists, scans), which is what the judge is for.
VERDICT = {
    "vlm_f1_floor": -0.01,          # the VLM's median text F1 over the born-digital strata may trail Docling's by at most this …
    "vlm_judge_win_rate": 0.60,     # … and must win at least this share of the judge's decided born-digital pairs …
    "vlm_max_failure_rate": 0.05,   # … with at most this share of failed / empty pages.
}

VLM_PROMPT = (
    "Convert this book page to GitHub-flavored Markdown.\n"
    "- Transcribe ALL the text exactly as printed; do not summarize, correct, translate or add anything.\n"
    "- Use # to ###### for headings, pipe tables for tables, ``` fenced blocks (keeping indentation) for code, - / 1. for lists.\n"
    "- Leave out running headers, footers and page numbers. Mark a figure as ![figure](), keeping its caption as text.\n"
    "- Reply with the Markdown only, no commentary and no code fence around it."
)

JUDGE_PROMPT = (
    "You compare two Markdown transcriptions (Candidate 1 and Candidate 2) of the SAME book page, shown as an image.\n"
    "Judge which transcription is more faithful to the page: complete text, nothing invented, tables / code (with indentation) / lists "
    "/ headings correctly structured. Ignore running headers, footers and page numbers.\n\n"
    "Candidate 1:\n<<<\n{one}\n>>>\n\nCandidate 2:\n<<<\n{two}\n>>>\n\n"
    'Reply with JSON only: {{"winner": "1" | "2" | "tie", "omissions": {{"1": n, "2": n}}, "invented": {{"1": n, "2": n}}, '
    '"structure_better": "1" | "2" | "tie", "note": "one short sentence"}}'
)


# ---------------------------------------------------------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------------------------------------------------------

def tokens(text: str) -> list[str]:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower())


def strip_wrapping_fence(markdown: str) -> str:
    """A model told not to fence its reply sometimes does; the page's own code blocks are inside it."""
    stripped = markdown.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", stripped, flags=re.DOTALL)
    return match.group(1) if match else stripped


def text_scores(reference: str, output: str) -> dict:
    ref, out = Counter(tokens(reference)), Counter(tokens(output))
    overlap = sum((ref & out).values())
    n_ref, n_out = sum(ref.values()), sum(out.values())
    recall = overlap / n_ref if n_ref else 1.0
    precision = overlap / n_out if n_out else (1.0 if not n_ref else 0.0)
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    return {"recall": recall, "precision": precision, "f1": f1}


def headings_of(markdown: str) -> list[str]:
    return [re.sub(r"^#{1,6}\s+", "", line).strip(" #*") for line in markdown.splitlines() if re.match(r"^#{1,6}\s+\S", line)]


def heading_recall(titles: list[str], markdown: str) -> float | None:
    if not titles:
        return None
    found = {" ".join(tokens(h)) for h in headings_of(markdown)}
    hits = sum(1 for t in titles if " ".join(tokens(t)) in found or any(" ".join(tokens(t)) in f for f in found))
    return hits / len(titles)


def has_fence(markdown: str) -> bool:
    return markdown.count("```") >= 2


def has_pipe_table(markdown: str) -> bool:
    return bool(re.search(r"^\s*\|.*\|\s*\n\s*\|?\s*:?-{2,}", markdown, flags=re.MULTILINE))


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


# ---------------------------------------------------------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------------------------------------------------------

def page_features(page) -> dict:
    textpage = page.get_textpage()
    n_chars = textpage.count_chars()
    mono = 0
    probes = 0
    for index in range(0, n_chars, max(1, n_chars // 30)):
        buffer = ctypes.create_string_buffer(128)
        flags = ctypes.c_int(0)
        if pdfium_raw.FPDFText_GetFontInfo(textpage, index, buffer, 128, ctypes.byref(flags)):
            probes += 1
            mono += bool(re.search(r"mono|courier|consolas|code|typewriter", buffer.value.decode(errors="ignore"), re.I))
    width, height = page.get_size()
    image_area = 0.0
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]):
        left, bottom, right, top = obj.get_bounds()
        image_area += max(0.0, right - left) * max(0.0, top - bottom)
    text = textpage.get_text_range()
    lines = [line for line in text.splitlines() if line.strip()]
    short_lines = sum(len(line.split()) <= 4 for line in lines) / max(1, len(lines))
    return {"chars": n_chars, "mono": mono / probes if probes else 0.0, "image_frac": image_area / (width * height),
            "table_caption": bool(re.search(r"\bTable\s+\d", text)), "short_lines": short_lines, "lines": len(lines)}


def stratum_of(features: dict) -> str | None:
    if features["chars"] < 400:
        return None
    if features["mono"] >= 0.35:
        return "code"
    if features["image_frac"] >= 0.12:
        return "figure"
    if features["table_caption"] and features["short_lines"] >= 0.45 and features["lines"] >= 12:
        return "table"
    if features["mono"] < 0.05 and features["chars"] >= 1500:
        return "prose"
    return None


def command_sample(args) -> None:
    out = Path(args.out)
    for sub in ("pages", "ref", "scans"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    items: list[dict] = []
    for book_path in args.books:
        pdf = pdfium.PdfDocument(book_path)
        outline: dict[int, list[str]] = {}
        for bookmark in pdf.get_toc():
            destination = bookmark.get_dest()
            if destination is not None:
                outline.setdefault(destination.get_index() + 1, []).append(" ".join(bookmark.get_title().split()))
        by_stratum: dict[str, list[int]] = {s: [] for s in STRATA if s != "scan"}
        candidates = list(range(10, len(pdf) - 5))
        rng.shuffle(candidates)
        for index in candidates:
            if all(len(by_stratum[s]) >= args.per_stratum * 4 for s in by_stratum):
                break
            stratum = stratum_of(page_features(pdf[index]))
            if stratum and len(by_stratum[stratum]) < args.per_stratum * 4:
                by_stratum[stratum].append(index)
        book_key = re.sub(r"\W+", "-", Path(book_path).stem.lower())[:24].strip("-")
        for stratum, indexes in by_stratum.items():
            # pages with an outline entry first: they are the ones heading recall can be measured on
            indexes.sort(key=lambda i: (i + 1) not in outline)
            for index in indexes[: args.per_stratum]:
                items.append({"id": f"{book_key}-{index + 1:04d}", "book": book_path, "page": index + 1, "stratum": stratum,
                              "outline": outline.get(index + 1, [])})
        scan_pool = [i for i in by_stratum["prose"] if f"{book_key}-{i + 1:04d}" not in {x["id"] for x in items}]
        for index in scan_pool[: args.per_stratum]:
            items.append({"id": f"{book_key}-{index + 1:04d}-scan", "book": book_path, "page": index + 1, "stratum": "scan",
                          "outline": outline.get(index + 1, [])})
    for item in items:
        pdf = pdfium.PdfDocument(item["book"])
        page = pdf[item["page"] - 1]
        image = page.render(scale = RENDER_DPI / 72).to_pil().convert("RGB")
        image.save(out / "pages" / f"{item['id']}.png")
        (out / "ref" / f"{item['id']}.txt").write_text(page.get_textpage().get_text_range())
        if item["stratum"] == "scan":
            image.save(out / "scans" / f"{item['id']}.pdf", "PDF", resolution = RENDER_DPI)
    (out / "sample.json").write_text(json.dumps(items, indent = 1))
    print(f"sampled {len(items)} pages -> {out / 'sample.json'}")
    print(Counter(i["stratum"] for i in items))


# ---------------------------------------------------------------------------------------------------------------------------
# arms
# ---------------------------------------------------------------------------------------------------------------------------

def load_sample(out: Path) -> list[dict]:
    return json.loads((out / "sample.json").read_text())


def save_result(out: Path, arm: str, item: dict, **fields) -> None:
    folder = out / "arms" / arm
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item['id']}.json").write_text(json.dumps({"id": item["id"], **fields}))


def read_result(out: Path, arm: str, item_id: str) -> dict | None:
    path = out / "arms" / arm / f"{item_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def command_docling(args) -> None:
    import dataclasses

    import domains
    import domains.bd.ingestion.extract.service as extract

    out = Path(args.out)
    for item in load_sample(out):
        if read_result(out, "docling", item["id"]):
            continue
        scan = item["stratum"] == "scan"
        domains.bd.ingestion.extract.config.DOCLING = dataclasses.replace(domains.bd.ingestion.extract.config.DOCLING, ocr = scan)
        extract.converter.cache_clear()
        source, page_no = (str(out / "scans" / f"{item['id']}.pdf"), 1) if scan else (item["book"], item["page"])
        started = time.time()
        try:
            result = extract.convert_slice(source, page_no, page_no)
            markdown = result.pages[0].markdown if result.pages else ""
            save_result(out, "docling", item, markdown = markdown, seconds = time.time() - started, error = None)
        except Exception as e:  # noqa: BLE001 — a failed page is a data point
            save_result(out, "docling", item, markdown = "", seconds = time.time() - started, error = f"{type(e).__name__}: {e}")
        print(f"docling {item['id']} {time.time() - started:.1f}s")


class Endpoint:
    """An OpenAI-compatible chat endpoint (the rotator), paced to a requests-per-minute budget."""

    def __init__(self, url: str, key: str, rpm: float) -> None:
        self.url, self.key, self.interval = url.rstrip("/"), key, 60.0 / rpm
        self.client = httpx.Client(timeout = httpx.Timeout(30.0, read = 300.0))
        self._next = 0.0
        self._pacing = threading.Lock()

    def chat(self, model: str, content: list[dict], max_tokens: int = 4096) -> tuple[str, dict]:
        with self._pacing:                       # calls start at least `interval` apart, whichever thread makes them
            wait = self._next - time.time()
            if wait > 0:
                time.sleep(wait)
            self._next = time.time() + self.interval
        for attempt in range(4):
            response = self.client.post(
                f"{self.url}/chat/completions",
                headers = {"Authorization": f"Bearer {self.key}"} if self.key else {},
                json = {"model": model, "temperature": 0, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]},
            )
            if response.status_code in (429, 502, 503, 504):
                time.sleep(float(response.headers.get("retry-after", 5 * (attempt + 1))))
                continue
            response.raise_for_status()
            body = response.json()
            return body["choices"][0]["message"]["content"] or "", {"usage": body.get("usage"), "served_by": response.headers.get("x-rotator-model")}
        raise RuntimeError("endpoint kept answering 429/5xx")


def image_block(path: Path) -> dict:
    import base64

    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}}


def command_vlm(args) -> None:
    out = Path(args.out)
    endpoint = Endpoint(args.url, args.key, args.rpm)
    arm = f"vlm-{args.model}"

    def one(item: dict) -> None:
        started = time.time()
        try:
            text, meta = endpoint.chat(args.model, [{"type": "text", "text": VLM_PROMPT}, image_block(out / "pages" / f"{item['id']}.png")],
                                       max_tokens = args.max_tokens)
            save_result(out, arm, item, markdown = strip_wrapping_fence(text), seconds = time.time() - started, error = None, **meta)
        except Exception as e:  # noqa: BLE001 — a failed page is a data point
            save_result(out, arm, item, markdown = "", seconds = time.time() - started, error = f"{type(e).__name__}: {e}")
        print(f"{arm} {item['id']} {time.time() - started:.1f}s", flush = True)

    pending = [item for item in load_sample(out) if not read_result(out, arm, item["id"])]
    with ThreadPoolExecutor(max_workers = args.workers) as pool:       # a reasoning model takes ~17 s a page: several at once
        list(pool.map(one, pending))


def command_judge(args) -> None:
    out = Path(args.out)
    endpoint = Endpoint(args.url, args.key, args.rpm)
    verdicts_path = out / f"judge-{args.a}-vs-{args.b}.json"
    verdicts = json.loads(verdicts_path.read_text()) if verdicts_path.exists() else {}
    saving = threading.Lock()

    def one(item: dict) -> None:
        a, b = read_result(out, args.a, item["id"]), read_result(out, args.b, item["id"])
        flipped = random.Random(item["id"]).random() < 0.5
        first, second = (b, a) if flipped else (a, b)
        prompt = JUDGE_PROMPT.format(one = first["markdown"][:12000], two = second["markdown"][:12000])
        try:
            text, _ = endpoint.chat(args.judge_model, [{"type": "text", "text": prompt}, image_block(out / "pages" / f"{item['id']}.png")], max_tokens = 2048)
            import json_repair

            verdict = json_repair.loads(text)
            winner = verdict.get("winner")
            mapped = {"1": args.b if flipped else args.a, "2": args.a if flipped else args.b}.get(str(winner), "tie")
            result = {"winner": mapped, "raw": verdict, "flipped": flipped}
        except Exception as e:  # noqa: BLE001
            result = {"winner": "error", "raw": f"{type(e).__name__}: {e}", "flipped": flipped}
        with saving:
            verdicts[item["id"]] = result
            verdicts_path.write_text(json.dumps(verdicts, indent = 1))
        print(f"judge {item['id']} -> {result['winner']}", flush = True)

    pending = []
    for item in load_sample(out):
        if item["stratum"] == "scan":
            continue
        a, b = read_result(out, args.a, item["id"]), read_result(out, args.b, item["id"])
        if item["id"] not in verdicts and a and b and not a["error"] and not b["error"]:
            pending.append(item)
    with ThreadPoolExecutor(max_workers = args.workers) as pool:
        list(pool.map(one, pending))


# ---------------------------------------------------------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------------------------------------------------------

def arm_rows(out: Path, arm: str) -> list[dict]:
    rows = []
    for item in load_sample(out):
        result = read_result(out, arm, item["id"])
        if result is None:
            continue
        reference = (out / "ref" / f"{item['id']}.txt").read_text()
        markdown = result["markdown"]
        failed = bool(result["error"]) or len(markdown.strip()) == 0
        rows.append({"item": item, "failed": failed, "seconds": result["seconds"], "markdown": markdown,
                     **({} if failed else text_scores(reference, markdown)),
                     "heading_recall": None if failed else heading_recall(item["outline"], markdown),
                     "fence": has_fence(markdown), "table": has_pipe_table(markdown)})
    return rows


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["failed"]]
    return {
        "n": len(rows), "failed": len(rows) - len(ok),
        "recall": median([r["recall"] for r in ok]), "precision": median([r["precision"] for r in ok]), "f1": median([r["f1"] for r in ok]),
        "heading_recall": median([r["heading_recall"] for r in ok if r["heading_recall"] is not None]),
        "seconds": median([r["seconds"] for r in rows]),
        "code_fenced": (sum(r["fence"] for r in ok) / len(ok)) if ok else None,
        "table_found": (sum(r["table"] for r in ok) / len(ok)) if ok else None,
    }


def fmt(value, digits = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def command_report(args) -> None:
    out = Path(args.out)
    sample = load_sample(out)
    items_by_id = {item["id"]: item for item in sample}
    per_arm = {arm: arm_rows(out, arm) for arm in args.arms}
    print("| arm | stratum | n | failed | recall | precision | F1 | heading recall | fenced (code) | table found | s/page |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for arm, rows in per_arm.items():
        for stratum in (*STRATA, "all"):
            subset = rows if stratum == "all" else [r for r in rows if r["item"]["stratum"] == stratum]
            if not subset:
                continue
            s = summarize(subset)
            # the structure proxies only mean something on the pages that have the structure
            fenced = s["code_fenced"] if stratum == "code" else None
            table = s["table_found"] if stratum == "table" else None
            print(f"| {arm} | {stratum} | {s['n']} | {s['failed']} | {fmt(s['recall'])} | {fmt(s['precision'])} | {fmt(s['f1'])} | "
                  f"{fmt(s['heading_recall'])} | {fmt(fenced, 2)} | {fmt(table, 2)} | {fmt(s['seconds'], 1)} |")
    verdict_files = sorted(out.glob("judge-*.json"))
    judge_rates: dict[tuple[str, str], float | None] = {}
    for path in verdict_files:
        verdicts = json.loads(path.read_text())
        a, b = path.stem[len("judge-"):].split("-vs-")
        wins = Counter(
            verdict["winner"] for item_id, verdict in verdicts.items()
            if item_id in items_by_id and items_by_id[item_id]["stratum"] != "scan"
        )
        decided = wins[a] + wins[b]
        judge_rates[(a, b)] = wins[b] / decided if decided else None
        print(f"\njudge {a} vs {b} (born-digital): {dict(wins)}" + (f" -> {b} wins {wins[b] / decided:.0%} of decided pairs" if decided else " -> no decided pairs"))
    if "docling" in per_arm:
        for arm, rows in per_arm.items():
            if arm == "docling":
                continue
            born = lambda rs: [r for r in rs if r["item"]["stratum"] != "scan"]
            d, v = summarize(born(per_arm["docling"])), summarize(born(rows))
            fail_rate = v["failed"] / v["n"] if v["n"] else 1.0
            margin = v["f1"] - d["f1"] if v["f1"] is not None and d["f1"] is not None else None
            judge_rate = judge_rates.get(("docling", arm))
            gates = (
                margin is not None and margin >= VERDICT["vlm_f1_floor"],
                fail_rate <= VERDICT["vlm_max_failure_rate"],
                None if judge_rate is None else judge_rate >= VERDICT["vlm_judge_win_rate"],
            )
            eligible = False if False in gates else (None if None in gates else True)
            outcome = "qualifies to replace Docling" if eligible is True else ("inconclusive" if eligible is None else "does not qualify")
            margin_text = "not measured" if margin is None else f"{margin:+.3f} (needs ≥ {VERDICT['vlm_f1_floor']:+.2f})"
            judge_text = "not measured" if judge_rate is None else f"{judge_rate:.0%} (needs ≥ {VERDICT['vlm_judge_win_rate']:.0%})"
            print(f"\nverdict inputs for {arm} (born-digital): F1 delta {margin_text}; "
                  f"failure rate {fail_rate:.0%} (needs ≤ {VERDICT['vlm_max_failure_rate']:.0%}); judge win rate {judge_text}; verdict: {outcome}.")


def main() -> None:
    parser = argparse.ArgumentParser(description = __doc__.split("\n")[0])
    parser.add_argument("--out", default = "/tmp/bd_ab")
    sub = parser.add_subparsers(dest = "command", required = True)
    p = sub.add_parser("sample"); p.add_argument("--books", nargs = "+", required = True); p.add_argument("--per-stratum", type = int, default = 2); p.add_argument("--seed", type = int, default = 7)
    sub.add_parser("docling")
    for name in ("vlm", "judge"):
        p = sub.add_parser(name)
        p.add_argument("--url", default = os.environ.get("BD_AB_URL", ""))
        p.add_argument("--key", default = os.environ.get("BD_AB_KEY", ""))
        p.add_argument("--rpm", type = float, default = 20.0)
        p.add_argument("--workers", type = int, default = 4, help = "pages in flight at once (the rpm budget still applies)")
        if name == "vlm":
            p.add_argument("--model", required = True)
            # a reasoning model thinks before it transcribes: at 4096 the dense pages (code, figures) spent the whole budget thinking
            p.add_argument("--max-tokens", type = int, default = 16000)
        else:
            p.add_argument("--a", required = True); p.add_argument("--b", required = True); p.add_argument("--judge-model", required = True)
    p = sub.add_parser("report"); p.add_argument("arms", nargs = "+")
    args = parser.parse_args()
    if args.command in ("vlm", "judge") and not args.url:
        sys.exit("set BD_AB_URL (or --url) to the rotator's OpenAI-compatible base URL")
    {"sample": command_sample, "docling": command_docling, "vlm": command_vlm, "judge": command_judge, "report": command_report}[args.command](args)


if __name__ == "__main__":
    main()
