"""Cross-stage pipeline-state introspection.

The Docs Distiller is a 4-stage pipeline:

  Catalog → Ingestion → Planner → Synth (chapter render = "Study")

Each stage consumes the previous stage's MinIO artifacts. The frontend
needs to know, for a given framework slug, which downstream stages are
currently cached so it can:

  * label cascade-delete confirm dialogs accurately
      ("Wipe Planner — will ALSO erase the cached Synth+Study", etc.)
  * skip cascade calls when a downstream stage has no cache to wipe
  * gate stepper navigation (already done elsewhere)

Rather than letting the frontend round-trip to each stage's own
endpoint, this module exposes a single read that probes MinIO HEAD on
the canonical "this stage has output" markers:

  ingestion: ingestion/{slug}/manifest.json
  planner:   planner/{slug}/plan-latest.json
  synth:     anything under synth/{slug}/   (chapter folders exist)
  study:     at least one synth/{slug}/{cid}/render-latest.json
             (a chapter has reached the render stage)

Returns booleans only — the caller decides what to do.
"""
import logging

from fastapi import APIRouter, HTTPException

from domains.dd.ingestion.storage import get_storage


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{slug}/state")
async def pipeline_state(slug: str) -> dict:
    """Return a flat dict of `{ingestion, planner, synth, study}` booleans
    indicating which pipeline stages have cached artifacts for ``slug``.

    Used by the frontend wipe / delete dialogs to surface accurate
    cascade-impact messaging ("Wipe Planner will also delete the cached
    Synth + Study for this framework").
    """
    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    minio = get_storage()

    async def _exists(key: str) -> bool:
        try:
            return await minio.exists(key)
        except Exception as e:
            logger.info(f"[pipeline-state] exists({key!r}) failed: {e}")
            return False

    async def _has_any(prefix: str) -> bool:
        """True iff at least one object exists under ``prefix``. Uses the
        existing list helper with a fast bail (max 1 key)."""
        try:
            keys = await minio.list(prefix)
        except Exception as e:
            logger.info(f"[pipeline-state] list({prefix!r}) failed: {e}")
            return False
        return bool(keys)

    ingestion = await _exists(f"ingestion/{slug}/manifest.json")
    planner   = await _exists(f"planner/{slug}/plan-latest.json")
    synth     = await _has_any(f"synth/{slug}/")
    # Study = at least one chapter actually rendered (render-latest.json
    # exists under a chapter folder). We do a single list pass and scan
    # the keys — cheaper than per-chapter HEAD probes when the user
    # has many chapters but none rendered yet.
    study = False
    if synth:
        try:
            keys = await minio.list(f"synth/{slug}/")
            study = any(k.endswith("/render-latest.json") for k in keys)
        except Exception as e:
            logger.info(f"[pipeline-state] study probe failed: {e}")
            study = False

    return {
        "slug": slug,
        "ingestion": ingestion,
        "planner":   planner,
        "synth":     synth,
        "study":     study,
    }
