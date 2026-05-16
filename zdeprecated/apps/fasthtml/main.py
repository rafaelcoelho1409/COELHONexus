"""
COELHO Nexus — FastHTML web frontend (HTMX-driven, server-rendered)

Drop-in replacement for the Go `apps/web/main.go`. Same routes, same HTMX
contracts, same DaisyUI/Tailwind markup — written in Python so the whole
stack lives in one language alongside FastAPI + Celery + the KD pipeline.

Architecture:
  - `fast_app(...)` factory injects HTMX + custom <head> assets (Tailwind CDN,
    DaisyUI, Lucide, fonts).
  - Routes are split by feature into `routes/home.py` and `routes/kd.py`,
    composed via FastHTML's `APIRouter.to_app(app)` pattern.
  - `services/fastapi_client.py` owns the upstream FastAPI HTTP client and the
    reverse-proxy helper used by /api/kd/inspect/* and /api/test.
  - Static assets are served directly by FastHTML from `apps/fasthtml/static/`,
    URL prefix `/static/`.

Visual parity with the Go app: Tailwind + DaisyUI via CDN (zero build step),
emerald (light) + forest (dark) themes persisted in localStorage, Lucide
icons re-initialized on every htmx swap. PWA service worker registered from
`/static/sw.js` exactly as before.
"""
from fasthtml.common import fast_app, serve
from starlette.staticfiles import StaticFiles

from components.base import HEAD_ASSETS
from routes.home import ar as home_router
from routes.kd import ar as kd_router


# Build the FastHTML app. We disable the default htmx auto-inject because the
# CDN <script> tags in `HEAD_ASSETS` already pin specific htmx + htmx-ext-sse
# versions matching what apps/web shipped (htmx 2.0.4, sse 2.2.3). `pico=False`
# turns off the default Pico CSS — we use Tailwind/DaisyUI exclusively.
app, rt = fast_app(
    pico=False,
    htmx=False,            # we ship our own pinned versions in HEAD_ASSETS
    default_hdrs=False,    # don't auto-inject any default headers
    hdrs=HEAD_ASSETS,
    live=False,            # uvicorn --reload handles live reload
    htmlkw={"lang": "en", "data-theme": "emerald"},  # default theme = emerald
    bodykw={"class": "min-h-screen bg-base-200 text-base-content"},
)


# Mount /static via Starlette's StaticFiles. fast_app's `static_path` kwarg is
# only a fallback handler for bare-extension URLs (`/foo.css`); it does NOT
# auto-mount a `/static/*` prefix. We need that prefix because every page in
# HEAD_ASSETS, sidebar.py, etc. references `/static/manifest.json`,
# `/static/sw.js`, `/static/icons/icon.svg` — the same paths apps/web used.
app.mount("/static", StaticFiles(directory="static"), name="static")


# Mount feature routers. Both routers expose `ar` (an APIRouter instance).
home_router.to_app(app)
kd_router.to_app(app)


if __name__ == "__main__":
    serve()
