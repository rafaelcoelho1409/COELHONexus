"""GET /api/v1/docs-distiller/frameworks — read sources.yaml and return the catalog."""
from pathlib import Path

import yaml
from fastapi import APIRouter

router = APIRouter()

# /app/routers/v1/docs_distiller/frameworks.py → parents[3] = /app
SOURCES_PATH = Path(__file__).resolve().parents[3] / "files" / "sources.yaml"


def _load_frameworks() -> list[dict]:
    with open(SOURCES_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("frameworks", [])


@router.get("/frameworks")
def list_frameworks() -> list[dict]:
    """Return the full framework catalog. Re-read on every request so YAML
    edits land without a pod restart."""
    return _load_frameworks()
