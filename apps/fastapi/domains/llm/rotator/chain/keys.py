from __future__ import annotations


# "embed" filter never affects the rotator's own embedder (a separate pool
# on the external rotator side). Feeds is_non_chat_model() in domain.py —
# still used by YCS (agents/router.py) to keep embedding/rerank/TTS/vision
# models out of its chat-model ranking.
_NON_CHAT_MARKERS: tuple[str, ...] = (
    "embed", "bge", "e5-", "-e5", "gte-", "rerank", "deplot", "ocr",
    "whisper", "clip", "siglip", "-vit", "vit-", "guard", "reward",
)
