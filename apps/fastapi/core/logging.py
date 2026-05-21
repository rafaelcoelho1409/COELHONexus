"""Root logging configuration.

uvicorn 0.32+ does not attach a handler to the root logger, so any
`logging.getLogger(__name__).info(...)` from app code goes nowhere unless we
configure one. Call `configure_logging()` once at startup (from app.py).
"""
import logging


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level = level,
        format = "%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
