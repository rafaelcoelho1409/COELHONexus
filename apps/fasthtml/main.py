"""COELHO Nexus — FastHTML application entry point.

Assembly only: build the app, mount /static, register every feature/page
router, then serve. Everything else lives in dedicated modules:

  shell.py                       — HEAD + topbar layout (_Shell)
  proxy.py                       — /api/{path:path} → FastAPI reverse proxy
  features/home.py               — / landing page (hero + live stats + features)
  features/docs_distiller.py     — /docs-distiller wizard (_Picker)
  routes.py                      — /coming-soon, /health
  static/css/                    — split stylesheets (base, components, dd/*, home, youtube)
  static/js/dd/                  — ES module wizard client-side logic
"""
from fasthtml.common import fast_app, serve
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

import proxy
import routes
from features import docs_distiller, home, youtube_content_search
from shell import HEAD


class _RevalidateStatics(StaticFiles):
    """StaticFiles that forces the browser to revalidate every asset.

    CSS ships as <link>s and JS as native ES modules. Module sub-imports
    (`import './synth.js'`) resolve relative to the importing module's
    URL and DON'T inherit any `?v=` query on the entry <script>, so
    query-string versioning can't reliably bust the whole module graph —
    which is why edits sometimes "don't show up" until a manual hard
    refresh.

    Sending `Cache-Control: no-cache` makes the browser revalidate each
    asset against its ETag / Last-Modified (which Starlette's StaticFiles
    already emits): unchanged files come back as a fast 304, changed
    files download fresh. Net: a skaffold redeploy is reflected on the
    next normal navigation — no hard refresh, no stale CSS/JS.
    """
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app, rt = fast_app(
    pico=False,
    htmx=False,
    default_hdrs=False,
    live=False,
    hdrs=HEAD,
    routes=[Mount("/static", _RevalidateStatics(directory="static"),
                  name="static")],
)


proxy.register(rt)
home.register(rt)
docs_distiller.register(rt)
youtube_content_search.register(rt)
routes.register(rt)


if __name__ == "__main__":
    serve()
