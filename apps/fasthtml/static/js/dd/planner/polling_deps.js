// planner/polling_deps.js — DI dependency registry for polling.js.
//
// polling.js needs renderPlannerCards, markPlannerFailed, cardEl
// (all defined in planner.js after polling functions in the original
// file). A direct import would cycle. Mutated in-place at module init
// via registerPollingDeps from planner.js.

export const deps = {
  renderPlannerCards:       null,
  markPlannerFailed:        null,
  cardEl:                   null,
  // refreshPlannerStartState added 2026-06-06 — was incorrectly imported
  // from './graph.js' (no such export); circular import via './planner.js'
  // or './lifecycle.js' empirically didn't reliably resolve in browsers
  // even though the Node spec says it should. DI via this registry is
  // what the rest of the file already uses for the same shape of
  // problem (renderPlannerCards / markPlannerFailed / cardEl).
  refreshPlannerStartState: null,
};

export function registerPollingDeps(obj) {
  Object.assign(deps, obj);
}
