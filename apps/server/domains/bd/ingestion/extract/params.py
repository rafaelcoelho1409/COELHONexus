from __future__ import annotations

import os


# A folder of models downloaded ahead of time (`docling-tools models download`), which Docling then uses without fetching anything:
# for an air-gapped worker. Unset (the default), Docling fetches its layout and table models through the Hugging Face cache on the
# first extraction; the model cache persists in the worker container until it is replaced.
ARTIFACTS_PATH = os.environ.get("DOCLING_ARTIFACTS_PATH") or None

FIGURE_FORMAT = "PNG"
FIGURE_CONTENT_TYPE = "image/png"
FIGURE_NAME_FORMAT = "p{page_no:04d}-{index}.png"
EPUB_FIGURE_NAME_FORMAT = "s{section_no:04d}-{index}.png"
