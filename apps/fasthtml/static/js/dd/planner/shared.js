// planner/shared.js — pure helpers shared between planner.js and
// its siblings (graph.js etc.). Mirrors synth/shared.js.

export function _fieldPresent(values, field) {
  return values && Object.prototype.hasOwnProperty.call(values, field);
}

export function _plannerStorageKey(slug) {
  return 'dd:planner:active:' + slug;
}
