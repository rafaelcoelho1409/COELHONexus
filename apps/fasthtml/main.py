from fasthtml.common import fast_app, serve
from starlette.responses import PlainTextResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from shell import HEAD



# Mount Starlette's StaticFiles at /static — predictable URL→file mapping
# (FastHTML's `static_path` arg has a known bug, see AnswerDotAI/fasthtml#410).
app, rt = fast_app(
    pico = False,
    htmx = False,
    default_hdrs = False,
    live = False,
    hdrs = HEAD,
    #routes = [
    #  Mount("/static", 
    #  StaticFiles(directory = "static"), 
    #  name = "static")
    #],
)


# Helm startup/liveness/readiness probes hit GET /health on :3000
# (k8s values.yaml fasthtml.*ProbeSettings). A plain 200 is all the
# httpGet probe needs to mark the pod healthy.
@rt("/health")
def health():
    return PlainTextResponse("OK")


if __name__ == "__main__":
    serve()
