"""Pure functions for the RR domain — no I/O, no event loop, no mocks."""
from __future__ import annotations
from . import entities, keys, params, patterns

import math
from datetime import date, datetime
from typing import Any


def _canonical_arxiv_id(raw: str | None) -> str | None:
    """Extract 'YYMM.NNNNN' from any form: bare id, versioned, 'arXiv:' prefix,
    or full URL. Validates the YYMM prefix's month is 01-12 and tries every
    match in `raw`, not just the first — rejects coincidental digit-dot-
    digit substrings from an unrelated ID scheme that would otherwise look
    plausible. Confirmed live (2026-09-20): an OpenAlex work whose DOI
    contained '2026.20183' (month '26' — invalid) was misattributed as an
    arXiv id, pulling an unrelated gene-therapy paper into a 'context
    engineering' digest under a fabricated arxiv_id."""
    if not raw:
        return None
    for m in patterns.ARXIV_ID_RE.finditer(raw):
        candidate = m.group(1)
        month = int(candidate[2:4])
        if 1 <= month <= 12:
            return candidate
    return None


def _parse_date(value: Any) -> date | None:
    """Parse heterogeneous date shapes the 5 source tools return; bad input → None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # Year-only fallback (S2 sometimes only has the year)
    try:
        return date(int(s), 1, 1)
    except (ValueError, TypeError):
        return None


def normalize_arxiv(d: dict[str, Any]) -> entities.NormalizedPaper:
    return entities.NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = _parse_date(d.get("published")),
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("categories", []) or []),
        citations             = 0,
        influential_citations = 0,
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = 0,
        sources               = frozenset({keys.SOURCE_ARXIV}),
    )


def normalize_s2(d: dict[str, Any]) -> entities.NormalizedPaper:
    external_ids = d.get("external_ids") or {}
    arxiv_raw = external_ids.get(keys.S2_EXTERNAL_ID_ARXIV)
    published = _parse_date(d.get("publication_date")) or _parse_date(d.get("year"))
    return entities.NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(arxiv_raw),
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = published,
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("fields_of_study", []) or []),
        citations             = int(d.get("citation_count")             or 0),
        influential_citations = int(d.get("influential_citation_count") or 0),
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = 0,
        sources               = frozenset({keys.SOURCE_S2}),
    )


def normalize_hf(d: dict[str, Any]) -> entities.NormalizedPaper:
    return entities.NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = _parse_date(d.get("published")),
        authors               = tuple(d.get("authors", []) or []),
        categories            = (),
        citations             = 0,
        influential_citations = 0,
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = int(d.get("upvotes") or 0),
        sources               = frozenset({keys.SOURCE_HF}),
    )


def normalize_hn(d: dict[str, Any]) -> entities.NormalizedPaper:
    author = (d.get("author") or "").strip()
    return entities.NormalizedPaper(
        arxiv_id              = _canonical_arxiv_id(d.get("arxiv_id")),
        title                 = (d.get("title") or "").strip(),
        # HN self-post body is the closest analogue to an "abstract"; most HN hits are link posts and this stays empty.
        abstract              = (d.get("story_text") or "").strip(),
        published             = _parse_date(d.get("created_at")),
        authors               = (author,) if author else (),
        categories            = tuple(d.get("tags", []) or []),
        citations             = 0,
        influential_citations = 0,
        hn_points             = int(d.get("points")       or 0),
        hn_num_comments       = int(d.get("num_comments") or 0),
        hf_upvotes            = 0,
        sources               = frozenset({keys.SOURCE_HN}),
    )


def normalize_openalex(d: dict[str, Any]) -> entities.NormalizedPaper:
    """OpenAlex has no native arxiv_id field; recovered opportunistically
    from the DOI when the work IS an arXiv preprint (DOI shape
    '10.48550/arXiv.2406.12345'). Requires the literal substring 'arxiv'
    in the DOI before even attempting the numeric-pattern match — a
    cheap, strong pre-filter against a coincidental digit-dot-digit
    match inside some OTHER DOI scheme's suffix (see `_canonical_arxiv_id`
    docstring for the live incident this guards against). `categories`
    carries OpenAlex's free-text topic names, not arXiv-style codes, so
    `_vertical_fit` scoring won't match against `profile_verticals` for
    OpenAlex-sourced papers — same honest degradation HN's free-text
    `tags` already has."""
    external_ids = d.get("external_ids") or {}
    doi = external_ids.get("DOI") or ""
    arxiv_id = _canonical_arxiv_id(doi) if "arxiv" in doi.lower() else None
    published = _parse_date(d.get("publication_date"))
    if published is None and d.get("publication_year"):
        published = _parse_date(str(d["publication_year"]))
    return entities.NormalizedPaper(
        arxiv_id              = arxiv_id,
        title                 = (d.get("title")    or "").strip(),
        abstract              = (d.get("abstract") or "").strip(),
        published             = published,
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("topics", []) or []),
        citations             = int(d.get("cited_by_count") or 0),
        influential_citations = 0,
        hn_points             = 0,
        hn_num_comments       = 0,
        hf_upvotes            = 0,
        sources               = frozenset({keys.SOURCE_OPENALEX}),
    )


def _normalized_title(title: str) -> str:
    """Secondary dedup key for items without arxiv_id — catches HN crossposts with identical titles."""
    if not title:
        return ""
    return patterns.TITLE_NORM_RE.sub(" ", title.lower()).strip()


def dedup_by_arxiv_id(items: list[entities.NormalizedPaper]) -> list[entities.NormalizedPaper]:
    """Primary dedup on arxiv_id (max-merge signals); secondary dedup by normalized title for no-id items."""
    by_id: dict[str, entities.NormalizedPaper] = {}
    by_title: dict[str, entities.NormalizedPaper] = {}
    no_key: list[entities.NormalizedPaper] = []
    for it in items:
        if it.arxiv_id:
            existing = by_id.get(it.arxiv_id)
            by_id[it.arxiv_id] = _merge(existing, it) if existing else it
            continue
        nt = _normalized_title(it.title)
        if not nt:
            no_key.append(it)
            continue
        existing_t = by_title.get(nt)
        by_title[nt] = _merge(existing_t, it) if existing_t else it
    return list(by_id.values()) + list(by_title.values()) + no_key


def _merge(a: entities.NormalizedPaper, b: entities.NormalizedPaper) -> entities.NormalizedPaper:
    """Max-merge per-source signals; strings/dates prefer first non-empty; sets union."""
    return entities.NormalizedPaper(
        arxiv_id              = a.arxiv_id,
        title                 = a.title    or b.title,
        abstract              = a.abstract or b.abstract,
        published             = a.published or b.published,
        authors               = a.authors  or b.authors,
        categories            = tuple(sorted(set(a.categories) | set(b.categories))),
        citations             = max(a.citations,             b.citations),
        influential_citations = max(a.influential_citations, b.influential_citations),
        hn_points             = max(a.hn_points,             b.hn_points),
        hn_num_comments       = max(a.hn_num_comments,       b.hn_num_comments),
        hf_upvotes            = max(a.hf_upvotes,            b.hf_upvotes),
        sources               = a.sources | b.sources,
        embedding             = a.embedding or b.embedding,
        has_code              = a.has_code or b.has_code,
    )


def signal_score(
    p: entities.NormalizedPaper,
    *,
    now: date,
    profile_embedding: tuple[float, ...] | None = None,
    profile_verticals: tuple[str, ...] = (),
    weights: params.SignalWeights = params.WEIGHTS,
    domain_params: params.DomainParams = params.DOMAIN_PARAMS,
) -> float:
    """Sortable composite scalar — weights need not sum to 1; each component is roughly [0, 1]."""
    rel = _cosine(profile_embedding, p.embedding) \
        if (profile_embedding and p.embedding) else 0.0
    rec = _recency_decay(p.published, now, domain_params.recency_half_life_days)
    vel = _velocity(p.citations, p.published, now, domain_params.velocity_min_age_days)
    infl = (p.influential_citations / p.citations) if p.citations > 0 else 0.0
    infl = max(0.0, min(infl, 1.0))
    fit = _vertical_fit(p.categories, profile_verticals)
    # log1p caps buzz dominance from one viral HN post; /14 normalizes since log1p(1_000_000) ≈ 13.8.
    buzz_raw = _log1p(p.hn_points) + _log1p(p.hf_upvotes)
    buzz = min(buzz_raw / 14.0, 1.0)
    code = 1.0 if p.has_code else 0.0
    # arxiv_id absence = product announcement; lift prevents displacing real papers on thin result sets.
    has_aid = 1.0 if p.arxiv_id else 0.0
    return (
        weights.relevance         * rel
        + weights.recency         * rec
        + weights.citation_velocity * vel
        + weights.influential_ratio * infl
        + weights.vertical_fit    * fit
        + weights.cross_tier_buzz * buzz
        + weights.has_code        * code
        + weights.has_arxiv_id    * has_aid
    )


def diff_vs_seen(
    candidates: list[entities.NormalizedPaper],
    seen_arxiv_ids: frozenset[str],
) -> tuple[list[entities.NormalizedPaper], list[entities.NormalizedPaper]]:
    """Returns (new, returning). Papers without arxiv_id are always new — no stable identity in radar_seen."""
    new: list[entities.NormalizedPaper] = []
    returning: list[entities.NormalizedPaper] = []
    for c in candidates:
        if c.arxiv_id and c.arxiv_id in seen_arxiv_ids:
            returning.append(c)
        else:
            new.append(c)
    return new, returning


def _cosine(a: tuple[float, ...] | None, b: tuple[float, ...] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na  += x * x
        nb  += y * y
    denom = (na ** 0.5) * (nb ** 0.5)
    return (dot / denom) if denom > 0.0 else 0.0


def _recency_decay(published: date | None, now: date, half_life_days: int) -> float:
    """Exponential decay: 2^(-age/half_life). Same-day or future → 1.0."""
    if published is None:
        return 0.0
    age_days = (now - published).days
    if age_days <= 0:
        return 1.0
    return 2.0 ** (-age_days / half_life_days)


def _velocity(citations: int, published: date | None, now: date, min_age_days: int) -> float:
    """log1p(citations) / log1p(age_days) — log-saturated citations-per-day proxy, clamped to [0, 1]."""
    if citations <= 0 or published is None:
        return 0.0
    age_days = max((now - published).days, min_age_days)
    denom = math.log1p(age_days)
    if denom <= 0.0:
        return 0.0
    return min(math.log1p(citations) / denom, 1.0)


def _vertical_fit(categories: tuple[str, ...], verticals: tuple[str, ...]) -> float:
    """Fraction of the paper's categories that match profile verticals. Both sides use the same controlled vocabulary."""
    if not categories or not verticals:
        return 0.0
    cats  = {c.lower() for c in categories}
    verts = {v.lower() for v in verticals}
    overlap = cats & verts
    return (len(overlap) / len(cats)) if overlap else 0.0


def _log1p(x: int | float) -> float:
    return math.log1p(x) if x > 0 else 0.0


# --- arXiv taxonomy (verticals validation) ---
#
# Snapshot of https://arxiv.org/category_taxonomy as of 2026-06-14 (155 codes,
# verified by direct diff against the live page). The arXiv taxonomy changes
# ~once a year historically; regenerate with the snippet below when upstream
# moves. Hardcoding (rather than importing the `arxiv` pypi package) keeps
# the fastapi image lean — the pkg ships network/IO classes we don't need,
# and v4.0.0 does NOT expose a `taxonomy.definitions.CATEGORIES` dict despite
# some third-party docs claiming so.
#
# Used at the HTTP boundary by `domains/rr/schemas.py`'s `ScanRequest`
# validator. A verbatim copy lives in `apps/fasthtml/features/rr/taxonomy.py`
# so the multi-select widget's custom-add field can validate client-side
# without a round-trip. Keep them in sync; regenerate both together.
#
# Regen recipe (zero deps, copy/paste into a REPL):
#
#     import urllib.request, re
#     html = urllib.request.urlopen(
#         "https://arxiv.org/category_taxonomy", timeout=15
#     ).read().decode()
#     codes = sorted(set(
#         re.findall(r'<h4>([a-z\-]+(?:\.[a-zA-Z\-]+)?)\s*<span', html)
#     ))
#     # → 155 codes today; then re-tabulate by archive prefix into _BY_ARCHIVE.

# Archive → tuple of subcategory suffixes. The grouped shape keeps this
# section greppable + scannable; the public `ARXIV_CATEGORIES` frozenset
# below is what callers reach for. Standalone archives (gr-qc, hep-*, …)
# live in `_STANDALONE` because they have no subcategory dot-suffix.
_BY_ARCHIVE: dict[str, tuple[str, ...]] = {
    "cs": (
        "AI", "AR", "CC", "CE", "CG", "CL", "CR", "CV", "CY", "DB",
        "DC", "DL", "DM", "DS", "ET", "FL", "GL", "GR", "GT", "HC",
        "IR", "IT", "LG", "LO", "MA", "MM", "MS", "NA", "NE", "NI",
        "OH", "OS", "PF", "PL", "RO", "SC", "SD", "SE", "SI", "SY",
    ),
    "math": (
        "AC", "AG", "AP", "AT", "CA", "CO", "CT", "CV", "DG", "DS",
        "FA", "GM", "GN", "GR", "GT", "HO", "IT", "KT", "LO", "MG",
        "MP", "NA", "NT", "OA", "OC", "PR", "QA", "RA", "RT", "SG",
        "SP", "ST",
    ),
    "stat":    ("AP", "CO", "ME", "ML", "OT", "TH"),
    "q-bio":   ("BM", "CB", "GN", "MN", "NC", "OT", "PE", "QM", "SC", "TO"),
    "q-fin":   ("CP", "EC", "GN", "MF", "PM", "PR", "RM", "ST", "TR"),
    "econ":    ("EM", "GN", "TH"),
    "eess":    ("AS", "IV", "SP", "SY"),
    "astro-ph":("CO", "EP", "GA", "HE", "IM", "SR"),
    "cond-mat":(
        "dis-nn", "mes-hall", "mtrl-sci", "other",
        "quant-gas", "soft", "stat-mech", "str-el", "supr-con",
    ),
    "physics": (
        "acc-ph", "ao-ph", "app-ph", "atm-clus", "atom-ph", "bio-ph",
        "chem-ph", "class-ph", "comp-ph", "data-an", "ed-ph", "flu-dyn",
        "gen-ph", "geo-ph", "hist-ph", "ins-det", "med-ph", "optics",
        "plasm-ph", "pop-ph", "soc-ph", "space-ph",
    ),
    "nlin":    ("AO", "CD", "CG", "PS", "SI"),
}

# Archives that have no subcategories — the archive code IS the leaf code.
_STANDALONE: tuple[str, ...] = (
    "gr-qc", "math-ph",
    "hep-ex", "hep-lat", "hep-ph", "hep-th",
    "nucl-ex", "nucl-th",
    "quant-ph",
)


ARXIV_CATEGORIES: frozenset[str] = frozenset(
    {f"{archive}.{sub}" for archive, subs in _BY_ARCHIVE.items() for sub in subs}
    | set(_STANDALONE)
)


def is_valid_vertical(code: str) -> bool:
    """O(1) membership check against the arXiv taxonomy.

    Case-sensitive (arxiv codes use exact case: `cs.LG`, not `CS.lg`). Empty
    strings and whitespace return False; callers should strip before calling.
    """
    return bool(code) and code in ARXIV_CATEGORIES


# Code → full subject name. Surfaced as hover tooltips in the browse-all
# modal so the operator can disambiguate e.g. `math.AT` (Algebraic Topology)
# from `math.AG` (Algebraic Geometry) without leaving the form.
# Same regen recipe as ARXIV_CATEGORIES — scrape arxiv.org/category_taxonomy
# pairs and dump sorted; the `<h4>` regex above captures both code + name.
ARXIV_DESCRIPTIONS: dict[str, str] = {
    'astro-ph.CO'           : 'Cosmology and Nongalactic Astrophysics',
    'astro-ph.EP'           : 'Earth and Planetary Astrophysics',
    'astro-ph.GA'           : 'Astrophysics of Galaxies',
    'astro-ph.HE'           : 'High Energy Astrophysical Phenomena',
    'astro-ph.IM'           : 'Instrumentation and Methods for Astrophysics',
    'astro-ph.SR'           : 'Solar and Stellar Astrophysics',
    'cond-mat.dis-nn'       : 'Disordered Systems and Neural Networks',
    'cond-mat.mes-hall'     : 'Mesoscale and Nanoscale Physics',
    'cond-mat.mtrl-sci'     : 'Materials Science',
    'cond-mat.other'        : 'Other Condensed Matter',
    'cond-mat.quant-gas'    : 'Quantum Gases',
    'cond-mat.soft'         : 'Soft Condensed Matter',
    'cond-mat.stat-mech'    : 'Statistical Mechanics',
    'cond-mat.str-el'       : 'Strongly Correlated Electrons',
    'cond-mat.supr-con'     : 'Superconductivity',
    'cs.AI'                 : 'Artificial Intelligence',
    'cs.AR'                 : 'Hardware Architecture',
    'cs.CC'                 : 'Computational Complexity',
    'cs.CE'                 : 'Computational Engineering, Finance, and Science',
    'cs.CG'                 : 'Computational Geometry',
    'cs.CL'                 : 'Computation and Language',
    'cs.CR'                 : 'Cryptography and Security',
    'cs.CV'                 : 'Computer Vision and Pattern Recognition',
    'cs.CY'                 : 'Computers and Society',
    'cs.DB'                 : 'Databases',
    'cs.DC'                 : 'Distributed, Parallel, and Cluster Computing',
    'cs.DL'                 : 'Digital Libraries',
    'cs.DM'                 : 'Discrete Mathematics',
    'cs.DS'                 : 'Data Structures and Algorithms',
    'cs.ET'                 : 'Emerging Technologies',
    'cs.FL'                 : 'Formal Languages and Automata Theory',
    'cs.GL'                 : 'General Literature',
    'cs.GR'                 : 'Graphics',
    'cs.GT'                 : 'Computer Science and Game Theory',
    'cs.HC'                 : 'Human-Computer Interaction',
    'cs.IR'                 : 'Information Retrieval',
    'cs.IT'                 : 'Information Theory',
    'cs.LG'                 : 'Machine Learning',
    'cs.LO'                 : 'Logic in Computer Science',
    'cs.MA'                 : 'Multiagent Systems',
    'cs.MM'                 : 'Multimedia',
    'cs.MS'                 : 'Mathematical Software',
    'cs.NA'                 : 'Numerical Analysis',
    'cs.NE'                 : 'Neural and Evolutionary Computing',
    'cs.NI'                 : 'Networking and Internet Architecture',
    'cs.OH'                 : 'Other Computer Science',
    'cs.OS'                 : 'Operating Systems',
    'cs.PF'                 : 'Performance',
    'cs.PL'                 : 'Programming Languages',
    'cs.RO'                 : 'Robotics',
    'cs.SC'                 : 'Symbolic Computation',
    'cs.SD'                 : 'Sound',
    'cs.SE'                 : 'Software Engineering',
    'cs.SI'                 : 'Social and Information Networks',
    'cs.SY'                 : 'Systems and Control',
    'econ.EM'               : 'Econometrics',
    'econ.GN'               : 'General Economics',
    'econ.TH'               : 'Theoretical Economics',
    'eess.AS'               : 'Audio and Speech Processing',
    'eess.IV'               : 'Image and Video Processing',
    'eess.SP'               : 'Signal Processing',
    'eess.SY'               : 'Systems and Control',
    'gr-qc'                 : 'General Relativity and Quantum Cosmology',
    'hep-ex'                : 'High Energy Physics - Experiment',
    'hep-lat'               : 'High Energy Physics - Lattice',
    'hep-ph'                : 'High Energy Physics - Phenomenology',
    'hep-th'                : 'High Energy Physics - Theory',
    'math-ph'               : 'Mathematical Physics',
    'math.AC'               : 'Commutative Algebra',
    'math.AG'               : 'Algebraic Geometry',
    'math.AP'               : 'Analysis of PDEs',
    'math.AT'               : 'Algebraic Topology',
    'math.CA'               : 'Classical Analysis and ODEs',
    'math.CO'               : 'Combinatorics',
    'math.CT'               : 'Category Theory',
    'math.CV'               : 'Complex Variables',
    'math.DG'               : 'Differential Geometry',
    'math.DS'               : 'Dynamical Systems',
    'math.FA'               : 'Functional Analysis',
    'math.GM'               : 'General Mathematics',
    'math.GN'               : 'General Topology',
    'math.GR'               : 'Group Theory',
    'math.GT'               : 'Geometric Topology',
    'math.HO'               : 'History and Overview',
    'math.IT'               : 'Information Theory',
    'math.KT'               : 'K-Theory and Homology',
    'math.LO'               : 'Logic',
    'math.MG'               : 'Metric Geometry',
    'math.MP'               : 'Mathematical Physics',
    'math.NA'               : 'Numerical Analysis',
    'math.NT'               : 'Number Theory',
    'math.OA'               : 'Operator Algebras',
    'math.OC'               : 'Optimization and Control',
    'math.PR'               : 'Probability',
    'math.QA'               : 'Quantum Algebra',
    'math.RA'               : 'Rings and Algebras',
    'math.RT'               : 'Representation Theory',
    'math.SG'               : 'Symplectic Geometry',
    'math.SP'               : 'Spectral Theory',
    'math.ST'               : 'Statistics Theory',
    'nlin.AO'               : 'Adaptation and Self-Organizing Systems',
    'nlin.CD'               : 'Chaotic Dynamics',
    'nlin.CG'               : 'Cellular Automata and Lattice Gases',
    'nlin.PS'               : 'Pattern Formation and Solitons',
    'nlin.SI'               : 'Exactly Solvable and Integrable Systems',
    'nucl-ex'               : 'Nuclear Experiment',
    'nucl-th'               : 'Nuclear Theory',
    'physics.acc-ph'        : 'Accelerator Physics',
    'physics.ao-ph'         : 'Atmospheric and Oceanic Physics',
    'physics.app-ph'        : 'Applied Physics',
    'physics.atm-clus'      : 'Atomic and Molecular Clusters',
    'physics.atom-ph'       : 'Atomic Physics',
    'physics.bio-ph'        : 'Biological Physics',
    'physics.chem-ph'       : 'Chemical Physics',
    'physics.class-ph'      : 'Classical Physics',
    'physics.comp-ph'       : 'Computational Physics',
    'physics.data-an'       : 'Data Analysis, Statistics and Probability',
    'physics.ed-ph'         : 'Physics Education',
    'physics.flu-dyn'       : 'Fluid Dynamics',
    'physics.gen-ph'        : 'General Physics',
    'physics.geo-ph'        : 'Geophysics',
    'physics.hist-ph'       : 'History and Philosophy of Physics',
    'physics.ins-det'       : 'Instrumentation and Detectors',
    'physics.med-ph'        : 'Medical Physics',
    'physics.optics'        : 'Optics',
    'physics.plasm-ph'      : 'Plasma Physics',
    'physics.pop-ph'        : 'Popular Physics',
    'physics.soc-ph'        : 'Physics and Society',
    'physics.space-ph'      : 'Space Physics',
    'q-bio.BM'              : 'Biomolecules',
    'q-bio.CB'              : 'Cell Behavior',
    'q-bio.GN'              : 'Genomics',
    'q-bio.MN'              : 'Molecular Networks',
    'q-bio.NC'              : 'Neurons and Cognition',
    'q-bio.OT'              : 'Other Quantitative Biology',
    'q-bio.PE'              : 'Populations and Evolution',
    'q-bio.QM'              : 'Quantitative Methods',
    'q-bio.SC'              : 'Subcellular Processes',
    'q-bio.TO'              : 'Tissues and Organs',
    'q-fin.CP'              : 'Computational Finance',
    'q-fin.EC'              : 'Economics',
    'q-fin.GN'              : 'General Finance',
    'q-fin.MF'              : 'Mathematical Finance',
    'q-fin.PM'              : 'Portfolio Management',
    'q-fin.PR'              : 'Pricing of Securities',
    'q-fin.RM'              : 'Risk Management',
    'q-fin.ST'              : 'Statistical Finance',
    'q-fin.TR'              : 'Trading and Market Microstructure',
    'quant-ph'              : 'Quantum Physics',
    'stat.AP'               : 'Applications',
    'stat.CO'               : 'Computation',
    'stat.ME'               : 'Methodology',
    'stat.ML'               : 'Machine Learning',
    'stat.OT'               : 'Other Statistics',
    'stat.TH'               : 'Statistics Theory',
}


def describe_vertical(code: str) -> str:
    """Return the human subject name for a code (e.g. `'cs.LG' → 'Machine
    Learning'`). Returns an empty string for unknown codes; callers should
    branch on truthiness."""
    return ARXIV_DESCRIPTIONS.get(code, "")
