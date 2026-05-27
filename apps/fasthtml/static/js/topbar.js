/* DD-NAVBAR-SOTA-2026-05-26 (Wave B1) — running-work status dot.
 *
 * Polls /api/v1/docs-distiller/runs/active every 30s. When ANY active
 * ingestion run is in flight, the matching nav-item gets the
 * .has-running class which surfaces its .nav-status-dot (pulsing
 * sky-blue dot, top-right of the nav pill — see base.css).
 *
 * Today: covers ingestion only (that's the only `*/active` endpoint we
 * have). Synth / planner detection is a TODO follow-up — would either
 * need a new /api/v1/docs-distiller/synth/active endpoint or a
 * client-side aggregator over /recent + per-thread state.
 *
 * Failure mode: silent. Any fetch/JSON error leaves the existing class
 * state untouched, so the dot just doesn't appear; no UI breakage.
 */
(() => {
  const POLL_INTERVAL_MS = 30000;
  // Map backend feature → nav-item data-status-key. Multiple sources
  // can flag the same key (e.g. ingestion + synth both bump
  // "docs-distiller"); we union the per-source results.
  const FEATURE_TO_KEY = {
    ingestion: "docs-distiller",
  };

  const item = (key) =>
    document.querySelector(`.nav-item[data-status-key="${key}"]`);

  async function fetchActiveIngestion() {
    try {
      const res = await fetch("/api/v1/docs-distiller/runs/active", {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data?.active) ? data.active : [];
    } catch (_e) {
      return [];
    }
  }

  function applyRunningSet(runningKeys) {
    document.querySelectorAll(".nav-item[data-status-key]").forEach((el) => {
      const key = el.getAttribute("data-status-key");
      if (runningKeys.has(key)) {
        el.classList.add("has-running");
      } else {
        el.classList.remove("has-running");
      }
    });
  }

  async function tick() {
    const active = await fetchActiveIngestion();
    const running = new Set();
    if (active.length > 0) {
      running.add(FEATURE_TO_KEY.ingestion);
    }
    applyRunningSet(running);
  }

  // Run once on load (defer attribute ensures DOM is ready), then on
  // interval. Use visibilitychange to pause polling when the tab is
  // hidden — avoids background-tab traffic.
  let timer = null;
  function start() {
    if (timer) return;
    tick();
    timer = setInterval(tick, POLL_INTERVAL_MS);
  }
  function stop() {
    if (!timer) return;
    clearInterval(timer);
    timer = null;
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });
  start();
})();
