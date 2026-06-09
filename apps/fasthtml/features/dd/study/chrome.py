"""Study toolbar pieces — chapter-drawer toggle + search/focus utilities.

The Learn/Flashcards mode switch was removed 2026-06-08 along with the
Active Recall + FSRS Flashcards subsystems. ☰ Chapters is hidden on
desktop via CSS — the chapter rail is in-flow there."""
from fasthtml.common import Button


def StudyTabs():
    return Button(
        "☰ Chapters", id = "fw-study-toc-toggle",
        cls = "fw-study-toc-toggle", type = "button",
        title = "Show chapters",
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
