"""Home page sections — hero, stats, feature triplet, how-it-works, footer.

2026-06-18 SOTA redesign (anchored to June 2026 patterns from Linear /
Anthropic / Vercel):
  - Hero: outcome-focused headline + kinetic rotating accent
    ("distill docs · search videos · track research") via pure-CSS
    keyframes — no JS. Mirrors Linear's hero verb rotation.
  - Stats: 4 tiles, one per product (DD/YCS/RR) + corpus size.
    Replaces the prior DD-only 4 (frameworks/pages/tiers/bytes).
  - Features: 3 LIVE cards (DD/YCS/RR) with sub-route chips so the
    operator can jump straight into Planner/Ask/Digest.
  - How it works: reframed product-agnostic (Source → Ingest → Reason
    → Study) — previously read like a DD-only wizard.
  - Foot: surfaces the tech-stack identity (DeepAgents · FastMCP ·
    LiteLLM · LangFuse · 100% free LLM tier) — Anthropic-style
    measured "what's actually in the box" claim."""
from fasthtml.common import A, Div, P, Span

from .format import _fmt_bytes, _fmt_int
from .widgets import _FeatureCard


def Hero(has_library: bool):
    primary_cta = A(
        "Open Docs Distiller →",
        href = "/docs-distiller",
        cls  = "btn-primary home-cta-primary",
    )
    # Kinetic rotating accent. Pure CSS — three <span>s cross-fade
    # under a single height-locked window. The cycle is 9s wall-clock
    # (3s per word). See `.home-kinetic-word:nth-child(N)` in home.css.
    rotor = Span(
        Span("distill docs",         cls = "home-kinetic-word"),
        Span("search videos",        cls = "home-kinetic-word"),
        Span("track research",       cls = "home-kinetic-word"),
        cls = "home-kinetic-rotor",
        **{"aria-label": "distill docs, search videos, track research"},
    )
    return Div(
        Div(
            Div("COELHO Nexus", cls = "home-hero-title"),
            Div(
                Span("Built for the operator who needs to ",
                     cls = "home-kinetic-lead"),
                rotor,
                cls = "home-hero-kinetic",
            ),
            P(
                "One control plane for three knowledge surfaces: framework "
                "documentation, YouTube transcripts, and academic research "
                "feeds. Every LLM call routed through an adaptive bandit, "
                "every pipeline checkpointed for surgical replay.",
                cls = "home-hero-tagline",
            ),
            Div(
                primary_cta,
                A(
                    "Browse all surfaces" if has_library else "Start your first study",
                    href = "#home-features",
                    cls = "home-cta-link",
                ),
                cls = "home-hero-cta",
            ),
            cls = "home-hero-text",
        ),
        cls = "home-hero",
    )


def Stats(library: list[dict], ycs_total: int | None, rr_total: int | None):
    n_fw    = len(library)
    n_bytes = sum(it.get("total_bytes") or 0 for it in library)

    def _stat(num: str, label: str, sub: str = ""):
        return Div(
            Div(num, cls = "home-stat-num"),
            Div(label, cls = "home-stat-label"),
            (Div(sub, cls = "home-stat-sub") if sub else ""),
            cls = "home-stat",
        )

    return Div(
        _stat(_fmt_int(n_fw) if n_fw else "—",
              "Frameworks ingested", "Docs Distiller"),
        _stat(_fmt_int(ycs_total) if ycs_total else "—",
              "Videos processed", "YouTube Content Search"),
        _stat(_fmt_int(rr_total) if rr_total else "—",
              "Radar scans launched", "Research Radar"),
        _stat(_fmt_bytes(n_bytes) if n_bytes else "—",
              "Corpus size", "across all surfaces"),
        cls = "home-stats",
    )


def Features():
    return Div(
        _FeatureCard(
            "Docs Distiller",
            "Pick from 115+ framework catalogs, ingest across five tiers "
            "(llms-full → llms-txt → sitemap → BFS → GitHub), run the "
            "eight-substep planner, study chapter-by-chapter.",
            "Live", "live", "/docs-distiller",
            chips = [
                ("Catalog",  "/docs-distiller"),
                ("Planner",  "/docs-distiller/planner"),
                ("Synth",    "/docs-distiller/synth"),
                ("Study",    "/docs-distiller/study"),
            ],
        ),
        _FeatureCard(
            "YouTube Content Search",
            "Index entire channels: Playwright-CDP transcripts (Whisper "
            "fallback) → Qdrant hybrid retrieval + Neo4j entity graph → "
            "Adaptive Graph-RAG ask with sub-second seek.",
            "Live", "live", "/youtube-content-search",
            chips = [
                ("Source",  "/youtube-content-search"),
                ("Ingest",  "/youtube-content-search/ingestion"),
                ("Ask",     "/youtube-content-search/ask"),
                ("Query",   "/youtube-content-search/query"),
            ],
        ),
        _FeatureCard(
            "Research Radar",
            "DeepAgents+FastMCP discovery across arXiv · Semantic Scholar · "
            "HF Daily · HN — ranked digest with money-angle, method, math, "
            "and a Build tab that synthesises runnable Python from the paper.",
            "Live", "live", "/research-radar",
            chips = [
                ("Pipeline",  "/research-radar"),
                ("Digest",    "/research-radar/digest"),
            ],
        ),
        id  = "home-features",
        cls = "home-features",
    )


def HowItWorks():
    steps = [
        ("01", "Source",
         "Pick from 115+ framework catalogs, curated YouTube channels, "
         "or live research arenas (arXiv · S2 · HF Daily · HN)."),
        ("02", "Ingest",
         "Per-domain MinIO + Postgres + Qdrant + Neo4j + Elasticsearch. "
         "Content-addressed caching means replays cost nothing."),
        ("03", "Reason",
         "Every LLM call routed through FGTS-VA (NeurIPS 2025) over a "
         "free-tier rotator. LangGraph checkpoints every super-step."),
        ("04", "Study",
         "Render the canonical output: structured chapters, ranked "
         "findings with executable code, or graph-RAG answers with cites."),
    ]
    return Div(
        Div("How it works", cls = "home-section-title"),
        Div(
            *[
                Div(
                    Div(num, cls = "home-step-num"),
                    Div(label, cls = "home-step-label"),
                    Div(desc, cls = "home-step-desc"),
                    cls = "home-step",
                )
                for num, label, desc in steps
            ],
            cls = "home-steps",
        ),
        cls = "home-section",
    )


def Foot():
    return Div(
        Span(
            "DeepAgents · FastMCP · LiteLLM Router · LangFuse · "
            "100% free-tier LLM rotator · single-node K8s",
            cls = "home-foot-meta",
        ),
        Span(
            A("API docs", href = "/api/v1/docs", cls = "home-foot-link"),
            " · ",
            A("Health", href = "/health", cls = "home-foot-link"),
            " · ",
            A("Settings", href = "/settings", cls = "home-foot-link"),
            cls = "home-foot-links",
        ),
        cls = "home-foot",
    )
