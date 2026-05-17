"""
ParetoBandit cell drift detection via river.drift.ADWIN (Phase 2 enhancement #4).

DESIGN (2026-05-14): geometric forgetting in pareto_bandit.update() decays at a
constant rate (γ=0.01), which is good for slow trends but slow to react to
sudden regime shifts (e.g. NIM silently hot-swapping GLM-5.1 weights to a
worse checkpoint). ADWIN ("Adaptive Windowing", Bifet & Gavaldà SDM 2007)
gives an explicit statistical test for distribution shift on the success/fail
stream. When ADWIN raises a drift alarm, we reset the cell's posterior to
the current benchmark composite — explicit re-init faster than waiting for
geometric decay.

Belt + suspenders for non-stationarity: ADWIN catches fast shifts (seconds
to minutes), geometric forgetting handles slow trends (hours to days).

Per-cell ADWIN state lives in module memory (not Redis — ADWIN's internal
window structure isn't trivially JSON-serializable, and losing the window on
restart is acceptable: the bandit's Redis state survives, and ADWIN simply
rebuilds its window from new observations).

Observation feed: `feed_observation(deployment, dd_process, success)` is
called immediately after every pareto_bandit.update() in helpers.py. ADWIN
runs in O(log W) per observation — cheap.

Reset path: when ADWIN.update() returns True (drift detected), we
asynchronously reset the cell:
  1. Compute current benchmark composite for (deployment, dd_process)
  2. Build a fresh CellState from that prior
  3. Write to Redis (replaces stale posterior)
  4. Emit dd.pareto_drift_reset_total metric

OTel metrics:
  dd.pareto_drift_observations_total{dd_process}    Counter — observations fed
  dd.pareto_drift_detected_total{deployment, dd_process}  Counter — drift events
  dd.pareto_drift_reset_total{deployment, dd_process}     Counter — successful resets

Public API:
  feed_observation(deployment, dd_process, success)             → bool (drift detected)
  await maybe_reset_cell(deployment, dd_process, *, redis)      → bool (reset done)
  await drift_sweep(*, redis)                                    → dict (admin scan)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as redis_aio

logger = logging.getLogger(__name__)


# Lazy river import — graceful degrade if not installed.
_river_drift_module = None
_RIVER_AVAILABLE: bool | None = None


def _get_river_drift():
    """Lazy import of river.drift. Returns the module or None."""
    global _river_drift_module, _RIVER_AVAILABLE
    if _RIVER_AVAILABLE is not None:
        return _river_drift_module
    try:
        from river import drift as _drift
        _river_drift_module = _drift
        _RIVER_AVAILABLE = True
        logger.info("[pareto-drift] river.drift available — ADWIN enabled")
    except ImportError:
        _RIVER_AVAILABLE = False
        logger.info(
            "[pareto-drift] river not installed — drift detection disabled "
            "(geometric forgetting still runs in pareto_bandit.update)"
        )
    return _river_drift_module


# Per-cell ADWIN state. Key = (deployment, dd_process) tuple. Lost on
# restart; the bandit's Redis state survives. ADWIN rebuilds its window
# from fresh observations after restart — typically converges in <100 obs.
_adwin_state: dict[tuple[str, str], Any] = {}

# Track which cells have a pending reset (drift detected but not yet
# reset to benchmark). Caller drains via maybe_reset_cell() or
# drift_sweep().
_pending_resets: set[tuple[str, str]] = set()


def feed_observation(
    deployment: str,
    dd_process: str,
    success: bool,
) -> bool:
    """Feed one success/fail observation to the cell's ADWIN detector.

    Returns True if ADWIN detected drift this update — the caller should
    schedule a cell reset via maybe_reset_cell(). Returns False otherwise.

    Cheap (O(log window_size)). Safe to call from the hot path inline.
    """
    drift_module = _get_river_drift()
    if drift_module is None:
        return False
    _record_observation(dd_process)
    key = (deployment, dd_process)
    adwin = _adwin_state.get(key)
    if adwin is None:
        try:
            adwin = drift_module.ADWIN()
            _adwin_state[key] = adwin
        except Exception as e:
            logger.warning(f"[pareto-drift] failed to init ADWIN for {key}: {e}")
            return False
    try:
        # river ≥0.20 modifies the detector in-place; update() may return
        # the detector, a bool, or None depending on version. Authoritative
        # source is `adwin.drift_detected` AFTER the update call.
        adwin.update(1.0 if success else 0.0)
        drifted = bool(getattr(adwin, "drift_detected", False))
    except Exception as e:
        logger.debug(f"[pareto-drift] ADWIN.update raised for {key}: {e}")
        return False

    if drifted:
        _pending_resets.add(key)
        _record_detected(deployment, dd_process)
        logger.warning(
            f"[pareto-drift] drift DETECTED for {deployment}/{dd_process} "
            f"(pending reset)"
        )
    return drifted


async def maybe_reset_cell(
    deployment: str,
    dd_process: str,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    """If the cell has a pending drift-induced reset, perform it.

    Reset = compute current benchmark composite for this deployment+process,
    overwrite the cell's posterior with a fresh CellState seeded from that
    prior. Clears the pending flag on success.

    Returns True if a reset was performed, False otherwise.
    """
    key = (deployment, dd_process)
    if key not in _pending_resets:
        return False
    try:
        from services.llm import pareto_bandit, benchmarks
        canonical = benchmarks.normalize_model_name(deployment)
        scores = await benchmarks.get_benchmarks(canonical, redis=redis)
        weights = benchmarks.STEP_WEIGHTS.get(
            dd_process, benchmarks.STEP_WEIGHTS["dd-all"],
        )
        new_prior = benchmarks.compute_composite_score(scores, weights)
        fresh = pareto_bandit.CellState.fresh(deployment, dd_process, new_prior)
        await pareto_bandit.save_cell_state(fresh, redis=redis)
        _pending_resets.discard(key)
        # Also reset the ADWIN window — it has stale memory of the pre-drift regime.
        _adwin_state.pop(key, None)
        _record_reset(deployment, dd_process)
        logger.warning(
            f"[pareto-drift] RESET {deployment}/{dd_process} → benchmark prior={new_prior:.4f}"
        )
        return True
    except Exception as e:
        logger.warning(
            f"[pareto-drift] reset failed for {key}: {type(e).__name__}: {e}"
        )
        return False


async def drift_sweep(
    *,
    redis: "redis_aio.Redis | None",
) -> dict[str, Any]:
    """Process all pending drift resets in one pass. For admin / Celery beat use.

    Returns {"pending": [..], "reset": [..], "errors": [..]} describing the action.
    """
    pending_snapshot = list(_pending_resets)
    reset: list[str] = []
    errors: list[str] = []
    for deployment, dd_process in pending_snapshot:
        try:
            ok = await maybe_reset_cell(deployment, dd_process, redis=redis)
            if ok:
                reset.append(f"{deployment}/{dd_process}")
        except Exception as e:
            errors.append(f"{deployment}/{dd_process}: {type(e).__name__}")
    return {
        "pending_before": [f"{d}/{p}" for d, p in pending_snapshot],
        "reset": reset,
        "errors": errors,
        "pending_after": [f"{d}/{p}" for d, p in _pending_resets],
        "adwin_cells_tracked": len(_adwin_state),
    }


def get_state_summary() -> dict[str, Any]:
    """Snapshot of in-memory ADWIN state for admin / debugging."""
    return {
        "river_available": bool(_get_river_drift()),
        "adwin_cells_tracked": len(_adwin_state),
        "pending_resets": [
            f"{d}/{p}" for d, p in _pending_resets
        ],
    }


# =============================================================================
# OTel metric helpers
# =============================================================================
_metric_instruments: dict[str, Any] = {}


def _ensure_metrics() -> dict[str, Any]:
    if _metric_instruments:
        return _metric_instruments
    try:
        from services.llm.otel_setup import get_meter
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["obs_counter"] = meter.create_counter(
            name="dd.pareto_drift_observations_total",
            description="Observations fed to ADWIN — labels: dd_process",
        )
        _metric_instruments["detected_counter"] = meter.create_counter(
            name="dd.pareto_drift_detected_total",
            description="Drift events raised by ADWIN — labels: deployment, dd_process",
        )
        _metric_instruments["reset_counter"] = meter.create_counter(
            name="dd.pareto_drift_reset_total",
            description="Cells reset from benchmark prior after drift — labels: deployment, dd_process",
        )
        logger.info(f"[pareto-drift] {len(_metric_instruments)} OTel instruments registered")
    except Exception as e:
        logger.warning(f"[pareto-drift] OTel init failed: {type(e).__name__}: {e}")
    return _metric_instruments


def _record_observation(dd_process: str) -> None:
    inst = _ensure_metrics()
    c = inst.get("obs_counter")
    if c is None:
        return
    try:
        c.add(1, attributes={"dd_process": dd_process})
    except Exception:
        pass


def _record_detected(deployment: str, dd_process: str) -> None:
    inst = _ensure_metrics()
    c = inst.get("detected_counter")
    if c is None:
        return
    try:
        c.add(1, attributes={"deployment": deployment, "dd_process": dd_process})
    except Exception:
        pass


def _record_reset(deployment: str, dd_process: str) -> None:
    inst = _ensure_metrics()
    c = inst.get("reset_counter")
    if c is None:
        return
    try:
        c.add(1, attributes={"deployment": deployment, "dd_process": dd_process})
    except Exception:
        pass
