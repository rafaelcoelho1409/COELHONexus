"""COELHO Nexus — FastHTML application entry point.

Assembly only: build the app, mount /static, register every feature
router, then serve. Everything else lives in dedicated packages:

  layout/                — HEAD + topbar/_Shell page chrome
  proxy.py               — /api/{path:path} → FastAPI reverse proxy
  features/<name>/       — one package per feature, exposing register(rt)
    home/                — / landing
    dd/                  — /docs-distiller wizard (5 stage pages)
    settings/            — /settings BYOK + model selection
    ycs/                 — /youtube-content-search 3-step wizard
    common/              — /research-radar, /health
  static/css/            — split stylesheets (base, components, dd/*, …)
  static/js/dd/          — ES module wizard client-side logic
"""
from fasthtml.common import fast_app, serve
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

import proxy
from features import common, dd, home, rr, settings, ycs
from layout.head import HEAD


class _RevalidateStatics(StaticFiles):
    """StaticFiles that forces the browser to revalidate every asset.

    CSS ships as <link>s and JS as native ES modules. Module sub-imports
    (`import './synth.js'`) resolve relative to the importing module's
    URL and DON'T inherit any `?v=` query on the entry <script>, so
    query-string versioning can't reliably bust the whole module graph.

    Sending `Cache-Control: no-cache` makes the browser revalidate each
    asset against its ETag / Last-Modified (which Starlette emits):
    unchanged → fast 304, changed → fresh download. A skaffold redeploy
    is reflected on the next normal navigation."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app, rt = fast_app(
    pico = False,
    htmx = False,
    default_hdrs = False,
    live = False,
    hdrs = HEAD,
    routes = [Mount("/static", _RevalidateStatics(directory = "static"),
                    name = "static")],
)


# Remove FastHTML's built-in catch-all static-extension route, which
# `fast_app(...)` injects unconditionally as `/{fname:path}.{ext:static}`
# (regex matches png/jpg/svg/css/js/woff/…) and points at the working-
# directory `static_path = '.'`. With our explicit `/static` Mount above,
# that catch-all only does harm — it intercepts ANY URL ending in a known
# asset extension BEFORE the `/api/{path:path}` proxy gets to see it,
# including artifact endpoints like `/api/v1/.../artifacts/{sha}.png`
# which then 404 from FastHTML instead of being forwarded to FastAPI.
app.router.routes = [
    r for r in app.router.routes
    if getattr(r, "path", "") != "/{fname:path}.{ext:static}"
]


proxy.register(rt)
home.register(rt)
dd.register(rt)
ycs.register(rt)
rr.register(rt)
settings.register(rt)
common.register(rt)


if __name__ == "__main__":
    serve()
