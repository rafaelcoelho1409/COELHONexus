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
  // Wave C / Wave F — sticky stuck-detection + smart auto-hide     //
  // -------------------------------------------------------------- //
  const topbarWrap = document.querySelector(".topbar-wrap");
  if (!topbarWrap) return;

  // Sentinel: 1px element in normal flow with -1px margin so it
  // contributes ZERO layout space but is observable. Placed right
  // before .topbar-wrap so its intersection state mirrors whether
  // the bar is currently pinned at the viewport edge.
  const sentinel = document.createElement("div");
  sentinel.setAttribute("aria-hidden", "true");
  sentinel.style.cssText = "height:1px;margin-bottom:-1px;";
  topbarWrap.parentNode.insertBefore(sentinel, topbarWrap);

  const stuckObserver = new IntersectionObserver(
    ([entry]) => {
      topbarWrap.classList.toggle("is-stuck", !entry.isIntersecting);
    },
    { threshold: [0, 1] },
  );
  stuckObserver.observe(sentinel);

  // Wave F / Wave G — smart auto-hide. The header is up to four stacked
  // rows: brand+nav, (feature title), stage tabs, contextual toolbar.
  // We collapse only the rows marked `.topbar-collapsible` (the feature
  // title row on non-DD pages; the contextual toolbar on DD pages) —
  // brand+nav and the stage tabs stay PINNED so identity + stage
  // switching are never lost mid-scroll. The CSS collapses each row via
  // max-height+padding so lower rows slide up cleanly.
  //
  // If there are no collapsible rows (e.g. Home), the toggle is a no-op.
  //
  // Tunables:
  //   SCROLL_DELTA  — ignore micro-scrolls below this (px).
  //   SHOW_BELOW_Y  — always show when within this many px of the
  //                   document top (avoids hiding right after landing).
  const collapsibles = topbarWrap.querySelectorAll(".topbar-collapsible");
  const SCROLL_DELTA = 6;
  const SHOW_BELOW_Y = 80;
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  let lastY = window.scrollY;
  let ticking = false;

  function setHidden(hidden) {
    collapsibles.forEach((el) => el.classList.toggle("is-hidden", hidden));
  }

  function onScroll() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(() => {
      const y = window.scrollY;
      const dy = y - lastY;

      if (motionQuery.matches) {
        // Reduced motion — never hide.
        setHidden(false);
      } else if (Math.abs(dy) >= SCROLL_DELTA) {
        setHidden(dy > 0 && y > SHOW_BELOW_Y);
        lastY = y;
      } else if (y <= SHOW_BELOW_Y) {
        // Near the top regardless — make sure row 2 is visible.
        setHidden(false);
      }

      ticking = false;
    });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
})();
