/* DD-NAVBAR-SOTA topbar client behaviors.
 *
 * Wave B1 — running-work status dot:
 *   Polls /api/v1/docs-distiller/runs/active every 30s. When ANY
 *   active ingestion run is in flight, the matching nav-item gets
 *   .has-running which surfaces a pulsing sky-blue .nav-status-dot.
 *   Today: covers ingestion only. Synth/planner is a TODO.
 *   Failure mode: silent. Any fetch/JSON error leaves the existing
 *   class state untouched, so the dot just doesn't appear.
 *
 * Wave C (2026-05-27) — sticky auto-hide topbar:
 *   1. Stuck detection via IntersectionObserver sentinel. A 1px
 *      element sits in normal flow ABOVE .topbar-wrap; once it
 *      exits the viewport, the bar is pinned → toggle .is-stuck.
 *      Sentinel uses negative margin to avoid any layout shift.
 *   2. Auto-hide on scroll-down via passive scroll listener,
 *      throttled with requestAnimationFrame. Threshold + delta
 *      avoid flicker; near the top of the page the bar always
 *      shows. prefers-reduced-motion disables the hide behavior
 *      entirely (CSS also disables the transition).
 */
(() => {
  // -------------------------------------------------------------- //
  // Wave B1 — running-work status dot                              //
  // -------------------------------------------------------------- //
  const POLL_INTERVAL_MS = 30000;
  // Map backend feature → nav-item data-status-key. Multiple sources
  // can flag the same key (e.g. ingestion + synth both bump
  // "docs-distiller"); we union the per-source results.
  const FEATURE_TO_KEY = {
    ingestion: "docs-distiller",
  };

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

  // -------------------------------------------------------------- //
  // App-shell (2026-05-28) — the header is a FIXED grid row (row 1 of
  // `.shell`); `.page` (row 2) is the scroll region. The document never
  // scrolls, so the old sentinel + smart-auto-hide machinery is gone.
  // We just toggle `.is-stuck` (shadow + divider) once `.page` scrolls,
  // so the header visually detaches from the content beneath it.
  // -------------------------------------------------------------- //
  const topbarWrap = document.querySelector(".topbar-wrap");
  const page = document.querySelector(".page");
  if (!topbarWrap || !page) return;

  let stuckTicking = false;
  const syncStuck = () =>
    topbarWrap.classList.toggle("is-stuck", page.scrollTop > 4);
  page.addEventListener("scroll", () => {
    if (stuckTicking) return;
    stuckTicking = true;
    requestAnimationFrame(() => { stuckTicking = false; syncStuck(); });
  }, { passive: true });
  syncStuck();
})();
