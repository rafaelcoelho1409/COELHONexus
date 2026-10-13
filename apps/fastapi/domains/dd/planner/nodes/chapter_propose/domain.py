"""chapter_propose — pure helpers (target sizing, structural seed
extraction, summary helper, manifest hash). Prompt builders live in
prompts.py; Pydantic schemas in schemas.py. LLM output decoding is
`chat_structured_async` — no manual JSON parse needed here anymore."""
from __future__ import annotations
import domains
from . import params, patterns, schemas, versions

import json
import math
import os
from collections import Counter
from hashlib import sha256
from typing import Optional



def target_chapters_for_n_docs(n_docs: int) -> int:
    """Per-corpus target chapter count (guides the proposer + optimal-
    stopping floor). Clamped to [PROPOSALS_TARGET_FLOOR,
    PROPOSALS_TARGET_CEILING]."""
    if n_docs <= 0:
        return params.PROPOSALS_TARGET_FLOOR
    return min(
        params.PROPOSALS_TARGET_CEILING,
        max(params.PROPOSALS_TARGET_FLOOR, round(n_docs / params.PROPOSALS_DIVISOR)),
    )


def _extract_h12_headings(body: str, max_n: int) -> list[str]:
    """First N H1/H2 headings from a markdown body. Fenced code blocks are
    stripped first — see FENCED_CODE_RE."""
    out: list[str] = []
    body_no_code = patterns.FENCED_CODE_RE.sub("", body or "")
    for m in patterns.H2_RE.finditer(body_no_code):
        h = " ".join(m.group(1).strip().split())
        h = patterns.LEADING_MD_LINK_RE.sub("", h)
        h = patterns.TRAILING_MD_LINK_RE.sub("", h).strip()
        if not h or h.casefold() in params.GENERIC_HEADINGS:
            continue
        out.append(h)
        if len(out) >= max_n:
            break
    return out


def _namespace_from_key(source_key: str) -> Optional[str]:
    """Extract a 'namespace' from a source key. Captures CLI subcommand
    patterns + file-tree top-level directories under `commands/`."""
    m = patterns.CLI_PATTERN_RE.search(source_key)
    if m:
        return m.group(1).lower()
    parts = source_key.split("/")
    if len(parts) >= 4 and parts[0] == "ingestion":
        # e.g. ingestion/claude-code/pages/0012-foo.md → "pages"
        return parts[2].lower()
    return None


def extract_structural_seeds(
    *,
    source_keys: list[str],
    bodies_by_key: dict[str, str],
) -> dict:
    """Top-level headings + CLI namespace seeds from the corpus for chapter_propose."""
    headings_counter: Counter[str] = Counter()
    for key in source_keys:
        body = bodies_by_key.get(key) or ""
        for h in _extract_h12_headings(body, max_n = 4):
            headings_counter[h] += 1

    namespaces_counter: Counter[str] = Counter()
    for key in source_keys:
        ns = _namespace_from_key(key)
        if ns:
            namespaces_counter[ns] += 1

    # chapter seed. Keep ones that occur ≥ 2.
    seed_headings = [
        h for h, n in headings_counter.most_common(params.SEED_MAX_HEADINGS)
        if n >= 2
    ][:params.SEED_MAX_HEADINGS]
    seed_namespaces = [
        ns for ns, n in namespaces_counter.most_common(params.SEED_MAX_NAMESPACES)
        if n >= 2
    ]

    return {
        "headings":   seed_headings,
        "namespaces": seed_namespaces,
    }


def build_source_structure(entries: list[dict]) -> str:
    """The docs' own structure as a prompt block: parent pages in navigation order
    (manifest `idx`) with their sub-page counts — the strongest free prior for
    chapter grouping and order. "" unless the corpus has multi-page families
    (a flat site or a single-URL dump has no kinship to report). Capped at
    SOURCE_STRUCTURE_MAX_LINES lines (largest families kept, order preserved)."""
    fams: dict[str, dict] = {}
    for e in entries or []:
        url = (e.get("url") or "").split("#", 1)[0]
        if not url or not e.get("key"):
            continue
        f = fams.setdefault(url, {"idx": e.get("idx", 0), "n": 0, "title": ""})
        f["n"] += 1
        f["idx"] = min(f["idx"], e.get("idx", 0))
        if not f["title"]:
            f["title"] = (e.get("title") or "").split(" — ")[0].strip()
    multi = [f for f in fams.values() if f["n"] >= 2]
    sections = _site_sections(fams)
    if (not multi and not sections) or len(fams) <= 1:
        return ""
    keep = sorted(multi, key = lambda f: -f["n"])[:params.SOURCE_STRUCTURE_MAX_LINES]
    keep.sort(key = lambda f: f["idx"])
    lines = [f"  {i}. {f['title'] or '(untitled)'} ({f['n']} pages)" for i, f in enumerate(keep, 1)]
    n_single = sum(1 for f in fams.values() if f["n"] == 1)
    if n_single:
        lines.append(f"  (+ {n_single} single-page docs not listed)")
    if sections:
        lines.append(SECTIONS_HEADER)
        lines += [f"  - {label} ({n} pages)" for label, n in sections]
    return "\n".join(lines)


SECTIONS_HEADER = "  Site sections (URL directories holding many pages):"


def _site_sections(fams: dict[str, dict]) -> list[tuple[str, int]]:
    """[(directory label, n docs)] for URL directories with ≥ SECTION_MIN_DOCS direct
    docs that are not the site's trunk (≤ SECTION_MAX_SHARE of the corpus), in
    navigation order. Directories count their OWN pages only, so nested sections
    (integrations/ vs integrations/providers/) never overlap."""
    total = sum(f["n"] for f in fams.values())
    by_dir: dict[str, dict] = {}
    for url, f in fams.items():
        d = url.rsplit("/", 1)[0] if "/" in url.split("://", 1)[-1] else ""
        if not d:
            continue
        g = by_dir.setdefault(d, {"n": 0, "idx": f["idx"]})
        g["n"] += f["n"]
        g["idx"] = min(g["idx"], f["idx"])
    if len(by_dir) < 2:
        return []
    root = os.path.commonprefix(list(by_dir))
    root = root[: root.rfind("/") + 1] if "/" in root else ""
    out = []
    for d, g in by_dir.items():
        if g["n"] < params.SECTION_MIN_DOCS or g["n"] > params.SECTION_MAX_SHARE * total:
            continue
        label = d[len(root):].strip("/")
        if label:
            out.append((g["idx"], label, g["n"]))
    out.sort()
    return [(label, n) for _i, label, n in out[:params.SECTION_MAX_LINES]]


def build_fallback_proposals(
    framework: str, seeds: dict, target_chapters: int, n_docs: int,
) -> schemas.ChapterProposalList:
    """Deterministic fallback when all LLM samples fail (timeout/402). Uses
    structural seeds (headings/namespaces) so pipeline never returns 0 chapters
    and downstream never silently succeeds with empty plan. Mirrors doc_distill
    fallback distillate pattern."""
    headings = (seeds.get("headings") or [])[:]
    namespaces = (seeds.get("namespaces") or [])[:]
    # Target clamped to schema range
    n = max(params.PROPOSALS_MIN, min(params.PROPOSALS_MAX, target_chapters))
    generic = ["Core Concepts", "Configuration", "API Reference", "Guides", "Advanced Topics", "Troubleshooting", "Examples", "Best Practices"]

    # Dedup on the FINAL title (post word-count normalization below), case-
    # insensitively. Two reasons this has to happen in one place: (1)
    # headings_counter (domain.py) never case-normalizes, so two docs'
    # headings differing only in case (e.g. "How It Works" vs "How it
    # Works") both survive as distinct seed strings; (2) the old code
    # dedup-checked the RAW title but only applied the 2-8-word
    # normalization afterward in a separate loop, so two distinct raw
    # titles could still collide once truncated/suffixed. Either way a
    # case-sensitive "not in candidates" check here let both through, only
    # to collide on ChapterProposalList's case-insensitive uniqueness
    # validator — crashing the fallback itself with no further recovery
    # path. Confirmed live 2026-09-09 on the fastmcp corpus (duplicate
    # 'How It Works' after the LLM path had already failed all samples).
    candidates: list[str] = []
    seen: set[str] = set()

    def _try_add(raw_title: str) -> bool:
        words = raw_title.split()
        title = raw_title
        if len(words) < 2:
            title = title + " Overview"
        elif len(words) > 8:
            title = " ".join(words[:8])
        key = title.casefold()
        if key in seen:
            return False
        seen.add(key)
        candidates.append(title)
        return True

    # Prefer headings (human-written) then namespaces (file-tree) then generic.
    for h in headings:
        if len(candidates) >= n:
            break
        _try_add(h)
    for ns in namespaces:
        if len(candidates) >= n:
            break
        _try_add(ns.replace("-", " ").title())
    for g in generic:
        if len(candidates) >= n:
            break
        _try_add(g)

    proposals = [
        schemas.ChapterProposal(
            title = title,
            description = f"Covers {title.lower()} in {framework} based on structural signals from {n_docs} docs.",
            key_concepts = [title.lower().replace(" ", "_") + "_1", title.lower().replace(" ", "_") + "_2", title.lower().replace(" ", "_") + "_3"],
        )
        for title in candidates
    ]
    return schemas.ChapterProposalList(proposals = proposals)


def summarize_proposal(props: list[schemas.ChapterProposal], score: Optional[dict] = None) -> dict:
    """Compact summary for the USC vote picker (+ the evidence score when known)."""
    out = {
        "n_chapters":         len(props),
        "titles":             [p.title for p in props],
        "max_concept_count":  max(
            (len(p.key_concepts) for p in props), default = 0,
        ),
        "total_concepts":     sum(len(p.key_concepts) for p in props),
    }
    if score:
        out["unmatched_pct"] = score["unmatched_pct"]
        out["largest_share_pct"] = score["largest_share_pct"]
    return out


def propose_max_tokens(target_chapters: int) -> int:
    """First-draft token budget: scales with the chapter target, never below the floor."""
    return max(
        params.MAX_TOKENS_PROPOSE,
        min(params.MAX_TOKENS_PROPOSE_MAX, params.MAX_TOKENS_BASE + params.MAX_TOKENS_PER_CHAPTER * target_chapters),
    )


def retry_max_tokens(max_tokens: int) -> int:
    """Budget for re-asking a draft that was cut at max_tokens."""
    return min(params.MAX_TOKENS_RETRY_CAP, max(max_tokens, int(max_tokens * params.LENGTH_RETRY_FACTOR)))


def _words(text: str) -> set[str]:
    return {
        w for w in patterns.SCORE_WORD_RE.findall((text or "").lower())
        if w not in params.SCORE_STOP
    }


def doc_match_text(key: str, distillate: Optional[dict], body: str = "") -> str:
    """What a doc is matched on: page slug + distillate summary and terms (or the body head)."""
    slug = patterns.KEY_PREFIX_RE.sub("", os.path.basename(key or "").rsplit(".", 1)[0])
    d = distillate or {}
    text = " ".join([slug, d.get("summary") or "", " ".join(d.get("key_terms") or [])]).strip()
    if not (d.get("summary") or "").strip():
        text = f"{slug} {body or ''}"
    return text[:params.SCORE_DOC_CHARS]


def score_candidates(
    candidates: list[list[dict]], doc_texts: list[str], target_chapters: int,
) -> list[dict]:
    """Evidence score per proposal sample (higher = better), no LLM. Every doc is matched to
    its best chapter by IDF-weighted cosine over title + description + key concepts; a
    sample loses points for docs matching no chapter, for a chapter that would hold more than
    SCORE_SHARE_CAP of the docs, and for a chapter count far from the target. Returns, per
    candidate: {n, unmatched_pct, largest_share_pct, score}."""
    docs = [_words(t) for t in doc_texts]
    n_docs = len(docs)
    df = Counter(w for ws in docs for w in ws)
    idf = {w: math.log(1.0 + n_docs / c) for w, c in df.items()}
    doc_norm = [math.sqrt(sum(idf[w] ** 2 for w in ws)) or 1.0 for ws in docs]
    out = []
    for props in candidates:
        chapters = []
        for p in props:
            ws = _words(" ".join([p.get("title") or "", p.get("description") or "", " ".join(p.get("key_concepts") or [])]))
            chapters.append((ws, math.sqrt(sum(idf.get(w, math.log(1.0 + n_docs)) ** 2 for w in ws)) or 1.0))
        counts = [0] * len(chapters)
        unmatched = 0
        for ws, dn in zip(docs, doc_norm):
            best, best_i = 0.0, -1
            for i, (cw, cn) in enumerate(chapters):
                shared = ws & cw
                if not shared:
                    continue
                cos = sum(idf[w] ** 2 for w in shared) / (dn * cn)
                if cos > best:
                    best, best_i = cos, i
            if best_i < 0 or best < params.SCORE_MIN_MATCH:
                unmatched += 1
            else:
                counts[best_i] += 1
        n = len(chapters)
        unmatched_frac = unmatched / n_docs if n_docs else 0.0
        share = (max(counts) / n_docs) if n_docs and counts else 0.0
        t = max(1, target_chapters)
        gap = (t - n) / t if n < t else 0.5 * (n - t) / t
        score = (
            (1.0 - unmatched_frac)
            - params.SCORE_SHARE_WEIGHT * max(0.0, share - params.SCORE_SHARE_CAP)
            - params.SCORE_COUNT_WEIGHT * gap
        )
        out.append({
            "n": n,
            "unmatched_pct": round(100 * unmatched_frac, 1),
            "largest_share_pct": round(100 * share, 1),
            "score": round(score, 4),
        })
    return out


def shortlist(scores: list[dict], target_chapters: int) -> list[int]:
    """Candidate indices that go on to the tie-break: those within SCORE_TIE_MARGIN of the
    best score, after dropping samples far below the target chapter count (when at least one
    sample reaches it). Best-first; a single index means the evidence decided."""
    if not scores:
        return []
    floor = params.SCORE_MIN_COUNT_FRAC * target_chapters
    pool = [i for i, s in enumerate(scores) if s["n"] >= floor] or list(range(len(scores)))
    best = max(scores[i]["score"] for i in pool)
    near = [i for i in pool if best - scores[i]["score"] <= params.SCORE_TIE_MARGIN]
    return sorted(near, key = lambda i: -scores[i]["score"])




def fingerprint(obj) -> str:
    """16-hex content fingerprint of any JSON-able object (stable key order)."""
    return sha256(
        json.dumps(obj, sort_keys = True, ensure_ascii = False, default = str).encode("utf-8"),
    ).hexdigest()[:16]


def manifest_hash(
    *,
    slug: str,
    source_keys: list[str],
    distill_ref: Optional[str],
    distill_fp: str = "",
) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(source_keys):
        h.update(b"|")
        h.update(k.encode())
    h.update(b"|distill=")
    h.update((distill_ref or "").encode())
    if distill_fp:
        # `distill_ref` is the constant "latest" path: without the CONTENT of the
        # distillates, a re-distilled corpus kept hitting the old proposals.
        h.update(b"|distill_content=")
        h.update(distill_fp.encode())
    return h.hexdigest()[:16]


def is_length_error(exc: Exception) -> bool:
    """True when the model's output was cut at max_tokens."""
    return domains.settings.chat.domain.is_length_error(exc)
