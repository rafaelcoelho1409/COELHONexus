"""Home page sections — hero, stats, feature cards, how-it-works, footer."""
from fasthtml.common import A, Div, P, Span

from .format import _fmt_bytes, _fmt_int
from .widgets import _FeatureCard


def Hero(has_library: bool):
    primary_cta = A(
        "Open Docs Distiller →",
        href = "/docs-distiller",
        cls = "btn-primary home-cta-primary",
    )
    return Div(
        Div(
            Div("COELHO Nexus", cls = "home-hero-title"),
            P(
                "Turn framework docs into structured studies you can absorb "
                "in an evening. Five-tier ingestion (llms-full → sitemap → "
                "BFS), eight-substep planner, every LLM call routed through "
                "an adaptive bandit, every super-step checkpointed for "
                "surgical replay.",
                cls = "home-hero-tagline",
            ),
            Div(
                primary_cta,
                A(
                    "View library" if has_library else "Build your first study",
                    href = "/docs-distiller",
                    cls = "home-cta-link",
                ),
                cls = "home-hero-cta",
            ),
            cls = "home-hero-text",
        ),
        cls = "home-hero",
    )


def Stats(library: list[dict]):
    n_fw = len(library)
    n_pages = sum(it.get("page_count") or 0 for it in library)
    n_bytes = sum(it.get("total_bytes") or 0 for it in library)
    tiers = {it.get("tier_kind") for it in library if it.get("tier_kind")}

    def _stat(num: str, label: str):
        return Div(
            Div(num, cls = "home-stat-num"),
            Div(label, cls = "home-stat-label"),
            cls = "home-stat",
        )

    return Div(
        _stat(_fmt_int(n_fw), "Frameworks ingested"),
        _stat(_fmt_int(n_pages) if n_pages else "—", "Pages stored"),
        _stat(_fmt_bytes(n_bytes) if n_bytes else "—", "Corpus size"),
        _stat(str(len(tiers)) if tiers else "—", "Ingestion tiers used"),
        cls = "home-stats",
    )


def Features():
    return Div(
        _FeatureCard(
            "Docs Distiller",
            "Pick a framework, ingest its docs across 5 tiers "
            "(llms-full → llms-txt → sitemap → BFS → GitHub), run the "
            "8-substep Planner, study chapter-by-chapter.",
            "Live", "live", "/docs-distiller",
        ),
        _FeatureCard(
            "YouTube Content Search",
            "Search transcripts across curated channels with sub-second "
            "seek. Playwright-CDP transcript extraction (no public API "
            "works reliably), Whisper fallback for missing captions.",
            "Coming", "coming", None,
        ),
        _FeatureCard(
            "Roadmap",
            "Audio Distiller (podcasts → structured notes), Paper "
            "Distiller (arXiv PDFs → citation-graph studies), and "
            "user-imported corpus support.",
            "Soon", "soon", None,
        ),
        cls = "home-features",
    )


def HowItWorks():
    steps = [
        ("01", "Catalog",  "Pick from 115+ frameworks across 5 tiers."),
        ("02", "Ingest",   "Per-framework MinIO storage; cache reused across replans."),
        ("03", "Plan",     "8 substep nodes — each Postgres-checkpointed + LangFuse-traced."),
        ("04", "Study",    "Render the canonical manifest; drill into individual files."),
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
        Span("v1.0 · single-node K8s · all LLM calls via rotator",
             cls = "home-foot-meta"),
        Span(
            A("API docs", href = "/api/v1/docs", cls = "home-foot-link"),
            " · ",
            A("Health", href = "/health", cls = "home-foot-link"),
            cls = "home-foot-links",
        ),
        cls = "home-foot",
    )
