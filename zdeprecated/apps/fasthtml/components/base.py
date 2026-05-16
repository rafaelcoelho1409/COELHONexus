"""
Base page shell — sidebar + main + theme/PWA scripts.

Visual parity with apps/web/main.go's inline `homePage` and `kdInspectPage`
(emerald/forest DaisyUI themes, Inter + JetBrains Mono fonts, HTMX + Lucide
re-init on swap, PWA service worker, theme-toggle persisted in localStorage).
"""
from fasthtml.common import (
    Aside, Body, Button, Div, Html, I, Link, Main, Meta, Nav, Script, Span,
    Style, Title,
)


# =============================================================================
# <head> assets — pinned CDN versions matching apps/web/main.go inline scripts
# =============================================================================
# Single source of truth: every page renders inside Base() which uses these,
# so a CDN bump only needs to change here.
HEAD_ASSETS = (
    Meta(charset="UTF-8"),
    Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
    Meta(name="theme-color", content="#16a34a"),
    Link(rel="manifest", href="/static/manifest.json"),
    Link(rel="preconnect", href="https://fonts.googleapis.com"),
    Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    Link(
        rel="stylesheet",
        href=(
            "https://fonts.googleapis.com/css2?"
            "family=Inter:wght@400;500;600;700"
            "&family=JetBrains+Mono:wght@400;500&display=swap"
        ),
    ),
    # DaisyUI 4 — emerald (light) + forest (dark) themes ship pre-compiled.
    Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/daisyui@4.12.14/dist/full.min.css"),
    # Tailwind via Play CDN with typography plugin (used in /kd/inspect prose).
    Script(src="https://cdn.tailwindcss.com?plugins=typography"),
    # HTMX 2.0.4 — pinned to match apps/web inline script.
    Script(src="https://unpkg.com/htmx.org@2.0.4"),
    # Idiomorph extension — DOM-merge swap strategy. Required so polling
    # containers (#kd-study-chapters, #kd-study-header) preserve native
    # element state across re-renders: <details> open/closed, focus, scroll
    # position, video playback, form input state. Carson Gross (HTMX
    # creator) explicitly recommends idiomorph for polled <details> state
    # preservation; HTMX 4.0 makes it the default swap strategy.
    Script(src="https://unpkg.com/idiomorph@0.7.3/dist/idiomorph-ext.min.js"),
    # HTMX SSE extension 2.2.3 — used by future /youtube/ask streaming view.
    Script(src="https://unpkg.com/htmx-ext-sse@2.2.3/sse.js", defer=True),
    # Lucide icons — `defer` so the script tag is non-blocking; we
    # re-init the icons after every htmx:afterSwap below.
    Script(src="https://unpkg.com/lucide@latest", defer=True),
    # Tailwind config (font families) — must run BEFORE Tailwind classes are
    # applied. Inlined as a small <script> right after the Tailwind CDN load.
    Script("""
        tailwind.config = {
          theme: { extend: { fontFamily: {
            sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
            mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
          } } },
        };
    """),
    # Page-level styles (memo card, nav-item, prose pre overrides for /kd/inspect).
    # Verbatim copy of the inline <style> block from apps/web/main.go to keep
    # visual parity until we wire up the compiled Tailwind pipeline.
    Style("""
        body { font-family: Inter, ui-sans-serif, system-ui, sans-serif;
          -webkit-font-smoothing: antialiased; line-height: 1.55; }
        code, pre { font-family: 'JetBrains Mono', ui-monospace, monospace; }
        .memo-card { background: hsl(var(--b1)); border: 1px solid hsl(var(--b3));
          border-radius: 0.5rem; padding: 1rem; transition: all 0.2s; }
        .memo-card:hover { box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
          border-color: hsl(var(--p) / 0.3); }
        .nav-item { display: flex; align-items: center; gap: 0.75rem;
          padding: 0.5rem 0.75rem; font-size: 0.875rem; font-weight: 500;
          border-radius: 0.375rem; color: hsl(var(--bc) / 0.7);
          text-decoration: none; transition: colors 0.15s; }
        .nav-item:hover { color: hsl(var(--bc)); background: hsl(var(--b2)); }
        .nav-item-active { background: hsl(var(--b2)); color: hsl(var(--p)); }
        /* Codehilite outputs <pre> with inline styles; ensure prose doesn't
           fight them by giving them their own wrapper appearance. */
        .prose pre { background: #0d1117; color: #e6edf3; border-radius: 0.5rem;
          padding: 1rem; overflow-x: auto; font-size: 0.85rem; line-height: 1.55; }
        .prose pre code { background: transparent; padding: 0; color: inherit; }
        .prose code { background: rgba(0,0,0,0.06); padding: 0.1rem 0.35rem;
          border-radius: 0.25rem; font-size: 0.9em; }
        [data-theme="forest"] .prose code { background: rgba(255,255,255,0.08); }
        /* HTMX indicator — hidden by default, shown while a request is in flight.
           HTMX sets `htmx-request` on the element with `hx-indicator` (or its
           closest ancestor); we toggle display via that class. Using display
           rather than opacity so the indicator doesn't take up space when idle. */
        .htmx-indicator { display: none !important; }
        .htmx-request .htmx-indicator,
        .htmx-request.htmx-indicator { display: flex !important; }
    """),
)


# =============================================================================
# Footer scripts — theme toggle, Lucide re-init, PWA registration
# =============================================================================
# Same logic as apps/web/main.go's inline <script>: applied at body-end so
# the DOM is parsed before init runs. Kept as a single function so every
# page that uses Base() gets identical behavior.
_FOOTER_SCRIPT = """
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js").catch(()=>{}));
    }
    // DaisyUI built-in themes: emerald (light, green primary) <-> forest (dark, green primary).
    // Auto-detect system preference on first load, then remember user's choice.
    const themeKey = "coelhonexus:theme";
    const LIGHT = "emerald", DARK = "forest";
    const saved = localStorage.getItem(themeKey);
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initial = saved || (prefersDark ? DARK : LIGHT);
    document.documentElement.setAttribute("data-theme", initial);
    window.toggleTheme = () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next = cur === DARK ? LIGHT : DARK;
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem(themeKey, next);
    };
    // Lucide re-init on every htmx swap so newly-rendered icons appear.
    document.addEventListener("DOMContentLoaded", () => { if (window.lucide) window.lucide.createIcons(); });
    document.body.addEventListener("htmx:afterSwap", () => { if (window.lucide) window.lucide.createIcons(); });
    // Cmd/Ctrl+K focuses the search input if present (templ scaffold parity).
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        const el = document.querySelector("[data-cmdk-input]");
        if (el) { e.preventDefault(); el.focus(); }
      }
      if (e.key === "/" && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
        const el = document.querySelector("[data-search-input]");
        if (el) { e.preventDefault(); el.focus(); }
      }
    });
"""


def Page(title: str, *body_children, active_nav: str = ""):
    """
    Top-level page wrapper. Drop body children into the main column; the
    sidebar + footer scripts are added automatically.

    Args:
        title:       browser tab title (will be suffixed " · COELHONexus")
        body_children: arbitrary FT components to render inside <main>.
        active_nav:  key matching one of the Sidebar nav items
                     ("home", "kd-inspect", "youtube", ...). Highlights
                     the corresponding row.

    Returns the full <html>…</html> document. fast_app() will wrap this in
    the request-response cycle automatically — return its output from a route.
    """
    # Local import to avoid a circular reference at module load time
    # (sidebar imports from base for some shared classes).
    from components.sidebar import Sidebar
    return Html(
        Title(f"{title} · COELHONexus"),
        *HEAD_ASSETS,
        Body(
            Div(
                Sidebar(active_nav),
                Main(*body_children, cls="flex-1 ml-64"),
                cls="flex min-h-screen",
            ),
            Script(_FOOTER_SCRIPT),
            cls="min-h-screen bg-base-200 text-base-content",
        ),
        lang="en",
        data_theme="emerald",
    )
