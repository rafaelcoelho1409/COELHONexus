"""
Home + meta routes — landing page, container health probe, FastAPI health test.

Route map (parity with apps/web/main.go):
  GET /            → HomePage()
  GET /health      → "OK" (k8s container probe)
  GET /api/test    → DaisyUI alert fragment with FastAPI /health round-trip
"""
from fasthtml.common import APIRouter, Div, I, Span
from starlette.responses import PlainTextResponse

from components.home import HomePage
from services.fastapi_client import health_probe


ar = APIRouter()


@ar("/")
async def index():
    """Landing page — sidebar + feature cards + backend test strip."""
    return HomePage()


@ar("/health")
async def health():
    """k8s container probe — must be quick and dependency-free."""
    return PlainTextResponse("OK")


@ar("/api/test")
async def api_test():
    """
    HTMX target for the home page's "Test /health" button. Calls FastAPI
    /health and renders a DaisyUI alert (success or error) — same markup as
    apps/web/main.go::testFastAPIHandler so the swap looks identical.
    """
    ok, body = await health_probe()
    if ok:
        return Div(
            I(data_lucide="check-circle", cls="w-4 h-4"),
            Div(
                Span("Healthy", cls=""),
                Span(body, cls="text-[0.7rem] text-base-content/70 font-mono block"),
            ),
            role="alert",
            cls="alert alert-success text-xs p-3",
        )
    return Div(
        I(data_lucide="x-circle", cls="w-4 h-4"),
        Span(f"FastAPI error: {body}"),
        role="alert",
        cls="alert alert-error text-xs p-3",
    )
