"""Base exceptions + global handler registration.

Domain packages raise subclasses of `AppError`; `app.py` calls
`register_exception_handlers(app)` once so they map to clean JSON responses
instead of bare 500s.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base for app-raised errors. Subclasses set `status_code`."""

    status_code: int = 500

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class NotFoundError(AppError):
    status_code = 404


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code = exc.status_code, 
            content = {"detail": exc.detail}
        )
