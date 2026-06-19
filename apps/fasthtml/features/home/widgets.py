"""Home widgets — small reusable view primitives.

`_FeatureCard` is consumed by `Features()` to render each card in the
feature grid. 2026-06-18 SOTA refactor:
  - Adds `chips` (list[(label, href)]) so each LIVE card surfaces its
    sub-routes inline — operator can jump to Synth / Ask / Digest
    directly from home, the way Linear's product cards expose
    sub-surfaces (Projects/Cycles/Inbox) on hover.
  - `href=None` still suppresses the primary "Open →" link.
  - Status pill kept; status_kind in {live, coming, soon}."""
from fasthtml.common import A, Div, P, Span


def _FeatureCard(
    title:       str,
    desc:        str,
    status:      str,
    status_kind: str,
    href:        str | None,
    chips:       list[tuple[str, str]] | None = None,
):
    inner = [
        Div(
            Div(title, cls = "home-card-title"),
            Span(status, cls = f"home-card-status home-card-status-{status_kind}"),
            cls = "home-card-head",
        ),
        P(desc, cls = "home-card-desc"),
    ]
    if chips:
        inner.append(
            Div(
                *[A(label, href = url, cls = "home-card-chip") for label, url in chips],
                cls = "home-card-chips",
            ),
        )
    if href:
        inner.append(A("Open →", href = href, cls = "home-card-link"))
    return Div(*inner, cls = "home-card")
