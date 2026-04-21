"""
Knowledge Distiller — Anki export service

Reads each chapter's flashcards.json from MinIO, builds one `genanki.Deck`
per chapter (hierarchical naming so the user can study one chapter at a
time), packages everything into a single `.apkg` file, and uploads the
result back to MinIO at <study_root>/exports/study.apkg.

DETERMINISTIC IDS:
  genanki requires 32-bit integer IDs for Model and Deck. Re-running an
  export for the same study must produce compatible IDs so Anki treats
  the import as an UPDATE, not a duplicate. We hash stable inputs
  (framework + chapter number) into [2^30, 2^31) which is the range the
  genanki README recommends for hand-picked IDs.

DECK HIERARCHY:
  Anki uses "::" as a deck-hierarchy separator. We emit decks named
  "{framework} :: Chapter {NN} - {title}" so the user's Anki app shows
  a single folder for the study with one sub-deck per chapter.

SIDE NOTE:
  genanki.Package.write_to_file() writes to the local filesystem. We use
  a tempfile in the worker container and stream bytes into MinIO, then
  clean up. No persistent local artifact.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile

import genanki

from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# Chapters go from 01 to _MAX_CHAPTERS — matching synthesize_chapter's output.
_MAX_CHAPTERS = 12


def _stable_id(*parts: str) -> int:
    """
    Produce a deterministic 32-bit integer ID from arbitrary string inputs.
    Range matches genanki's recommendation: [2^30, 2^31).
    """
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).digest()
    # Take 8 bytes, mod into the target range, then offset.
    n = int.from_bytes(digest[:8], "big")
    return (1 << 30) + (n % ((1 << 31) - (1 << 30)))


# A single model shared across every study's deck. The model defines the card
# schema (front/back) and CSS — stable across runs by design so imports
# accumulate rather than create duplicate note types in the user's Anki.
_MODEL_ID = _stable_id("coelhonexus", "knowledge-distiller", "qa-model", "v1")
_KD_MODEL = genanki.Model(
    _MODEL_ID,
    "COELHO Nexus — Knowledge Distiller Q/A",
    fields = [
        {"name": "Front"},
        {"name": "Back"},
        {"name": "Chapter"},
    ],
    templates = [
        {
            "name": "Card 1",
            "qfmt": "<div class=\"chapter\">{{Chapter}}</div>\n{{Front}}",
            "afmt": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
        },
    ],
    css = """
        .card {
            font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;
            font-size: 18px;
            line-height: 1.5;
            text-align: left;
            color: #222;
            background: #fafafa;
        }
        .chapter {
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
        }
        code {
            font-family: "SF Mono", Monaco, Menlo, Consolas, monospace;
            background: #eee;
            padding: 2px 5px;
            border-radius: 3px;
        }
        hr#answer {
            border: 0;
            border-top: 1px solid #ccc;
            margin: 18px 0;
        }
    """,
)


def _safe_deck_slug(title: str) -> str:
    """Trim a chapter title into something Anki can display cleanly."""
    # Replace "::" (genanki's hierarchy separator) with a safe alternative,
    # collapse whitespace, and cap length — long titles wrap awkwardly in Anki.
    cleaned = title.replace("::", ":").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80]


async def _load_chapter_flashcards(
    storage: MinIOStudyStorage,
    study_root: str,
    chapter_num: int) -> list[dict] | None:
    """
    Read and parse flashcards.json for one chapter. Returns None if the
    file doesn't exist or is malformed — caller skips that chapter.
    """
    key = f"{study_root}/chapter{chapter_num:02d}/flashcards.json"
    if not await storage.exists(key):
        return None
    raw = await storage.read_text(key)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[anki] chapter {chapter_num} flashcards.json parse error: {e}")
        return None
    if not isinstance(data, list):
        logger.warning(f"[anki] chapter {chapter_num} flashcards.json is not a list")
        return None
    return data


async def _read_chapter_title(
    storage: MinIOStudyStorage,
    study_root: str,
    chapter_num: int) -> str:
    """
    Pull a human-readable chapter title from the README's first heading.
    Falls back to "Chapter NN" when no heading is found.
    """
    key = f"{study_root}/chapter{chapter_num:02d}/README.md"
    if not await storage.exists(key):
        return f"Chapter {chapter_num:02d}"
    text = await storage.read_text(key)
    # First ATX-style heading line ("# ..." or "## ...")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return _safe_deck_slug(stripped.lstrip("#").strip())
    return f"Chapter {chapter_num:02d}"


def _build_package(decks: list[genanki.Deck]) -> genanki.Package:
    """Wrap N decks into a single .apkg. genanki.Package accepts a list of Deck."""
    return genanki.Package(decks)


def _write_package_to_path(pkg: genanki.Package, path: str) -> None:
    """
    Sync genanki write. The caller wraps this in asyncio.to_thread so the
    event loop isn't blocked during the sqlite + zip work (~100ms for a
    normal-sized study, but non-trivial for big decks).
    """
    pkg.write_to_file(path)


async def render_anki_deck(
    storage: MinIOStudyStorage,
    study_root: str,
    framework: str) -> tuple[str, int]:
    """
    Build the Anki package for a completed study and upload to MinIO.

    Returns (object_key, bytes_written). Raises FileNotFoundError if the
    study has no flashcards at all (no chapter yielded a parseable JSON).
    """
    framework_root = _safe_deck_slug(framework or "Knowledge Distiller")
    decks: list[genanki.Deck] = []
    total_cards = 0

    for n in range(1, _MAX_CHAPTERS + 1):
        cards = await _load_chapter_flashcards(storage, study_root, n)
        if not cards:
            continue
        title = await _read_chapter_title(storage, study_root, n)
        deck_id = _stable_id(study_root, f"chapter-{n:02d}")
        deck_name = f"{framework_root}::Chapter {n:02d} - {title}"
        deck = genanki.Deck(deck_id, deck_name)
        chapter_label = f"Chapter {n:02d} — {title}"
        for card in cards:
            # Each entry is a Flashcard Pydantic dump: {"front": ..., "back": ...}
            front = (card.get("front") or "").strip()
            back = (card.get("back") or "").strip()
            if not front or not back:
                continue
            note = genanki.Note(
                model = _KD_MODEL,
                fields = [front, back, chapter_label],
            )
            deck.add_note(note)
            total_cards += 1
        if deck.notes:
            decks.append(deck)

    if not decks:
        raise FileNotFoundError(
            f"No flashcards found under {study_root!r} — nothing to export as .apkg"
        )

    pkg = _build_package(decks)
    tmp_dir = tempfile.mkdtemp(prefix = "kd-anki-")
    tmp_path = os.path.join(tmp_dir, "study.apkg")
    try:
        try:
            await asyncio.to_thread(_write_package_to_path, pkg, tmp_path)
        except Exception as e:
            raise RuntimeError(f"genanki.write_to_file failed: {e}") from e

        with open(tmp_path, "rb") as fh:
            body = fh.read()
        output_key = f"{study_root}/exports/study.apkg"
        bytes_written = await storage.write(
            key = output_key,
            content = body,
            content_type = "application/octet-stream",
        )
        logger.info(
            f"[anki] wrote {len(decks)} decks, {total_cards} cards "
            f"({bytes_written} bytes) → {output_key}"
        )
        return output_key, bytes_written
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass
