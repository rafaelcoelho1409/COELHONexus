"""infra/qdrant — shared async Qdrant client.

YCS Slice 5+6 owns the only consumer today (`ycs-transcripts` collection)
but the client lives here so future DD work doesn't fork the wiring."""
from .params import DEFAULT_DENSE_DIM
from .service import close_qdrant, get_qdrant


__all__ = ["DEFAULT_DENSE_DIM", "close_qdrant", "get_qdrant"]
