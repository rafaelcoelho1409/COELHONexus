"""infra/qdrant — shared async Qdrant client.

YCS Slice 5+6 owns the only consumer today (`ycs-transcripts` collection)
but the client lives here so future DD work doesn't fork the wiring."""
from . import params, service


__all__ = ["params", "service"]
