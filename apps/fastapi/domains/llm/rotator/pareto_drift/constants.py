# Per-cell ADWIN state. Key = (deployment, dd_process) tuple. Lost on
# restart; the bandit's Redis state survives. ADWIN rebuilds its window
# from fresh observations after restart — typically converges in <100 obs.
_adwin_state: dict[tuple[str, str], Any] = {}
# Track which cells have a pending reset (drift detected but not yet
# reset to benchmark). Caller drains via maybe_reset_cell() or
# drift_sweep().
_pending_resets: set[tuple[str, str]] = set()
_metric_instruments: dict[str, Any] = {}
