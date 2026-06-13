"""/research-radar route — replaces the placeholder in features/common/routes.py.

The placeholder there should be deleted; main.py's call ordering puts rr
BEFORE common, but better to remove the duplicate registration.
"""
from layout.shell import _Shell

from .body import RRBody


def register(rt) -> None:
    @rt("/research-radar")
    def research_radar():
        return _Shell(
            "research-radar",
            "Research Radar",
            body = RRBody(),
        )
