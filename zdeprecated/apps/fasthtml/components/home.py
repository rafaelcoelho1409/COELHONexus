"""
Home — landing page.

Direct port of apps/web/main.go's inline `homePage`: greeting header, two
feature cards (KD + YouTube Ask), and a "test FastAPI /health" strip
backed by HTMX. No future-feature placeholders (those live in the Sidebar
nav until each one ships).
"""
from fasthtml.common import (
    A, Button, Div, H1, Header, I, P, Section, Span,
)

from components.base import Page


def _FeatureCard(icon: str, title: str, desc: str, href: str):
    """One of the two large feature tiles on the landing page."""
    return A(
        Span(
            I(data_lucide=icon, cls="w-6 h-6"),
            cls=(
                "w-12 h-12 rounded-lg bg-primary/10 text-primary flex items-center "
                "justify-center shrink-0 group-hover:bg-primary "
                "group-hover:text-primary-content transition-colors"
            ),
        ),
        Div(
            Div(title, cls="text-base font-semibold"),
            Div(desc, cls="text-xs text-base-content/60 mt-1"),
            cls="min-w-0",
        ),
        href=href,
        cls="memo-card flex items-start gap-4 group no-underline",
    )


def _BackendStatusStrip():
    """The 'Test FastAPI /health' strip — htmx swaps result into #backend-result."""
    return Div(
        Div(
            Div(
                Div("FastAPI backend", cls="text-[0.7rem] uppercase tracking-wider text-base-content/60"),
                Div("Not yet checked.", id="backend-result",
                    cls="text-xs mt-1 text-base-content/60"),
            ),
            Button(
                I(data_lucide="activity", cls="w-4 h-4"),
                "Test /health",
                hx_get="/api/test",
                hx_target="#backend-result",
                hx_swap="innerHTML",
                cls="btn btn-sm btn-primary gap-2",
            ),
            cls="flex items-center justify-between gap-4",
        ),
        cls="memo-card",
    )


def HomePage():
    """Top-level landing page (matches apps/web/main.go::homeHandler output)."""
    return Page(
        "Home",
        Div(
            # Header
            Header(
                H1("Welcome back", cls="text-2xl font-bold tracking-tight"),
                P(
                    "Knowledge Distiller, YouTube RAG, and catalog health — "
                    "all in one place.",
                    cls="text-sm text-base-content/60 mt-1",
                ),
                cls="mb-8",
            ),
            # Two primary features
            Div(
                _FeatureCard(
                    "book-open-text",
                    "Knowledge Distiller",
                    "Turn framework docs into chapter-structured study guides "
                    "with synthesized code, flashcards, and challenges.",
                    "/kd/inspect",
                ),
                _FeatureCard(
                    "message-square-more",
                    "YouTube Ask",
                    "Agentic RAG over ingested YouTube content — ask in plain "
                    "language, get grounded answers with citations.",
                    "/youtube/ask",
                ),
                cls="grid grid-cols-1 md:grid-cols-2 gap-4 mb-10",
            ),
            # Backend status strip
            _BackendStatusStrip(),
            cls="max-w-5xl mx-auto px-8 py-10",
        ),
        active_nav="home",
    )
