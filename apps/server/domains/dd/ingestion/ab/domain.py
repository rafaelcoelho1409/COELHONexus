"""A/B — pure measures of what an arm made of a source, and the rule that says which arm won. No I/O, no model."""
from __future__ import annotations
import domains
from . import entities, params, patterns

import html
from collections import Counter

from markdown_it import MarkdownIt


# ---- reading a text the way a reader does ---------------------------------------------------------------------------------
def analyze(text: str) -> entities.Analysis:
    """The code blocks of `text` (as CommonMark reads them) and the lines outside them, with the prose's words counted. HTML blocks are
    not recognised (a bare `<pre />` would hide every fence after it), as in the normalizer."""
    lines = text.split("\n")
    fenced: set[int] = set()
    fences: list[str] = []
    for token in MarkdownIt("commonmark").disable("html_block").parse(text):
        if token.type == "fence" and token.map:
            fenced.update(range(token.map[0], token.map[1]))
            fences.append(token.content.rstrip("\n"))
    prose_lines = [line for i, line in enumerate(lines) if i not in fenced]
    prose = html.unescape("\n".join(prose_lines))
    prose = patterns.BARE_URL_RE.sub(" ", patterns.LINK_DESTINATION_RE.sub("]", prose))
    return entities.Analysis(fences = fences, prose_lines = prose_lines, tokens = Counter(w.lower() for w in patterns.WORD_RE.findall(prose)))


def merged(analyses: list[entities.Analysis]) -> entities.Analysis:
    total = entities.Analysis(fences = [], prose_lines = [], tokens = Counter())
    for a in analyses:
        total.fences.extend(a.fences)
        total.prose_lines.extend(a.prose_lines)
        total.tokens.update(a.tokens)
    return total


# ---- fidelity: nothing lost, nothing invented, no code damaged ----------------------------------------------------------------
def tolerant_fence(block: str) -> str:
    """A code block compared the way a declared change leaves it: entities decoded (a bundle that escaped `->` as `-&gt;`), a site's
    highlighter annotations and trailing blanks off."""
    lines = [patterns.CODE_ANNOTATION_RE.sub("", line).rstrip() for line in html.unescape(block).split("\n")]
    return "\n".join(lines).strip()


def fence_fidelity(reference: entities.Analysis, output: entities.Analysis) -> dict:
    """How many of the reference's distinct code blocks are not, byte for byte, in the output (`strict_changed`), and how many of those
    are not there either once declared changes are allowed for (`tolerant_changed`: lost or rewritten)."""
    ref, out = set(reference.fences), set(output.fences)
    strict = [block for block in ref if block not in out]
    tolerant_out = {tolerant_fence(block) for block in out}
    return {
        "distinct": len(ref),
        "strict_changed": len(strict),
        "tolerant_changed": sum(1 for block in strict if tolerant_fence(block) not in tolerant_out),
    }


def token_overlap(reference: entities.Analysis, output: entities.Analysis) -> dict:
    """The prose's words as a multiset: `recall` = how much of what the source says the output still says, `precision` = how much of what
    the output says the source had (1 − precision is what was invented or rewritten)."""
    shared = sum((reference.tokens & output.tokens).values())
    ref_total, out_total = sum(reference.tokens.values()), sum(output.tokens.values())
    return {"recall": shared / ref_total if ref_total else 1.0, "precision": shared / out_total if out_total else 1.0}


# ---- noise: what a reader meets that is not the page ----------------------------------------------------------------------------
def noise_counts(prose_lines: list[str]) -> dict[str, int]:
    counts: Counter = Counter()
    for line in prose_lines:
        for name, pattern in patterns.NOISE.items():
            if pattern.search(line):
                counts[name] += 1
    return dict(counts)


# ---- structure: the pages themselves -------------------------------------------------------------------------------------------
def first_heading(prose_lines: list[str]) -> str:
    return next((line[2:].strip() for line in prose_lines if line.startswith("# ")), "")


def title_key(text: str) -> str:
    """A title as two spellings of it can be compared: no link syntax or marks, lower case, letters and digits only."""
    plain = patterns.INLINE_MARKS_RE.sub("", patterns.MD_LINK_TEXT_RE.sub(r"\1", text))
    return patterns.NON_ALNUM_RE.sub(" ", html.unescape(plain).lower()).strip()


def _nav_key(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0].rstrip("/").removesuffix(".md")


def nav_agreement(pages: list, headings: list[str], nav_links: list, path_filter: dict | None) -> dict:
    """How the pages line up with the site's own index (its `llms.txt`) — an oracle neither arm wrote. Only the index's entries inside the
    catalog's `path_filter` count. `source_*`: by the `Source:` URL a page carries (None for a bundle with none); `title_*`: by the page's
    H1 against the title the index gives it; `pages_per_nav` > 1 is a bundle cut too small, < 1 pages merged."""
    flt = domains.dd.ingestion.filters.domain.declared_filter(path_filter)
    scoped = [
        (title, url) for title, url, _section in nav_links
        if flt is None or domains.dd.ingestion.filters.domain.passes_path_filter(url, flt)
    ]
    if not scoped or not pages:
        return {"nav": len(scoped), "agreement": None}
    nav_urls = {_nav_key(url) for _title, url in scoped}
    sources = {_nav_key(page.source_url) for page in pages if page.source_url}
    heading_keys = {title_key(h) for h in headings if h}
    nav_titles = [title_key(title) for title, _url in scoped if title]

    def loose(title: str) -> bool:
        if title in heading_keys:
            return True
        return len(title) >= params.TITLE_LOOSE_MIN_CHARS and any(
            len(h) >= params.TITLE_LOOSE_MIN_CHARS and (title in h or h in title) for h in heading_keys
        )

    exact = sum(1 for title in nav_titles if title in heading_keys) / max(1, len(nav_titles))
    loosely = sum(1 for title in nav_titles if loose(title)) / max(1, len(nav_titles))
    per_nav = len(pages) / len(scoped)
    result = {
        "nav": len(scoped), "pages_per_nav": per_nav, "title_exact": exact, "title_loose": loosely,
        "source_coverage": None, "source_spurious": None,
    }
    if sources:
        result["source_coverage"] = len(nav_urls & sources) / len(nav_urls)
        result["source_spurious"] = len(sources - nav_urls) / len(sources)
        result["agreement"] = result["source_coverage"] * (1 - result["source_spurious"])
    else:
        result["agreement"] = loosely * min(1.0, 1 / per_nav)
    return result


def scorecard(
    inputs: entities.Inputs,
    pages: list,
    *,
    reference: entities.Analysis | None = None,
) -> dict:
    """Every measure of one arm's pages for one source (`reference`: `analyze(inputs.text)` when the caller has it already)."""
    reference = reference or analyze(inputs.text)
    per_page = [analyze(page.body) for page in pages]
    total = merged(per_page)
    sizes = [len(page.body.encode("utf-8")) for page in pages]
    oversized_at = domains.dd.ingestion.post.params.OVERSIZED_SPLIT_BYTES
    headings = [first_heading(a.prose_lines) for a in per_page]
    # What a page is called, for the index's titles to be found among: its H1, and the last part of its title — dbt's pages all open
    # `# dbt Developer Hub` and are named "dbt Developer Hub › About the Discovery API".
    names = headings + [page.title.rsplit(" › ", 1)[-1] for page in pages]
    noise = noise_counts(total.prose_lines)
    total_bytes = sum(sizes)
    small = sum(1 for s in sizes if s < params.SMALL_PAGE_BYTES)
    oversized = sum(1 for s in sizes if s > oversized_at)
    fences = fence_fidelity(reference, total)
    card = {
        "pages": len(pages), "bytes": total_bytes, "reference_bytes": len(inputs.text.encode("utf-8")),
        "small_pages": small, "oversized_pages": oversized, "wrong_size_share": (small + oversized) / max(1, len(pages)),
        "no_h1": sum(1 for h in headings if not h),
        "duplicate_titles": len(pages) - len({p.title.casefold() for p in pages}),
        "duplicate_bodies": len(pages) - len({p.body for p in pages}),
        "sections": len({p.section for p in pages if p.section}),
        "fences": fences, "code_changed_share": fences["tolerant_changed"] / max(1, fences["distinct"]),
        **token_overlap(reference, total),
        "noise": noise, "noise_per_mb": sum(noise.values()) / max(total_bytes / 1e6, 1e-9),
        "reference_noise": noise_counts(reference.prose_lines),
        "nav": nav_agreement(pages, names, inputs.nav_links, inputs.params.get("path_filter")),
    }
    card["agreement"] = card["nav"].get("agreement")
    return card


# ---- the verdict ----------------------------------------------------------------------------------------------------------------
def _compare(a: float, b: float, *, lower_is_better: bool, slack: float, relative: bool) -> str:
    """`better`, `worse` or `same` for B against A: a difference inside the slack is `same`."""
    margin = slack * abs(a) if relative else slack
    delta = (a - b) if lower_is_better else (b - a)   # > 0: B is better
    if delta > margin:
        return "better"
    if delta < -margin:
        return "worse"
    return "same"


def verdict(a: dict, b: dict, *, judge_win_rate: float | None = None) -> dict:
    """Arm B against arm A for one source, by the rule fixed in `params.VERDICT` before any B was run: gates first (nothing invented,
    nothing lost, no code damaged), then three measures (noise, agreement with the site's index, pages of the wrong size) and, when a
    blind judge ran, its win rate. `winner` is `B` only for a B that clears the gates and is better on enough measures without being
    worse on any; `A` when B fails a gate or is worse on enough; otherwise `tie` — which keeps A."""
    rules = params.VERDICT
    gates = {
        "precision": b["precision"] >= rules.min_token_precision,
        "recall": b["recall"] >= a["recall"] - rules.recall_slack,
        "code": b["code_changed_share"] <= a["code_changed_share"] + rules.code_slack,
    }
    measures = {
        "noise": _compare(a["noise_per_mb"], b["noise_per_mb"], lower_is_better = True, slack = rules.noise_slack, relative = True),
        "wrong_size": _compare(a["wrong_size_share"], b["wrong_size_share"], lower_is_better = True, slack = rules.wrong_size_slack, relative = False),
    }
    if a["agreement"] is not None and b["agreement"] is not None:
        measures["agreement"] = _compare(a["agreement"], b["agreement"], lower_is_better = False, slack = rules.agreement_slack, relative = False)
    if judge_win_rate is not None:
        measures["judge"] = "better" if judge_win_rate >= rules.judge_min_win_rate else ("worse" if judge_win_rate <= 1 - rules.judge_min_win_rate else "same")
    better = sum(1 for m in measures.values() if m == "better")
    worse = sum(1 for m in measures.values() if m == "worse")
    if not all(gates.values()):
        winner = "A"
    elif better >= rules.wins_needed and worse == 0:
        winner = "B"
    elif worse >= rules.wins_needed and better == 0:
        winner = "A"
    else:
        winner = "tie"
    return {"winner": winner, "gates": gates, "measures": measures}
