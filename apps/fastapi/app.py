import logging
from contextlib import asynccontextmanager


# uvicorn 0.32+ doesn't attach a handler to the root logger, so any
# `logging.getLogger(__name__).warning(...)` from app code goes nowhere
# unless we configure one. Set INFO so lifespan/init breadcrumbs are
# visible alongside uvicorn's own access log lines.
logging.basicConfig(
    level = logging.INFO,
    format = "%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title = "COELHO Nexus - FastAPI",
    description = "COELHO Nexus - FastAPI",
    version = "1.0.0",
    lifespan = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"],
)


@app.get("/")
async def root():
    return {
        "service": "FastAPI Service - COELHO Nexus",
        "version": "1.0.0",
        "endpoints": {
            "docs": "/docs",
            "health": "/health"
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "COELHO Nexus"}
