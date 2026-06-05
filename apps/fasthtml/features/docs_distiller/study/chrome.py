"""Study toolbar pieces — mode switch + search/focus utilities.

Reader-mode switch (Learn / Flashcards) + the mobile chapter-drawer
toggle, relocated to the row-3 toolbar (2026-05-28). IDs/classes are
preserved so study.js bindings (S.studyTabBtns, #fw-study-toc-toggle)
keep working unchanged. ☰ Chapters is hidden on desktop via CSS; the
Learn/Flashcards pair renders as a segmented control."""
from fasthtml.common import Button, Div


def StudyTabs():
    return Div(
        Button("☰ Chapters", id = "fw-study-toc-toggle",
               cls = "fw-study-toc-toggle", type = "button",
               title = "Show chapters"),
        Div(
            Button("Learn", cls = "fw-study-tab active",
                   data_tab = "learn", type = "button"),
            Button("Flashcards", cls = "fw-study-tab",
                   data_tab = "flashcards", type = "button"),
            cls = "fw-study-modes", role = "tablist",
        ),
        cls = "fw-study-toolgroup",
    )


def StudyViewButtons():
    return (
        Button("🔍 Search", id = "fw-study-search-btn",
               cls = "fw-study-search-btn", type = "button",
               title = "Search all chapters (⌘K / Ctrl-K)"),
        Button("⛶", id = "fw-study-focus-toggle",
               cls = "fw-study-focus-toggle", type = "button",
               title = "Focus mode (distraction-free reading)"),
    )
