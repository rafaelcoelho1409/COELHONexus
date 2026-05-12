"""
Sidebar — Memos-style left rail navigation.

Direct port of apps/web/templates/sidebar.templ + the inline sidebar in
apps/web/main.go's homePage. Same nav groups (Knowledge, YouTube RAG, Ops),
same Lucide icons, same emerald/forest theme toggle button at the bottom.

Future-feature links (`/library`, `/youtube/*`, `/runs`, `/catalog`,
`/settings`) are kept here for visual parity with the Go scaffold — they'll
404 until those routes ship, same as in apps/web.
"""
from fasthtml.common import A, Aside, Button, Div, I, Nav, Span


def NavItem(key: str, icon: str, label: str, href: str, active: str):
    """One sidebar row. `key` matches the `active_nav` arg passed to Page()."""
    cls = "nav-item nav-item-active" if key == active else "nav-item"
    return A(
        I(data_lucide=icon, cls="w-4 h-4"),
        Span(label),
        href=href,
        cls=cls,
    )


def Sidebar(active: str = ""):
    """The fixed left-rail navigation. `active` highlights the current page."""
    return Aside(
        # Brand
        Div(
            Div(
                Span(
                    I(data_lucide="layers", cls="w-5 h-5"),
                    cls="w-8 h-8 rounded-md bg-primary/10 text-primary flex items-center justify-center",
                ),
                Div(
                    Div("COELHONexus", cls="font-semibold text-sm"),
                    Div("AI Engineering Hub", cls="text-[0.65rem] text-base-content/60 uppercase tracking-wider"),
                ),
                cls="flex items-center gap-2",
            ),
            cls="px-4 py-5 border-b border-base-300",
        ),
        # Nav groups
        Nav(
            Div("Knowledge", cls="text-[0.7rem] uppercase tracking-wider text-base-content/50 px-3 py-2"),
            NavItem("home", "home", "Home", "/", active),
            NavItem("kd-studies", "book-open-text", "Studies", "/kd/studies", active),
            NavItem("kd-inspect", "file-search", "Inspect Markdown", "/kd/inspect", active),
            NavItem("kd-map-compare", "scale", "MAP A/B Compare", "/kd/map-compare", active),
            NavItem("catalog", "activity", "Catalog Health", "/catalog", active),
            Div("YouTube RAG", cls="text-[0.7rem] uppercase tracking-wider text-base-content/50 px-3 py-2 mt-2"),
            NavItem("youtube", "message-square-more", "Ask the Corpus", "/youtube/ask", active),
            NavItem("youtube-ingest", "download", "Ingest", "/youtube/ingest", active),
            NavItem("youtube-graph", "network", "Knowledge Graph", "/youtube/graph", active),
            Div("Ops", cls="text-[0.7rem] uppercase tracking-wider text-base-content/50 px-3 py-2 mt-2"),
            NavItem("runs", "git-commit-horizontal", "Active Runs", "/runs", active),
            NavItem("settings", "settings", "Settings", "/settings", active),
            cls="flex-1 px-3 py-4 flex flex-col gap-1 overflow-y-auto",
        ),
        # Footer (theme toggle + version)
        Div(
            Button(
                I(data_lucide="moon", cls="w-4 h-4"),
                onclick="toggleTheme()",
                cls="btn btn-sm btn-ghost",
                title="Toggle theme",
            ),
            Span("v0.1 · FastHTML", cls="text-[0.7rem] text-base-content/50"),
            cls="px-3 py-3 border-t border-base-300 flex items-center justify-between",
        ),
        cls="fixed left-0 top-0 h-screen w-64 bg-base-100 border-r border-base-300 flex flex-col z-20",
    )
