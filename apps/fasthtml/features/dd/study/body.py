"""Study body — single-mode reader (README only, 2026-06-08).

Active Recall + Flashcards subsystems removed. Reader is now a single
column: chapter README on the left, sticky TOC on the right."""
from fasthtml.common import Button, Div, Span


def StudyBody(slug: str | None):
    return Div(
        Div(
            "Pick a framework from the library, then run Synth on its "
            "chapters to populate this study viewer.",
            id = "fw-study-empty", cls = "fw-stage-empty",
        ),
        Div(
            Div(id = "fw-study-side-backdrop", cls = "fw-study-side-backdrop"),
            Div(
                Div(
                    Span("Chapters", cls = "fw-study-side-title"),
                    Button("×", id = "fw-study-side-close",
                           cls = "fw-study-side-close", type = "button",
                           title = "Close"),
                    cls = "fw-study-side-header",
                ),
                Div(id = "fw-study-chapter-list",
                    cls = "fw-study-chapter-list"),
                cls = "fw-study-side",
                id = "fw-study-side",
            ),
            Div(
                Div(id = "fw-study-chapter-head",
                    cls = "fw-study-chapter-head"),
                Div(
                    Div(
                        Div(
                            Div(
                                Div(
                                    "Open the ☰ Chapters window and pick a "
                                    "chapter.",
                                    cls = "fw-empty",
                                ),
                                id = "fw-study-readme",
                                cls = "fw-study-prose",
                            ),
                            cls = "fw-study-learn-col",
                        ),
                        Div(id = "fw-study-toc", cls = "fw-study-toc"),
                        cls = "fw-study-pane fw-study-readme-pane active",
                        data_tab = "learn",
                    ),
                    cls = "fw-study-content",
                ),
                cls = "fw-study-main",
                id = "fw-study-main",
            ),
            cls = "fw-study-grid",
            id = "fw-study-grid",
        ),
        cls = "fw-step-panel active",
        id = "fw-step-5-panel",
    )
