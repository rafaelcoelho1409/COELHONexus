"""RRBody — Research Radar scan page.

Layout:
  ┌──────────────────────────────────────────────┐
  │  Scan form: topic · verticals · top_n        │
  ├──────────────────────────────────────────────┤
  │  Live status strip (phase + last message)    │
  ├──────────────────────────────────────────────┤
  │  Digest cards (rendered after status=done)   │
  └──────────────────────────────────────────────┘
"""
import json

from fasthtml.common import (
    H3, H4, Button, Details, Dialog, Div, Form, Input, Label, NotStr, P,
    Script, Span, Summary,
)

# `Script` here is for the inlined taxonomy JSON `<script type="application/json">`
# inside _VerticalMultiSelect — the page's main.js bundle is loaded by the
# pipeline/digest body modules, not here.

from .taxonomy import ARXIV_CATEGORIES, ARXIV_DESCRIPTIONS, _BY_ARCHIVE, _STANDALONE


# Display labels for the archive headings in the browse-all modal. Mirrors
# arxiv.org/category_taxonomy's groupings.
_ARCHIVE_LABELS: dict[str, str] = {
    "cs":       "Computer Science",
    "math":     "Mathematics",
    "stat":     "Statistics",
    "q-bio":    "Quantitative Biology",
    "q-fin":    "Quantitative Finance",
    "econ":     "Economics",
    "eess":     "Electrical Engineering & Systems Science",
    "astro-ph": "Astrophysics",
    "cond-mat": "Condensed Matter",
    "physics":  "Physics (other)",
    "nlin":     "Nonlinear Sciences",
}


# Curated arxiv categories that ride at the top of the multi-select panel —
# matched to `operator_profile.md`'s active verticals + the
# `arxiv_query_shaping` skill table. Order = priority (most relevant first).
# Anything outside this list is still selectable via the panel's custom-add
# field; the full taxonomy in `taxonomy.py` is the validator set.
_VERTICAL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("cs.LG",    "ML"),
    ("cs.AI",    "AI"),
    ("cs.CL",    "NLP"),
    ("cs.CV",    "Vision"),
    ("stat.ML",  "ML theory"),
    ("math.OC",  "Optimization"),
    ("math.PR",  "Probability"),
    ("q-fin.PR", "Quant pricing"),
    ("q-fin.ST", "Quant stats"),
    ("cs.CR",    "Security"),
)
_VERTICAL_DEFAULT: tuple[str, ...] = ("cs.LG", "cs.AI")


def _VerticalMultiSelect():
    """Native `<details>`-driven multi-select panel with checkbox rows +
    a custom-add input at the bottom.

    Single source of truth = the hidden `#verticals` input (rebuilt by JS
    from checkbox state on every change so the existing submit payload
    stays unchanged). The full arXiv taxonomy ships as inline JSON so the
    custom-add field can validate client-side without a round-trip; the
    same taxonomy file is also used server-side in
    `domains/rr/schemas.py`'s Pydantic validator (defense-in-depth)."""
    summary_initial = ", ".join(_VERTICAL_DEFAULT) or "Pick verticals…"
    return Details(
        Summary(
            Span(
                summary_initial,
                id  = "rr-vertical-summary",
                cls = "rr-multiselect-summary-text",
            ),
            Span("▾", cls = "rr-multiselect-caret", **{"aria-hidden": "true"}),
            cls = "rr-multiselect-summary",
        ),
        Div(
            # Add-custom moved to the TOP of the panel — it's the action the
            # operator most often does (curated set rarely needs reordering);
            # putting it above the options keeps the input next to the open
            # trigger so a fast type→Enter flow doesn't require scrolling.
            Div(
                Div(
                    Label("Add custom code", For="rr-vertical-custom",
                          cls = "rr-multiselect-add-label"),
                    Button(
                        f"Browse all ({len(ARXIV_CATEGORIES)})",
                        type = "button",
                        id   = "rr-vertical-browse-btn",
                        cls  = "rr-multiselect-browse-btn",
                        **{"aria-haspopup": "dialog", "aria-controls": "rr-vertical-browse-dialog"},
                    ),
                    cls = "rr-multiselect-add-label-row",
                ),
                Div(
                    Input(
                        id          = "rr-vertical-custom",
                        type        = "text",
                        placeholder = "e.g. eess.SP",
                        autocomplete = "off",
                        spellcheck  = "false",
                        cls         = "rr-multiselect-input",
                    ),
                    Button(
                        "+ Add",
                        type = "button",
                        id   = "rr-vertical-add-btn",
                        cls  = "rr-multiselect-add-btn",
                    ),
                    cls = "rr-multiselect-add-row",
                ),
                P(
                    "",
                    id     = "rr-vertical-error",
                    cls    = "rr-multiselect-error",
                    hidden = True,
                    role   = "alert",
                ),
                cls = "rr-multiselect-add",
            ),
            Div(cls = "rr-multiselect-divider"),
            Div(
                *(
                    _VerticalCheckboxRow(
                        code     = code,
                        label    = label,
                        checked  = code in _VERTICAL_DEFAULT,
                        is_curated = True,
                    )
                    for (code, label) in _VERTICAL_OPTIONS
                ),
                id  = "rr-vertical-options",
                cls = "rr-multiselect-options",
            ),
            cls = "rr-multiselect-panel",
        ),
        # Inline taxonomy for the client-side validator. `<script type=…>` keeps
        # the JSON out of the DOM-as-text stream and lets main.js parse it once.
        Script(
            NotStr(json.dumps(sorted(ARXIV_CATEGORIES))),
            id   = "rr-vertical-taxonomy",
            type = "application/json",
        ),
        _VerticalBrowseDialog(),
        id  = "rr-multiselect",
        cls = "rr-multiselect",
    )


def _VerticalBrowseDialog():
    """Native `<dialog>` listing every arXiv code, grouped by archive.

    Triggered by the "Browse all (155)" button next to the add-custom field.
    Clicking any code injects it into `#verticals` (via the same add path as
    the custom-add input) and closes the dialog. Server-side validation in
    `domains/rr/schemas.py` still runs at submit — defense-in-depth."""
    sections = []
    for archive, subs in _BY_ARCHIVE.items():
        sections.append(
            Div(
                H4(
                    f"{_ARCHIVE_LABELS.get(archive, archive)} ",
                    Span(f"({len(subs)})", cls = "rr-browse-section-count"),
                    cls = "rr-browse-section-title",
                ),
                Div(
                    *(
                        Button(
                            f"{archive}.{sub}",
                            type = "button",
                            cls  = "rr-browse-code",
                            **{
                                "data-vertical-code": f"{archive}.{sub}",
                                "data-tooltip": ARXIV_DESCRIPTIONS.get(f"{archive}.{sub}", ""),
                                "aria-label": (
                                    f"{archive}.{sub} — "
                                    f"{ARXIV_DESCRIPTIONS.get(f'{archive}.{sub}', '')}"
                                ),
                            },
                        )
                        for sub in subs
                    ),
                    cls = "rr-browse-code-grid",
                ),
                cls = "rr-browse-section",
            )
        )
    # Standalone archives — flat list, no dotted suffix.
    sections.append(
        Div(
            H4(
                "Standalone archives ",
                Span(f"({len(_STANDALONE)})", cls = "rr-browse-section-count"),
                cls = "rr-browse-section-title",
            ),
            Div(
                *(
                    Button(
                        code,
                        type = "button",
                        cls  = "rr-browse-code",
                        **{
                            "data-vertical-code": code,
                            "data-tooltip":       ARXIV_DESCRIPTIONS.get(code, ""),
                            "aria-label":         f"{code} — {ARXIV_DESCRIPTIONS.get(code, '')}",
                        },
                    )
                    for code in _STANDALONE
                ),
                cls = "rr-browse-code-grid",
            ),
            cls = "rr-browse-section",
        )
    )
    return Dialog(
        Div(
            Div(
                H3("All arXiv subject codes", cls = "rr-browse-title"),
                Button(
                    "×",
                    type = "button",
                    cls  = "rr-browse-close",
                    **{"aria-label": "Close", "data-rr-close-dialog": "true"},
                ),
                cls = "rr-browse-header",
            ),
            P(
                "Click a code to add it. Source: ",
                NotStr('<a href="https://arxiv.org/category_taxonomy" '
                       'target="_blank" rel="noopener">arxiv.org/category_taxonomy</a>'),
                cls = "rr-browse-subtitle",
            ),
            Div(*sections, cls = "rr-browse-body"),
            cls = "rr-browse-content",
        ),
        id  = "rr-vertical-browse-dialog",
        cls = "rr-browse-dialog",
        **{"aria-labelledby": "rr-browse-title"},
    )


def _VerticalCheckboxRow(*, code: str, label: str, checked: bool, is_curated: bool):
    """One row inside the multi-select panel. `is_curated=False` rows are the
    user's custom additions injected by JS — same shape so behavior is uniform."""
    return Label(
        Input(
            type    = "checkbox",
            value   = code,
            checked = checked,
            cls     = "rr-multiselect-checkbox",
            **{"data-vertical-code": code},
        ),
        Span(code, cls = "rr-multiselect-code"),
        Span("—", cls = "rr-multiselect-dash"),
        Span(label, cls = "rr-multiselect-label"),
        cls = "rr-multiselect-row"
              + ("" if is_curated else " rr-multiselect-row-custom"),
        **{"data-curated": "true" if is_curated else "false"},
    )


def ScanForm(extra_actions=None):
    """Public — lifted out of `RRBody` so `toolbar.py` can mount it in row 3
    of the page chrome (same pattern as `features/dd/shared/toolbar.py`
    and `features/ycs/shared/toolbar.py`). Form submission still routes
    through `main.js`'s `#rr-scan-form` handler.

    `extra_actions` is optional — when provided, it's appended INSIDE the
    `.rr-actions` cluster after Start + Stop. `toolbar.py` uses this to
    park the Recent-scans picker right next to the Start/Stop buttons so
    they read as one action group on the right of row 3."""
    return Form(
        Div(
            Label("Topic", For="topic", cls = "rr-label"),
            Input(
                id          = "topic",
                name        = "topic",
                placeholder = "e.g. deep agents",
                value       = "deep agents",
                required    = True,
                cls         = "rr-input",
            ),
            cls = "rr-field",
        ),
        Div(
            Div(
                Label("Verticals", For="verticals", cls = "rr-label"),
                Button(
                    "i",
                    type = "button",
                    cls  = "rr-info",
                    **{
                        "aria-label":   "More info about verticals",
                        "data-tooltip": (
                            "Pick arXiv subject codes from the dropdown, or type "
                            "a custom one (e.g. eess.SP) into the add field. "
                            "Codes are validated against the full arXiv taxonomy."
                        ),
                    },
                ),
                cls = "rr-label-row",
            ),
            _VerticalMultiSelect(),
            # Hidden source of truth at submit time. JS keeps it synced from
            # the checkbox state inside _VerticalMultiSelect.
            Input(
                id    = "verticals",
                name  = "verticals",
                type  = "hidden",
                value = ", ".join(_VERTICAL_DEFAULT),
            ),
            cls = "rr-field rr-field-verticals",
        ),
        Div(
            Div(
                Label("Deep reads", For="top_n", cls = "rr-label"),
                Button(
                    "i",
                    type = "button",
                    cls  = "rr-info",
                    **{
                        "aria-label":   "More info about Deep reads",
                        "data-tooltip": (
                            "Papers to extract in detail. More = deeper "
                            "digest, longer scan. Range 4–100, default 8."
                        ),
                    },
                ),
                cls = "rr-label-row",
            ),
            # 2026-06-17: range slider replaced with number input — the
            # operator types specific N values (8, 12, …) every time, so
            # drag-to-precision was friction. Default value 8 reflects the
            # validated steady-state from MVP stability runs; schema in
            # domains/rr/schemas.py keeps API default at 12 for callers
            # who don't specify. localStorage persistence (main.js) still
            # applies — the typed value survives refreshes.
            Input(
                id    = "top_n",
                name  = "top_n",
                type  = "number",
                value = "8",
                min   = "4",
                max   = "100",
                step  = "1",
                cls   = "rr-top-n-input",
            ),
            cls = "rr-field rr-field-top-n",
        ),
        Div(
            # 2026-06-17 (v2): the topic pill has MOVED out of the
            # action cluster (and out of the row-3 toolbar entirely)
            # into a dedicated `.rr-topic-strip` at the top of the
            # PipelineBody / DigestBody. Reason: with the Recent-scans
            # dropdown also in this cluster, a long topic was spilling
            # over the other toolbar elements. The page-body location
            # gives the pill its own row so it can run as wide as it
            # needs without compromising the toolbar layout. The
            # `#rr-status-topic` element id stays the same so main.js's
            # `_setPillTopic()` works without changes.
            Button(
                Span("Start Scan", cls = "rr-btn-text"),
                type = "submit",
                cls  = "rr-submit",
                id   = "rr-start-btn",
            ),
            Button(
                # Spinner span is hidden until JS flips `data-busy="true"`
                # on the button. Two text spans so we can swap "Stop Scan" /
                # "Cancelling…" without re-creating DOM nodes.
                Span(cls = "rr-spinner", **{"aria-hidden": "true"}),
                Span("Stop Scan",   cls = "rr-btn-text rr-btn-text-idle"),
                Span("Cancelling…", cls = "rr-btn-text rr-btn-text-busy"),
                type   = "button",
                cls    = "rr-stop",
                id     = "rr-stop-btn",
                hidden = True,
                **{"data-busy": "false"},
            ),
            extra_actions if extra_actions is not None else "",
            cls = "rr-actions",
        ),
        id  = "rr-scan-form",
        cls = "rr-form",
    )


# NOTE: the page-body helpers (status strip + digest area + the legacy
# combined `RRBody()`) have moved to `pipeline.py` and `digest.py` for the
# row-2 stage split (Pipeline / Digest). This file now owns *only* the
# form widgets the row-3 toolbar mounts.
