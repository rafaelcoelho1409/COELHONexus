"""arXiv subject taxonomy — single source of truth for `verticals` validation.

Snapshot of https://arxiv.org/category_taxonomy as of 2026-06-14 (155 codes,
verified by direct diff against the live page). The arXiv taxonomy changes
~once a year historically; regenerate with the snippet below when upstream
moves. Hardcoding (rather than importing the `arxiv` pypi package) keeps
the fastapi image lean — the pkg ships network/IO classes we don't need,
and v4.0.0 does NOT expose a `taxonomy.definitions.CATEGORIES` dict despite
some third-party docs claiming so.

Used at the HTTP boundary by `domains/rr/schemas.py`'s `ScanRequest`
validator. A verbatim copy lives in `apps/fasthtml/features/rr/taxonomy.py`
so the multi-select widget's custom-add field can validate client-side
without a round-trip. Keep them in sync; regenerate both together.

Regen recipe (zero deps, copy/paste into a REPL):

    import urllib.request, re
    html = urllib.request.urlopen(
        "https://arxiv.org/category_taxonomy", timeout=15
    ).read().decode()
    codes = sorted(set(
        re.findall(r'<h4>([a-z\\-]+(?:\\.[a-zA-Z\\-]+)?)\\s*<span', html)
    ))
    # → 155 codes today; then re-tabulate by archive prefix into _BY_ARCHIVE.
"""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# Archive → tuple of subcategory suffixes. The grouped shape keeps this file
# greppable + scannable; the public `ARXIV_CATEGORIES` frozenset below is
# what callers reach for. Standalone archives (gr-qc, hep-*, …) live in
# `_STANDALONE` because they have no subcategory dot-suffix.
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Code → full subject name. Surfaced as hover tooltips in the browse-all
# modal so the operator can disambiguate e.g. `math.AT` (Algebraic Topology)
# from `math.AG` (Algebraic Geometry) without leaving the form.
#
# Same regen recipe as ARXIV_CATEGORIES — scrape arxiv.org/category_taxonomy
# pairs and dump sorted; the `<h4>` regex above captures both code + name.
# --------------------------------------------------------------------------- #
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
