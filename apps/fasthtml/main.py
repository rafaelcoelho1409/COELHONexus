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


app, rt = fast_app(
    pico=False,
    htmx=False,
    default_hdrs=False,
    live=False,
    hdrs=HEAD,
    routes=[Mount("/static", StaticFiles(directory="static"), name="static")],
)


proxy.register(rt)
home.register(rt)
docs_distiller.register(rt)
youtube_content_search.register(rt)
routes.register(rt)


if __name__ == "__main__":
    serve()
