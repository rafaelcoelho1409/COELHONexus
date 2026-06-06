"""ycs/grader — per-document char cap fed to the grader prompt.

Mirror of deprecated `services/youtube/grader.py:L52`. Caps the
transcript-excerpt portion of the grading prompt so a 10k-token
chunk doesn't blow the model's input budget."""
from __future__ import annotations


PER_DOC_CHAR_CAP = 2000
