"""Home widgets — small reusable view primitives.

`_FeatureCard` is consumed by `Features()` to render each card in the
feature grid. `href = None` ⇒ no "Open →" link (used for roadmap items
that aren't yet routable)."""
from fasthtml.common import A, Div, P, Span


def _FeatureCard(title: str, desc: str, status: str, status_kind: str,
                 href: str | None):
    inner = [
        Div(
            Div(title, cls = "home-card-title"),
            Span(status, cls = f"home-card-status home-card-status-{status_kind}"),
            cls = "home-card-head",
        ),
        P(desc, cls = "home-card-desc"),
    ]
    if href:
        inner.append(A("Open →", href = href, cls = "home-card-link"))
    return Div(*inner, cls = "home-card")
