"""Study body — 2-mode reader (Learn / Flashcards).

Status pill moved to the row-3 toolbar. The reader's own README /
Challenges / Flashcards tabs stay in the body — they're content
navigation within a chapter, not stage-level chrome."""
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
                # 2-mode reader (2026-05-28): LEARN = prose + recall in
                # one scroll; FLASHCARDS = the FSRS reviewer as a separate
                # drill mode. The mode switch + Search + Focus live in
                # the row-3 toolbar (StudyTabs/StudyViewButtons); only
                # the chapter-head + panes remain in the body.
                Div(id = "fw-study-chapter-head",
                    cls = "fw-study-chapter-head"),
                Div(
                    # LEARN pane = scrolling column (prose + recall) +
                    # the right-rail TOC. Article keeps id
                    # `fw-study-readme`; recall block keeps id
                    # `fw-study-challenges` so study.js writes to both
                    # unchanged — they just share one scroll now.
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
                            Div(
                                id = "fw-study-challenges",
                                cls = "fw-study-recall fw-study-prose",
                            ),
                            cls = "fw-study-learn-col",
                        ),
                        Div(id = "fw-study-toc", cls = "fw-study-toc"),
                        cls = "fw-study-pane fw-study-readme-pane active",
                        data_tab = "learn",
                    ),
                    Div(
                        Div(
                            "Pick a chapter to study its flashcards.",
                            cls = "fw-empty",
                        ),
                        id = "fw-study-flashcards",
                        cls = "fw-study-pane fw-study-cards",
                        data_tab = "flashcards",
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
