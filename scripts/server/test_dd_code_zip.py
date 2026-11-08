"""Offline check of the route that serves a tutorial chapter's `code/` folder as a zip: the files, their names relative to the chapter, and a 404 for a chapter
that has none.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_code_zip.py
"""
import asyncio
import io
import os
import sys
import zipfile
from pathlib import Path

for _name, _value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "", "ENVIRONMENT": "local", "DD_STUDY_SEM": "1"}.items():
    os.environ.setdefault(_name, _value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import infra
from fastapi import HTTPException

import importlib

# the package re-exports its APIRouter under the module's own name, so the module itself is fetched by name
router = importlib.import_module("entrypoints.api.v1.dd.pipeline.synth.router")


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class FakeMinio:
    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = objects

    async def list(self, prefix):
        return [key for key in self.objects if key.startswith(prefix)]

    async def read_many(self, keys):
        return [self.objects[key] for key in keys]


async def body_of(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator]) if hasattr(response.body_iterator, "__aiter__") else b"".join(response.body_iterator)


async def main() -> None:
    root = domains.dd.pipeline.storage.keys.chapter_artifact_key("fw", "ch-01", "")
    fake = FakeMinio({
        root + "README.md": "# Chapter",
        root + "code/INDEX.md": "# Code",
        root + "code/capstone/main.py": "print('hi')\n",
        root + "code/steps/01-setup/01-install.sh": "pip install x\n",
        domains.dd.pipeline.storage.keys.chapter_artifact_key("fw", "ch-02", "") + "code/capstone/main.py": "print('other')\n",
    })
    infra.minio.service.get_storage = lambda: fake

    response = await router.get_study_code("fw", "ch-01")
    check("the zip is served as an attachment named for the chapter", (response.media_type, response.headers["content-disposition"]), ("application/zip", 'attachment; filename="ch-01-code.zip"'))
    archive = zipfile.ZipFile(io.BytesIO(await body_of(response)))
    check("it holds the code folder, relative to the chapter, and nothing else", sorted(archive.namelist()), ["code/INDEX.md", "code/capstone/main.py", "code/steps/01-setup/01-install.sh"])
    check("the files are what was stored", archive.read("code/capstone/main.py").decode(), "print('hi')\n")

    try:
        await router.get_study_code("fw", "ch-03")
    except HTTPException as error:
        check("a chapter with no code folder is a 404 that says why", (error.status_code, "tutorial chapter" in error.detail), (404, True))
    else:
        raise AssertionError("a chapter with no code folder should be a 404")
    print("\ncode zip: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
