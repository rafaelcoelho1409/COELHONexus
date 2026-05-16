"""COELHO Nexus — FastHTML base shell."""
from fasthtml.common import H1, P, Title, fast_app, serve
from starlette.responses import PlainTextResponse


app, rt = fast_app(
    pico=False,
    htmx=False,
    default_hdrs=False,
    live=False,
)


@rt("/")
def index():
    return Title("COELHONexus"), H1("COELHONexus"), P("Base shell.")


@rt("/health")
def health():
    return PlainTextResponse("OK")


if __name__ == "__main__":
    serve()
